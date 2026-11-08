"""PostgreSQL runtime transactions for tablet check-in and scoped teachers.

This service is deliberately separate from the SQLite candidate management
system. It never creates a schema, provisions credentials, or targets a server
implicitly; the caller supplies an initialized runtime database connection.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from business.security import identifier, new_token, password_hash, token_digest, valid_token, verify_password
from business.store import Problem, uid
from .runtime_faces import FACE_MODEL, FaceCipher, match_face, validate_frames
from .runtime_permissions import CampusPermissions


CHINA_TZ = timezone(timedelta(hours=8))
MIN_UNITS = -1_000_000
MAX_UNITS = 10_000_000


def canonical_digest(action, payload):
    raw = json.dumps({'action': action, 'payload': payload}, ensure_ascii=True,
                     sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class RuntimeStore(CampusPermissions):
    def __init__(self, conninfo, clock=None, *, face_key=None):
        self.conninfo = conninfo
        self.clock = clock or (lambda: int(time.time()))
        self.face_key = face_key if face_key is not None else os.environ.get('FACE_PROFILE_KEY')

    def connect(self):
        return psycopg.connect(self.conninfo, row_factory=dict_row)

    def command(self, token, expected_kind, operation):
        with self.connect() as connection:
            with connection.transaction():
                actor = self.authenticate(connection, token, expected_kind)
                return operation(connection, actor)

    def authenticate(self, db, token, expected_kind=None):
        if not valid_token(token):
            raise Problem(401, 'unauthorized', '请重新登录')
        now = self.clock()
        digest = token_digest(token)
        if token.startswith('wxu_'):
            row = db.execute('''SELECT u.id,u.org_id,u.login,u.display_name,s.id AS session_id,
                    a.role AS staff_role,a.campus_id,cp.name AS staff_campus_name,
                    EXISTS(SELECT 1 FROM organization_staff_access own WHERE own.org_id=u.org_id
                           AND own.role='owner') AS managed_access
                FROM auth_sessions s JOIN users u ON u.org_id=s.org_id AND u.id=s.user_id
                LEFT JOIN organization_staff_access a ON a.org_id=u.org_id AND a.user_id=u.id
                LEFT JOIN campuses cp ON cp.org_id=a.org_id AND cp.id=a.campus_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>%s AND u.active=TRUE''',
                (digest, now)).fetchone()
            if row:
                roles = {r['role'] for r in db.execute(
                    'SELECT role FROM roles WHERE org_id=%s AND user_id=%s', (row['org_id'], row['id']))}
                if row['managed_access']:
                    roles.discard('admin')
                if row['staff_role']:
                    roles.add(row['staff_role'])
                if 'owner' in roles:
                    roles.add('admin')
                return dict(row, kind='user', roles=roles, session_digest=digest)
        else:
            row = db.execute('''SELECT d.id,d.org_id,d.name,c.generation
                FROM device_credentials c JOIN devices d ON d.org_id=c.org_id AND d.id=c.device_id
                WHERE c.token_hash=%s AND c.revoked_at IS NULL AND c.expires_at>%s
                  AND d.active=TRUE AND d.token_reissue_required=FALSE''', (digest, now)).fetchone()
            if row:
                return dict(row, kind='device', roles=set(), credential_digest=digest)
        raise Problem(401, 'unauthorized', '请重新登录')

    @staticmethod
    def _require_kind(actor, kind):
        if actor['kind'] != kind:
            raise Problem(403, kind + '_required', '当前凭据不能执行此操作')

    @staticmethod
    def _lock_command(db, actor, reference):
        # Serialize identical retries even when no api_commands row exists yet.
        key = json.dumps([actor['org_id'], actor['kind'], actor['id'], reference],
                         ensure_ascii=True, separators=(',', ':'))
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))

    @staticmethod
    def _prior(db, actor, reference, digest):
        row = db.execute('''SELECT request_digest,response FROM api_commands
            WHERE org_id=%s AND actor_type=%s AND actor_id=%s AND command_ref=%s''',
            (actor['org_id'], actor['kind'], actor['id'], reference)).fetchone()
        if not row:
            return None
        if row['request_digest'] != digest:
            raise Problem(409, 'idempotency_conflict', '同一操作编号不能用于不同内容')
        return dict(row['response'], duplicate=True)

    @staticmethod
    def _save_command(db, actor, reference, digest, response, now):
        db.execute('''INSERT INTO api_commands
            (org_id,actor_type,actor_id,command_ref,request_digest,response,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''',
            (actor['org_id'], actor['kind'], actor['id'], reference, digest, Jsonb(response), now))

    def _audit(self, db, actor, action, entity_id, detail, now):
        db.execute('''INSERT INTO audit_log
            (org_id,actor_type,actor_id,action,entity_id,detail,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''',
            (actor['org_id'], actor['kind'], actor['id'], action, entity_id, Jsonb(detail), now))

    @staticmethod
    def _lesson_student(db, org_id, lesson_id, student_id):
        return db.execute('''SELECT l.id AS lesson_id,l.class_id,l.starts_at,l.ends_at,l.state,
                ls.student_id,ls.expected,ls.charge_units,
                ar.status,ar.revision AS attendance_revision,
                sb.units AS balance_units,sb.revision AS balance_revision
            FROM lesson_students ls
            JOIN lessons l ON l.org_id=ls.org_id AND l.id=ls.lesson_id
            JOIN attendance_records ar ON ar.org_id=ls.org_id
                AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
            JOIN student_balances sb ON sb.org_id=ls.org_id AND sb.student_id=ls.student_id
            WHERE ls.org_id=%s AND ls.lesson_id=%s AND ls.student_id=%s
            FOR UPDATE OF ar,sb''', (org_id, lesson_id, student_id)).fetchone()

    def _teacher_lesson_allowed(self, db, actor, lesson_id, class_id):
        self._require_kind(actor, 'user')
        if 'teacher' not in actor['roles']:
            raise Problem(403, 'teacher_required', '需要教师身份')
        allowed = db.execute('''SELECT 1 FROM effective_lesson_teachers lt
            LEFT JOIN teacher_classes tc ON tc.org_id=lt.org_id AND tc.user_id=lt.user_id AND tc.class_id=%s
            WHERE lt.org_id=%s AND lt.user_id=%s AND lt.lesson_id=%s
              AND (lt.is_substitute OR tc.class_id IS NOT NULL)''',
            (class_id, actor['org_id'], actor['id'], lesson_id)).fetchone()
        if not allowed:
            raise Problem(403, 'lesson_scope', '只能操作本人授课班级的本节课')

    @staticmethod
    def _require_missing(fact, expected_revision):
        if not fact['expected'] or fact['status'] != 'missing':
            raise Problem(409, 'attendance_conflict', '仅能为应到且未签到、未请假的学生操作')
        if fact['attendance_revision'] != expected_revision:
            raise Problem(409, 'attendance_changed', '签到资料已更新，请刷新后重新核对')

    @staticmethod
    def _event(db, actor, fact, kind, reference, reason, occurred_at, now):
        event_id = uid()
        db.execute('''INSERT INTO attendance_events
            (id,org_id,lesson_id,student_id,kind,event_ref,actor_type,actor_id,reason,occurred_at,recorded_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (event_id, actor['org_id'], fact['lesson_id'], fact['student_id'], kind,
             reference, actor['kind'], actor['id'], reason, occurred_at, now))
        return event_id

    def _debit(self, db, actor, fact, kind, source_ref, previous_revision, now, reason):
        delta = -fact['charge_units']
        units = fact['balance_units'] + delta
        if not MIN_UNITS <= units <= MAX_UNITS:
            raise Problem(409, 'balance_limit', '扣课后余额超出允许范围，请先核对')
        db.execute('''UPDATE student_balances SET units=%s,revision=%s,updated_at=%s
            WHERE org_id=%s AND student_id=%s AND revision=%s''',
            (units, previous_revision + 1, now, actor['org_id'], fact['student_id'], previous_revision))
        db.execute('''INSERT INTO balance_ledger
            (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
             previous_revision,revision,actor_type,actor_id,reason,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (uid(), actor['org_id'], fact['student_id'], kind, source_ref,
             fact['balance_units'], delta, units, previous_revision, previous_revision + 1,
             actor['kind'], actor['id'], reason, now))
        return units, previous_revision + 1

    def login(self, organization, login, password, source):
        now = self.clock()
        buckets = [('ip:' + token_digest(source), 30), ('account:' + token_digest(login), 5)]
        result, failure = None, None
        with self.connect() as connection:
            with connection.transaction():
                db = connection
                db.execute('DELETE FROM runtime_login_limits WHERE window_start<%s AND blocked_until<%s', (now - 86400, now))
                limits = [(bucket, maximum, db.execute('SELECT * FROM runtime_login_limits WHERE bucket=%s', (bucket,)).fetchone()) for bucket, maximum in buckets]
                if any(row and row['blocked_until'] > now for _, _, row in limits):
                    failure = Problem(429, 'login_limited', '尝试过于频繁，请稍后再试')
                else:
                    candidates = db.execute('''SELECT u.*,c.password_hash FROM organizations o
                        JOIN users u ON u.org_id=o.id LEFT JOIN user_credentials c ON c.org_id=u.org_id AND c.user_id=u.id
                        WHERE u.login=%s AND (%s='' OR o.slug=%s) AND u.active=TRUE
                        ORDER BY u.org_id LIMIT 9''', (login, organization, organization)).fetchall()
                    if not candidates or len(candidates) > 8:
                        verify_password(password, None)
                        candidates = []
                    matches = [row for row in candidates if verify_password(password, row['password_hash'])]
                    if len(matches) != 1:
                        for bucket, maximum, row in limits:
                            fresh = not row or now - row['window_start'] >= 300
                            count, start = (1, now) if fresh else (row['failures'] + 1, row['window_start'])
                            blocked = now + 300 if count >= maximum else 0
                            db.execute('''INSERT INTO runtime_login_limits VALUES(%s,%s,%s,%s)
                                ON CONFLICT(bucket) DO UPDATE SET failures=excluded.failures,
                                window_start=excluded.window_start,blocked_until=excluded.blocked_until''', (bucket, count, start, blocked))
                        failure = Problem(401, 'invalid_login', '账号或密码不正确')
                    else:
                        user = matches[0]
                        db.execute('DELETE FROM runtime_login_limits WHERE bucket=%s', (buckets[1][0],))
                        db.execute('DELETE FROM auth_sessions WHERE expires_at<=%s', (now,))
                        db.execute('''DELETE FROM auth_sessions WHERE token_hash IN
                            (SELECT token_hash FROM auth_sessions WHERE org_id=%s AND user_id=%s
                             ORDER BY created_at DESC,token_hash DESC OFFSET 4)''', (user['org_id'], user['id']))
                        token = new_token('user')
                        db.execute('INSERT INTO auth_sessions VALUES(%s,%s,%s,%s,%s,%s,NULL)',
                                   (uid(), user['org_id'], user['id'], token_digest(token), now, now + 8 * 3600))
                        result = {'access_token': token, 'token_type': 'Bearer', 'expires_at': now + 8 * 3600}
        if failure:
            raise failure
        return result

    @staticmethod
    def _require_admin(actor):
        if actor['kind'] != 'user' or 'admin' not in actor['roles']:
            raise Problem(403, 'admin_required', '仅管理员可以执行此操作')

    def admin_students(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT s.id,s.name,s.external_ref,s.active,
                    COALESCE(ec.class_ids,'{}') AS class_ids,
                    COALESCE(ec.class_names,'{}') AS class_names,
                    COALESCE(ec.course_names,'{}') AS course_names,
                    (ec.class_ids)[1] AS class_id,(ec.class_names)[1] AS class_name,
                    CASE WHEN shared.other_campus THEN NULL ELSE sb.units END AS balance_units,
                    CASE WHEN shared.other_campus THEN NULL ELSE sb.revision END AS balance_revision,
                    NOT shared.other_campus AS editable,
                    COALESCE(sv.version,0) AS version,fp.model_version AS face_model_version,
                    (fp.id IS NOT NULL) AS face_registered
                FROM students s
                LEFT JOIN LATERAL (
                    SELECT array_agg(e.class_id ORDER BY c.name,c.id) AS class_ids,
                        array_agg(c.name ORDER BY c.name,c.id) AS class_names,
                        array_agg(co.name ORDER BY c.name,c.id) AS course_names
                    FROM student_class_enrollments e
                    JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
                    JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                    WHERE e.org_id=s.org_id AND e.student_id=s.id AND e.left_at IS NULL
                      AND (%s::text IS NULL OR c.campus_id=%s)
                 ) ec ON TRUE
                CROSS JOIN LATERAL (SELECT %s::text IS NOT NULL AND EXISTS(
                    SELECT 1 FROM student_class_enrollments e JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
                    WHERE e.org_id=s.org_id AND e.student_id=s.id AND e.left_at IS NULL
                    AND c.campus_id IS DISTINCT FROM %s
                ) AS other_campus) shared
                JOIN student_balances sb ON sb.org_id=s.org_id AND sb.student_id=s.id
                LEFT JOIN student_versions sv ON sv.org_id=s.org_id AND sv.student_id=s.id
                LEFT JOIN face_profiles fp ON fp.org_id=s.org_id AND fp.student_id=s.id AND fp.active=TRUE
                WHERE s.org_id=%s AND (%s::text IS NULL OR ec.class_ids IS NOT NULL)
                ORDER BY s.active DESC,s.name,s.id''',
                (actor.get('campus_id'), actor.get('campus_id'), actor.get('campus_id'),
                 actor.get('campus_id'), actor['org_id'], actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def _future_roster_candidates(self, db, actor, student_id, lock=False):
        self._require_admin(actor)
        student_query = '''SELECT id,name,external_ref,active FROM students
            WHERE org_id=%s AND id=%s'''
        if lock:
            student_query += ' FOR UPDATE'
        student = db.execute(student_query, (actor['org_id'], student_id)).fetchone()
        if not student:
            raise Problem(404, 'student_missing', '未找到学生')
        if not student['active']:
            raise Problem(409, 'student_inactive', '学生已停用，请先核对学籍状态')
        enrollments = db.execute('''SELECT e.class_id,c.name AS class_name
            FROM student_class_enrollments e JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
            WHERE e.org_id=%s AND e.student_id=%s AND e.left_at IS NULL ORDER BY c.name,c.id''',
            (actor['org_id'], student_id)).fetchall()
        student.update(class_ids=[row['class_id'] for row in enrollments],
                       class_names=[row['class_name'] for row in enrollments])
        now = self.clock()
        lesson_query = '''SELECT l.id,l.class_id,l.starts_at,l.ends_at,c.name AS class_name,
                cr.charge_units AS default_charge_units,cr.revision AS charge_rule_revision
            FROM student_class_enrollments e
            JOIN lessons l ON l.org_id=e.org_id AND l.class_id=e.class_id
            JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
            LEFT JOIN lesson_charge_rules cr ON cr.org_id=l.org_id AND cr.lesson_id=l.id
            WHERE e.org_id=%s AND e.student_id=%s AND e.left_at IS NULL
              AND l.state='scheduled' AND l.starts_at>%s
            ORDER BY l.starts_at,l.id'''
        if lock:
            lesson_query += ' FOR UPDATE OF l'
        lessons = db.execute(lesson_query, (actor['org_id'], student_id, now)).fetchall()
        ready, skipped, already = [], [], []
        for lesson in lessons:
            roster = db.execute('''SELECT student_id,charge_units FROM lesson_students
                WHERE org_id=%s AND lesson_id=%s ORDER BY student_id''',
                (actor['org_id'], lesson['id'])).fetchall()
            existing = next((row for row in roster if row['student_id'] == student_id), None)
            base = {'lesson_id': lesson['id'], 'class_id': lesson['class_id'],
                    'class_name': lesson['class_name'], 'starts_at': lesson['starts_at'],
                    'ends_at': lesson['ends_at'],
                    'charge_rule_revision': lesson['charge_rule_revision'] or 0}
            if existing:
                already.append(dict(base, charge_units=existing['charge_units']))
                continue
            if not roster:
                if lesson['default_charge_units'] is None:
                    skipped.append(dict(base, reason='no_charge_rule', observed_charge_units=[]))
                else:
                    ready.append(dict(base, charge_units=lesson['default_charge_units']))
                continue
            charge_units = sorted({row['charge_units'] for row in roster})
            if len(charge_units) != 1:
                skipped.append(dict(base, reason='mixed_charge_rule',
                                    observed_charge_units=charge_units))
                continue
            ready.append(dict(base, charge_units=charge_units[0]))
        return {'student': dict(student), 'server_time': now,
                'ready_lessons': ready, 'skipped_lessons': skipped,
                'already_present_lessons': already,
                'ready_count': len(ready), 'skipped_count': len(skipped),
                'already_present_count': len(already)}

    def _remove_pristine_future_rosters(self, db, org_id, student_id, class_id, now):
        rows = db.execute('''SELECT l.id,ar.status,ar.revision,ar.last_event_id
            FROM lessons l
            JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
            JOIN attendance_records ar ON ar.org_id=ls.org_id
                AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
            WHERE l.org_id=%s AND l.class_id=%s AND ls.student_id=%s
              AND l.state='scheduled' AND l.starts_at>%s
            ORDER BY l.starts_at,l.id FOR UPDATE OF l,ls,ar''',
            (org_id, class_id, student_id, now)).fetchall()
        removed, retained = [], []
        for row in rows:
            if row['status'] != 'missing' or row['revision'] != 0 or row['last_event_id']:
                retained.append(row['id'])
                continue
            db.execute('''DELETE FROM attendance_records
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s''',
                (org_id, row['id'], student_id))
            db.execute('''DELETE FROM lesson_students
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s''',
                (org_id, row['id'], student_id))
            removed.append(row['id'])
        return {'removed_lesson_ids': removed, 'retained_processed_lesson_ids': retained}

    def admin_set_lesson_charge_rule(self, token, lesson_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_set_lesson_charge_rule',
                                  {'lesson_id': lesson_id, **data})
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            lesson = db.execute('''SELECT id,class_id,starts_at,state FROM lessons
                WHERE org_id=%s AND id=%s FOR UPDATE''',
                (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到课次')
            now = self.clock()
            if lesson['state'] != 'scheduled' or lesson['starts_at'] <= now:
                raise Problem(409, 'lesson_not_editable', '只能设置尚未开始且未取消课次的扣课规则')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if not 0 < data['charge_units'] <= MAX_UNITS or data['expected_revision'] < 0:
                raise Problem(422, 'invalid_charge_rule', '扣课单位或规则版本不正确')
            roster = db.execute('''SELECT student_id,charge_units FROM lesson_students
                WHERE org_id=%s AND lesson_id=%s ORDER BY student_id FOR UPDATE''',
                (actor['org_id'], lesson_id)).fetchall()
            attendance = db.execute('''SELECT student_id,status,revision,last_event_id
                FROM attendance_records WHERE org_id=%s AND lesson_id=%s
                ORDER BY student_id FOR UPDATE''', (actor['org_id'], lesson_id)).fetchall()
            if any(row['status'] != 'missing' or row['revision'] != 0 or row['last_event_id']
                   for row in attendance):
                raise Problem(409, 'attendance_conflict', '已有学生签到资料经过处理，不能修改扣课规则')
            observed = sorted({row['charge_units'] for row in roster})
            rule = db.execute('''SELECT charge_units,revision FROM lesson_charge_rules
                WHERE org_id=%s AND lesson_id=%s FOR UPDATE''',
                (actor['org_id'], lesson_id)).fetchone()
            current_revision = rule['revision'] if rule else 0
            if current_revision != data['expected_revision']:
                raise Problem(409, 'charge_rule_changed', '扣课规则已被其他操作更新，请刷新后重新核对')
            if len(observed) == 1:
                raise Problem(409, 'charge_rule_consistent', '现有名单扣课单位已经一致，无需统一修改')
            revision = current_revision + 1
            db.execute('''INSERT INTO lesson_charge_rules
                (org_id,lesson_id,charge_units,revision,updated_at) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(org_id,lesson_id) DO UPDATE SET charge_units=EXCLUDED.charge_units,
                revision=EXCLUDED.revision,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], lesson_id, data['charge_units'], revision, now))
            updated = len(roster) if len(observed) > 1 else 0
            if updated:
                db.execute('''UPDATE lesson_students SET charge_units=%s
                    WHERE org_id=%s AND lesson_id=%s''',
                    (data['charge_units'], actor['org_id'], lesson_id))
            response = {'accepted': True, 'duplicate': False, 'lesson_id': lesson_id,
                        'charge_units': data['charge_units'],
                        'previous_charge_units': observed,
                        'previous_default_charge_units': rule['charge_units'] if rule else None,
                        'previous_revision': current_revision, 'charge_rule_revision': revision,
                        'roster_students_updated': updated, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_lesson_charge_rule_changed', lesson_id,
                        {'previous_charge_units': observed,
                         'previous_default_charge_units': rule['charge_units'] if rule else None,
                         'charge_units': data['charge_units'], 'previous_revision': current_revision,
                         'charge_rule_revision': revision, 'roster_students_updated': updated,
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_future_lessons(self, token, student_id):
        def run(db, actor):
            return self._future_roster_candidates(db, actor, student_id)
        return self.command(token, 'user', run)

    def admin_sync_future_lessons(self, token, student_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_sync_future_lessons',
                                  {'student_id': student_id, 'command_ref': reference})
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            # Recheck current student state even when returning a stored receipt.
            preview = self._future_roster_candidates(db, actor, student_id, lock=True)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = preview['server_time']
            synced = preview['ready_lessons']
            for lesson in synced:
                db.execute('''INSERT INTO lesson_students
                    (org_id,class_id,lesson_id,student_id,expected,charge_units)
                    VALUES(%s,%s,%s,%s,TRUE,%s)''',
                    (actor['org_id'], lesson['class_id'], lesson['lesson_id'],
                     student_id, lesson['charge_units']))
                db.execute('''INSERT INTO attendance_records
                    (org_id,lesson_id,student_id,status,revision,last_event_id,updated_at)
                    VALUES(%s,%s,%s,'missing',0,NULL,%s)''',
                    (actor['org_id'], lesson['lesson_id'], student_id, now))
            response = {'accepted': True, 'duplicate': False, 'student_id': student_id,
                        'student': preview['student'], 'server_time': now,
                        'synced_lessons': synced, 'synced_count': len(synced),
                        'skipped_lessons': preview['skipped_lessons'],
                        'skipped_count': preview['skipped_count'],
                        'already_present_lessons': preview['already_present_lessons'],
                        'already_present_count': preview['already_present_count'],
                        'lesson_rosters_changed': bool(synced), 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_student_future_rosters_synced', student_id,
                        {'class_ids': preview['student']['class_ids'],
                         'synced_lesson_ids': [row['lesson_id'] for row in synced],
                         'synced_count': len(synced),
                         'skipped_lessons': preview['skipped_lessons'],
                         'already_present_lesson_ids': [
                             row['lesson_id'] for row in preview['already_present_lessons']],
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_create_student(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_student', data)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            self._scope_record(db, actor, 'classes', data['class_id'])
            class_row = db.execute('SELECT id,name FROM classes WHERE org_id=%s AND id=%s',
                                   (actor['org_id'], data['class_id'])).fetchone()
            if not class_row:
                raise Problem(404, 'class_missing', '未找到入班班级')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if not data['name'].strip() or not data['external_ref'].strip():
                raise Problem(422, 'invalid_student', '请填写学生姓名和学号')
            if not data['source_ref'].strip():
                raise Problem(422, 'invalid_source_ref', '请填写业务来源编号')
            if db.execute('SELECT 1 FROM students WHERE org_id=%s AND external_ref=%s',
                          (actor['org_id'], data['external_ref'])).fetchone():
                raise Problem(409, 'external_ref_conflict', '该学号已存在，请核对后再登记')
            if db.execute('SELECT 1 FROM enrollment_records WHERE org_id=%s AND source_ref=%s',
                          (actor['org_id'], data['source_ref'])).fetchone():
                raise Problem(409, 'source_ref_conflict', '该业务来源编号已登记，请核对后更换')
            now = self.clock()
            if data['occurred_at'] > now + 120:
                raise Problem(409, 'enrollment_clock', '招新时间超前，请核对业务发生时间')
            student_id, enrollment_id, ledger_id = uid(), uid(), uid()
            db.execute('''INSERT INTO students(id,org_id,name,external_ref,active)
                VALUES(%s,%s,%s,%s,TRUE)''',
                (student_id, actor['org_id'], data['name'], data['external_ref']))
            db.execute('''INSERT INTO student_class_enrollments
                (id,org_id,student_id,class_id,joined_at,left_at,created_at)
                VALUES(%s,%s,%s,%s,%s,NULL,%s)''',
                (uid(), actor['org_id'], student_id, data['class_id'], data['occurred_at'], now))
            db.execute('''INSERT INTO opening_balances(org_id,student_id,units,source,review_ref)
                VALUES(%s,%s,%s,'manual_review',%s)''',
                (actor['org_id'], student_id, data['opening_units'], data['source_ref']))
            db.execute('INSERT INTO student_balances VALUES(%s,%s,%s,1,%s)',
                       (actor['org_id'], student_id, data['opening_units'], now))
            db.execute('''INSERT INTO balance_ledger
                (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
                 previous_revision,revision,actor_type,actor_id,reason,created_at)
                VALUES(%s,%s,%s,'opening',%s,0,%s,%s,0,1,'user',%s,%s,%s)''',
                (ledger_id, actor['org_id'], student_id, enrollment_id, data['opening_units'],
                 data['opening_units'], actor['id'], data['reason'], now))
            db.execute('''INSERT INTO enrollment_records
                (id,org_id,student_id,class_id,occurred_at,source_ref,actor_id,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (enrollment_id, actor['org_id'], student_id, data['class_id'],
                 data['occurred_at'], data['source_ref'], actor['id'], now))
            response = {'accepted': True, 'duplicate': False, 'student_id': student_id,
                        'enrollment_id': enrollment_id, 'ledger_id': ledger_id,
                        'class_id': data['class_id'], 'class_name': class_row['name'],
                        'name': data['name'], 'external_ref': data['external_ref'],
                        'opening_units': data['opening_units'],
                        'balance_units': data['opening_units'], 'balance_revision': 1,
                        'occurred_at': data['occurred_at'], 'source_ref': data['source_ref'],
                        'lesson_rosters_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_student_enrolled', enrollment_id,
                        {'student_id': student_id, 'class_id': data['class_id'],
                         'name': data['name'], 'external_ref': data['external_ref'],
                         'opening_units': data['opening_units'], 'occurred_at': data['occurred_at'],
                         'source_ref': data['source_ref'], 'reason': data['reason'],
                         'lesson_rosters_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_update_student(self, token, student_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_update_student', {'student_id': student_id, **data})
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            student = db.execute('''SELECT id,name,external_ref,active FROM students
                WHERE org_id=%s AND id=%s FOR UPDATE''',
                (actor['org_id'], student_id)).fetchone()
            if not student:
                raise Problem(404, 'student_missing', '未找到学生')
            class_ids = data['class_ids']
            if not class_ids or len(set(class_ids)) != len(class_ids):
                raise Problem(422, 'invalid_classes', '请至少选择一个班级，且不能重复')
            target_classes = db.execute('''SELECT id,name FROM classes
                WHERE org_id=%s AND id=ANY(%s) ORDER BY name,id FOR UPDATE''',
                (actor['org_id'], class_ids)).fetchall()
            if len(target_classes) != len(class_ids):
                raise Problem(404, 'class_missing', '未找到目标班级')
            self._scope_student_write(db, actor, student_id)
            for class_id in class_ids:
                self._scope_record(db, actor, 'classes', class_id)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            name, external_ref = data['name'].strip(), data['external_ref'].strip()
            if not name or not external_ref or not data['reason'].strip():
                raise Problem(422, 'invalid_student', '请完整填写学生资料、班级、学籍状态和操作原因')
            version_row = db.execute('''SELECT version FROM student_versions
                WHERE org_id=%s AND student_id=%s FOR UPDATE''',
                (actor['org_id'], student_id)).fetchone()
            current_version = version_row['version'] if version_row else 0
            if current_version != data['expected_version']:
                raise Problem(409, 'student_changed', '学生资料已被其他操作更新，请刷新后重新核对')
            if db.execute('''SELECT 1 FROM students WHERE org_id=%s AND external_ref=%s AND id<>%s''',
                          (actor['org_id'], external_ref, student_id)).fetchone():
                raise Problem(409, 'external_ref_conflict', '该学号已属于其他学生')
            current = db.execute('''SELECT class_id FROM student_class_enrollments
                WHERE org_id=%s AND student_id=%s AND left_at IS NULL ORDER BY class_id FOR UPDATE''',
                (actor['org_id'], student_id)).fetchall()
            previous_ids = [row['class_id'] for row in current]
            target_ids = sorted(class_ids)
            removed_ids = sorted(set(previous_ids) - set(target_ids))
            added_ids = sorted(set(target_ids) - set(previous_ids))
            deactivated = student['active'] and not data['active']
            reactivated = not student['active'] and data['active']
            if deactivated:
                removed_ids = previous_ids
                added_ids = []
            removed = {'removed_lesson_ids': [], 'retained_processed_lesson_ids': []}
            now = self.clock()
            for removed_id in removed_ids:
                result = self._remove_pristine_future_rosters(
                    db, actor['org_id'], student_id, removed_id, now)
                removed['removed_lesson_ids'].extend(result['removed_lesson_ids'])
                removed['retained_processed_lesson_ids'].extend(result['retained_processed_lesson_ids'])
                db.execute('''UPDATE student_class_enrollments SET left_at=%s
                    WHERE org_id=%s AND student_id=%s AND class_id=%s AND left_at IS NULL''',
                    (now, actor['org_id'], student_id, removed_id))
            for added_id in added_ids:
                db.execute('''INSERT INTO student_class_enrollments
                    (id,org_id,student_id,class_id,joined_at,left_at,created_at)
                    VALUES(%s,%s,%s,%s,%s,NULL,%s)''',
                    (uid(), actor['org_id'], student_id, added_id, now, now))
            db.execute('''UPDATE students SET name=%s,external_ref=%s,active=%s
                WHERE org_id=%s AND id=%s''',
                (name, external_ref, data['active'], actor['org_id'], student_id))
            version = current_version + 1
            db.execute('''INSERT INTO student_versions(org_id,student_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,student_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], student_id, version, now))
            added = {'ready_lessons': [], 'skipped_lessons': [], 'already_present_lessons': []}
            if data['active'] and (added_ids or reactivated):
                candidates = self._future_roster_candidates(db, actor, student_id, lock=True)
                for lesson in candidates['ready_lessons']:
                    db.execute('''INSERT INTO lesson_students
                        (org_id,class_id,lesson_id,student_id,expected,charge_units)
                        VALUES(%s,%s,%s,%s,TRUE,%s)''',
                        (actor['org_id'], lesson['class_id'], lesson['lesson_id'],
                         student_id, lesson['charge_units']))
                    db.execute('''INSERT INTO attendance_records
                        (org_id,lesson_id,student_id,status,revision,last_event_id,updated_at)
                        VALUES(%s,%s,%s,'missing',0,NULL,%s)''',
                        (actor['org_id'], lesson['lesson_id'], student_id, now))
                added = candidates
            active_ids = target_ids if data['active'] else []
            response = {'accepted': True, 'duplicate': False, 'student_id': student_id,
                        'name': name, 'external_ref': external_ref, 'active': data['active'],
                        'previous_class_ids': previous_ids, 'class_ids': active_ids,
                        'class_names': [row['name'] for row in target_classes] if data['active'] else [],
                        'class_id': active_ids[0] if active_ids else None,
                        'previous_active': student['active'],
                        'previous_version': current_version, 'version': version,
                        'removed_future_lesson_ids': removed['removed_lesson_ids'],
                        'removed_future_count': len(removed['removed_lesson_ids']),
                        'retained_processed_lesson_ids': removed['retained_processed_lesson_ids'],
                        'retained_processed_count': len(removed['retained_processed_lesson_ids']),
                        'added_future_lessons': added['ready_lessons'],
                        'added_future_count': len(added['ready_lessons']),
                        'skipped_future_lessons': added['skipped_lessons'],
                        'skipped_future_count': len(added['skipped_lessons']),
                        'already_present_count': len(added['already_present_lessons']),
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_student_updated', student_id,
                        {'previous': dict(student), 'name': name, 'external_ref': external_ref,
                         'active': data['active'], 'previous_class_ids': previous_ids,
                         'class_ids': active_ids, 'previous_version': current_version, 'version': version,
                         'reason': data['reason'],
                         'removed_future_lesson_ids': removed['removed_lesson_ids'],
                         'retained_processed_lesson_ids': removed['retained_processed_lesson_ids'],
                         'added_future_lesson_ids': [row['lesson_id'] for row in added['ready_lessons']],
                         'skipped_future_count': len(added['skipped_lessons']),
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_renewal(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_renewal', data)
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            student = db.execute('''SELECT s.name,s.active,sb.units,sb.revision
                FROM students s
                JOIN student_balances sb ON sb.org_id=s.org_id AND sb.student_id=s.id
                WHERE s.org_id=%s AND s.id=%s FOR UPDATE OF sb''',
                (actor['org_id'], data['student_id'])).fetchone()
            if not student:
                raise Problem(404, 'student_missing', '未找到学生或课时余额')
            if not student['active']:
                raise Problem(409, 'student_inactive', '学生已停用，请先核对学籍状态')
            course = db.execute('''SELECT co.id,co.name,co.unit_price_cents AS current_unit_price_cents
                FROM courses co WHERE co.org_id=%s AND co.id=%s AND co.active=TRUE
                  AND EXISTS(SELECT 1 FROM student_class_enrollments e
                    JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
                    WHERE e.org_id=%s AND e.student_id=%s AND e.left_at IS NULL AND c.course_id=co.id)''',
                (actor['org_id'], data['course_id'], actor['org_id'], data['student_id'])).fetchone()
            if not course:
                raise Problem(409, 'course_scope', '学生未报读该课程，或课程已停用')
            # Permission and current student state are rechecked even for retries.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if not data['source_ref'].strip():
                raise Problem(422, 'invalid_source_ref', '请填写业务来源编号')
            if (type(data['amount_cents']) is not int or not 0 <= data['amount_cents'] <= 100000000 or
                    type(data['unit_price_cents']) is not int or not 0 <= data['unit_price_cents'] <= 100000000):
                raise Problem(422, 'invalid_renewal_price', '实收金额和成交单价必须是非负整数分')
            if db.execute('SELECT 1 FROM renewal_records WHERE org_id=%s AND source_ref=%s',
                          (actor['org_id'], data['source_ref'])).fetchone():
                raise Problem(409, 'source_ref_conflict', '该业务来源编号已登记，请核对后更换')
            now = self.clock()
            if data['occurred_at'] > now + 120:
                raise Problem(409, 'renewal_clock', '续费时间超前，请核对业务发生时间')
            units = student['units'] + data['units_added']
            if not MIN_UNITS <= units <= MAX_UNITS:
                raise Problem(409, 'balance_limit', '续费后余额超出允许范围，请核对课时')
            renewal_id, ledger_id = uid(), uid()
            revision = student['revision'] + 1
            db.execute('''INSERT INTO renewal_records
                (id,org_id,student_id,course_id,units_added,amount_cents,unit_price_cents,
                 occurred_at,source_ref,actor_id,reason,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (renewal_id, actor['org_id'], data['student_id'], data['course_id'], data['units_added'],
                 data['amount_cents'], data['unit_price_cents'], data['occurred_at'],
                 data['source_ref'], actor['id'], data['reason'], now))
            db.execute('''UPDATE student_balances SET units=%s,revision=%s,updated_at=%s
                WHERE org_id=%s AND student_id=%s AND revision=%s''',
                (units, revision, now, actor['org_id'], data['student_id'], student['revision']))
            db.execute('''INSERT INTO balance_ledger
                (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
                 previous_revision,revision,actor_type,actor_id,reason,created_at)
                VALUES(%s,%s,%s,'renewal',%s,%s,%s,%s,%s,%s,'user',%s,%s,%s)''',
                (ledger_id, actor['org_id'], data['student_id'], renewal_id, student['units'],
                 data['units_added'], units, student['revision'], revision, actor['id'],
                 data['reason'], now))
            bindings = db.execute('''SELECT id FROM parent_student_bindings
                WHERE org_id=%s AND student_id=%s AND unbound_at IS NULL ORDER BY id''',
                (actor['org_id'], data['student_id'])).fetchall()
            for binding in bindings:
                payload = {'student_name': student['name'], 'course_id': course['id'],
                           'course_name': course['name'], 'units_added': data['units_added'],
                           'amount_cents': data['amount_cents'], 'balance_units': units,
                           'occurred_at': data['occurred_at'], 'source_ref': data['source_ref']}
                db.execute('''INSERT INTO notification_outbox
                    (id,org_id,binding_id,student_id,kind,source_event_id,payload,status,
                     attempts,next_attempt_at,created_at)
                    VALUES(%s,%s,%s,%s,'renewal',%s,%s,'pending',0,%s,%s)''',
                    (uid(), actor['org_id'], binding['id'], data['student_id'], renewal_id,
                     Jsonb(payload), now, now))
            response = {'accepted': True, 'duplicate': False, 'renewal_id': renewal_id,
                        'ledger_id': ledger_id, 'student_id': data['student_id'],
                        'course_id': course['id'], 'course_name': course['name'],
                        'units_added': data['units_added'], 'amount_cents': data['amount_cents'],
                        'unit_price_cents': data['unit_price_cents'],
                        'previous_balance_units': student['units'], 'balance_units': units,
                        'balance_revision': revision, 'occurred_at': data['occurred_at'],
                        'source_ref': data['source_ref'], 'notification_tasks': len(bindings),
                        'notification_status': 'pending' if bindings else 'no_active_bindings',
                        'wechat_sending_enabled': False, 'audited': True}
            self._audit(db, actor, 'admin_renewal_recorded', renewal_id,
                        {'student_id': data['student_id'], 'course_id': course['id'],
                         'units_added': data['units_added'], 'amount_cents': data['amount_cents'],
                         'unit_price_cents': data['unit_price_cents'],
                         'previous_balance_units': student['units'], 'balance_units': units,
                         'occurred_at': data['occurred_at'], 'source_ref': data['source_ref'],
                         'reason': data['reason'], 'notification_tasks': len(bindings)}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    @staticmethod
    def _course_values(data):
        name = data['name'].strip()
        content = data['content'].strip()
        price = data['unit_price_cents']
        if not name or len(name) > 80 or not content or len(content) > 2000:
            raise Problem(422, 'invalid_course', '请填写课程名称、课程内容，并控制在允许长度内')
        if type(price) is not int or not 0 <= price <= 100000000:
            raise Problem(422, 'invalid_course_price', '课程单价必须是非负整数分，且不超过 1000000 元')
        if not data['reason'].strip():
            raise Problem(422, 'invalid_course_reason', '请填写操作原因')
        return name, content, price

    @staticmethod
    def _lock_course_catalog(db, org_id):
        # courses has no version column; serialize catalog writes without adding a table.
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('course-catalog:' + org_id,))

    def admin_courses(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT co.id,co.name,co.content,co.unit_price_cents,co.active,co.created_at,
                    fee.rate_cents AS teaching_fee_cents,COALESCE(fee.version,0) AS teaching_fee_version,
                    (SELECT count(*) FROM classes c WHERE c.org_id=co.org_id AND c.course_id=co.id AND (%s::text IS NULL OR c.campus_id=%s)) AS class_count
                FROM courses co LEFT JOIN LATERAL (
                    SELECT rate_cents,version FROM course_teaching_fee_rates
                    WHERE org_id=co.org_id AND course_id=co.id ORDER BY version DESC LIMIT 1
                ) fee ON TRUE WHERE co.org_id=%s
                ORDER BY co.active DESC,co.name,co.id''', (actor.get('campus_id'), actor.get('campus_id'), actor['org_id'])).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_set_course_teaching_fee(self, token, course_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_set_course_teaching_fee', dict(data, course_id=course_id))
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            result = self._write_course_teaching_fee(db, actor, course_id, data, now)
            self._save_command(db, actor, reference, digest, result, now)
            return result
        return self.command(token, 'user', run)

    @staticmethod
    def _course_fee_proposal_digest(course_id, data):
        return canonical_digest('course_fee_proposal', dict(data, course_id=course_id))

    def _write_course_teaching_fee(self, db, actor, course_id, data, now):
        org_id = actor['org_id']
        if not db.execute('SELECT 1 FROM courses WHERE org_id=%s AND id=%s FOR UPDATE',
                          (org_id, course_id)).fetchone():
            raise Problem(404, 'course_missing', '未找到课程')
        previous = db.execute('''SELECT rate_cents,version FROM course_teaching_fee_rates
            WHERE org_id=%s AND course_id=%s ORDER BY version DESC LIMIT 1''',
            (org_id, course_id)).fetchone()
        version = previous['version'] if previous else 0
        if version != data['expected_version']:
            raise Problem(409, 'course_fee_changed', '课时费规则已更新，请刷新后重试')
        rate = data['rate_cents']
        effective_at = now + 1
        db.execute('''INSERT INTO course_teaching_fee_rates(org_id,course_id,version,effective_at,rate_cents)
            VALUES(%s,%s,%s,%s,%s)''', (org_id, course_id, version + 1, effective_at, rate))
        result = {'course_id': course_id, 'rate_cents': rate, 'version': version + 1,
                  'effective_at': effective_at, 'duplicate': False}
        self._audit(db, actor, 'course_teaching_fee_changed', course_id,
                    {**result, 'previous_rate_cents': previous['rate_cents'] if previous else None,
                     'reason': data['reason']}, now)
        return result

    def admin_create_course(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_course', data)
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            name, content, price = self._course_values(data)
            self._lock_course_catalog(db, actor['org_id'])
            if db.execute('SELECT 1 FROM courses WHERE org_id=%s AND name=%s',
                          (actor['org_id'], name)).fetchone():
                raise Problem(409, 'course_name_conflict', '该课程名称已存在')
            now, course_id = self.clock(), uid()
            db.execute('''INSERT INTO courses(id,org_id,name,content,unit_price_cents,active,created_at)
                VALUES(%s,%s,%s,%s,%s,TRUE,%s)''',
                       (course_id, actor['org_id'], name, content, price, now))
            response = {'accepted': True, 'duplicate': False, 'course_id': course_id,
                        'name': name, 'content': content, 'unit_price_cents': price,
                        'active': True, 'class_count': 0, 'balance_changed': False,
                        'history_changed': False, 'historical_renewals_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_course_created', course_id,
                        {'name': name, 'content': content, 'unit_price_cents': price,
                         'active': True, 'reason': data['reason'], 'balance_changed': False,
                         'history_changed': False, 'historical_renewals_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_update_course(self, token, course_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_update_course', {'course_id': course_id, **data})
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            name, content, price = self._course_values(data)
            if type(data['active']) is not bool:
                raise Problem(422, 'invalid_course_status', '课程状态必须是启用或停用')
            self._lock_course_catalog(db, actor['org_id'])
            course = db.execute('''SELECT id,name,content,unit_price_cents,active FROM courses
                WHERE org_id=%s AND id=%s FOR UPDATE''', (actor['org_id'], course_id)).fetchone()
            if not course:
                raise Problem(404, 'course_missing', '未找到课程')
            if db.execute('SELECT 1 FROM courses WHERE org_id=%s AND name=%s AND id<>%s',
                          (actor['org_id'], name, course_id)).fetchone():
                raise Problem(409, 'course_name_conflict', '该课程名称已存在')
            now = self.clock()
            db.execute('''UPDATE courses SET name=%s,content=%s,unit_price_cents=%s,active=%s
                WHERE org_id=%s AND id=%s''',
                       (name, content, price, data['active'], actor['org_id'], course_id))
            response = {'accepted': True, 'duplicate': False, 'course_id': course_id,
                        'previous_name': course['name'], 'previous_content': course['content'],
                        'previous_unit_price_cents': course['unit_price_cents'],
                        'previous_active': course['active'], 'name': name, 'content': content,
                        'unit_price_cents': price, 'active': data['active'],
                        'balance_changed': False, 'history_changed': False,
                        'historical_renewals_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_course_updated', course_id,
                        {'previous': dict(course), 'name': name, 'content': content,
                         'unit_price_cents': price, 'active': data['active'],
                         'reason': data['reason'], 'balance_changed': False,
                         'history_changed': False, 'historical_renewals_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_classes(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT c.id,c.name,c.course_id,c.campus_id,co.name AS course_name,
                    co.content AS course_content,co.unit_price_cents,co.active AS course_active,
                    COALESCE(cv.version,0) AS version,
                    (SELECT count(*) FROM student_class_enrollments e
                        WHERE e.org_id=c.org_id AND e.class_id=c.id AND e.left_at IS NULL) AS student_count,
                    (SELECT count(*) FROM student_class_enrollments e JOIN students s
                        ON s.org_id=e.org_id AND s.id=e.student_id
                        WHERE e.org_id=c.org_id AND e.class_id=c.id AND e.left_at IS NULL AND s.active=TRUE) AS active_student_count,
                    COALESCE((SELECT array_agg(tc.user_id ORDER BY tc.user_id) FROM teacher_classes tc
                        WHERE tc.org_id=c.org_id AND tc.class_id=c.id),'{}') AS teacher_ids
                FROM classes c JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN class_versions cv ON cv.org_id=c.org_id AND cv.class_id=c.id
                WHERE c.org_id=%s AND (%s::text IS NULL OR c.campus_id=%s) ORDER BY c.name,c.id''',
                (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_create_class(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_class', data)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            course = db.execute('SELECT id,name FROM courses WHERE org_id=%s AND id=%s AND active=TRUE',
                                (actor['org_id'], data['course_id'])).fetchone()
            if not course:
                raise Problem(404, 'course_missing', '未找到有效课程')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            name = data['name'].strip()
            if not name or not data['reason'].strip():
                raise Problem(422, 'invalid_class', '请填写班级名称、课程和操作原因')
            if db.execute('SELECT 1 FROM classes WHERE org_id=%s AND name=%s',
                          (actor['org_id'], name)).fetchone():
                raise Problem(409, 'class_name_conflict', '该班级名称已存在')
            now, class_id = self.clock(), uid()
            campus_id = data.get('campus_id') or actor.get('campus_id')
            self._require_campus_match(actor, campus_id)
            if campus_id:
                self._campus_exists(db, actor['org_id'], campus_id)
            elif actor.get('managed_access'):
                raise Problem(422, 'class_campus_required', '新班级须选择校区')
            db.execute('INSERT INTO classes(id,org_id,name,course_id,created_at,campus_id) VALUES(%s,%s,%s,%s,%s,%s)',
                       (class_id, actor['org_id'], name, course['id'], now, campus_id))
            response = {'accepted': True, 'duplicate': False, 'class_id': class_id,
                        'name': name, 'course_id': course['id'], 'course_name': course['name'], 'version': 0,
                        'student_count': 0, 'active_student_count': 0, 'teacher_ids': [],
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_class_created', class_id,
                        {'name': name, 'course_id': course['id'], 'course_name': course['name'], 'version': 0,
                         'reason': data['reason'], 'balance_changed': False,
                         'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_update_class(self, token, class_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_update_class', {'class_id': class_id, **data})
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'classes', class_id)
            self._lock_command(db, actor, reference)
            self._lock_lesson_schedule(db, actor['org_id'])
            class_row = db.execute('''SELECT c.id,c.name,c.course_id,co.name AS course_name
                FROM classes c JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                WHERE c.org_id=%s AND c.id=%s FOR UPDATE OF c''', (actor['org_id'], class_id)).fetchone()
            if not class_row:
                raise Problem(404, 'class_missing', '未找到班级')
            course = db.execute('''SELECT id,name,active FROM courses
                WHERE org_id=%s AND id=%s AND (active=TRUE OR id=%s)''',
                                (actor['org_id'], data['course_id'], class_row['course_id'])).fetchone()
            if not course or (not course['active'] and course['id'] != class_row['course_id']):
                raise Problem(404, 'course_missing', '未找到有效课程')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            name = data['name'].strip()
            if not name or not data['reason'].strip():
                raise Problem(422, 'invalid_class', '请填写班级名称、课程和操作原因')
            version_row = db.execute('''SELECT version FROM class_versions
                WHERE org_id=%s AND class_id=%s FOR UPDATE''', (actor['org_id'], class_id)).fetchone()
            current_version = version_row['version'] if version_row else 0
            if current_version != data['expected_version']:
                raise Problem(409, 'class_changed', '班级资料已被其他操作更新，请刷新后重新核对')
            if db.execute('SELECT 1 FROM classes WHERE org_id=%s AND name=%s AND id<>%s',
                          (actor['org_id'], name, class_id)).fetchone():
                raise Problem(409, 'class_name_conflict', '该班级名称已存在')
            now, version = self.clock(), current_version + 1
            if course['id'] != class_row['course_id']:
                db.execute('''INSERT INTO lesson_fee_courses(org_id,lesson_id,course_id)
                    SELECT org_id,id,%s FROM lessons WHERE org_id=%s AND class_id=%s AND starts_at<=%s
                    ON CONFLICT(org_id,lesson_id) DO NOTHING''',
                    (class_row['course_id'], actor['org_id'], class_id, now))
            db.execute('UPDATE classes SET name=%s,course_id=%s WHERE org_id=%s AND id=%s',
                       (name, course['id'], actor['org_id'], class_id))
            db.execute('''INSERT INTO class_versions(org_id,class_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,class_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], class_id, version, now))
            response = {'accepted': True, 'duplicate': False, 'class_id': class_id,
                        'previous_name': class_row['name'], 'previous_course_id': class_row['course_id'],
                        'previous_course_name': class_row['course_name'], 'course_id': course['id'],
                        'name': name, 'course_name': course['name'],
                        'previous_version': current_version, 'version': version,
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_class_updated', class_id,
                        {'previous_name': class_row['name'], 'previous_course_id': class_row['course_id'],
                         'previous_course_name': class_row['course_name'], 'course_id': course['id'],
                         'name': name, 'course_name': course['name'], 'previous_version': current_version,
                         'version': version, 'reason': data['reason'],
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_accounts(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT u.id,u.login,u.display_name,u.active,cp.id AS campus_id,cp.name AS campus_name,
                    COALESCE(uv.version,0) AS version,(c.user_id IS NOT NULL) AS password_configured,
                    COALESCE((SELECT array_agg(tc.class_id ORDER BY tc.class_id)
                        FROM teacher_classes tc JOIN classes c ON c.org_id=tc.org_id AND c.id=tc.class_id
                        WHERE tc.org_id=u.org_id AND tc.user_id=u.id AND (%s::text IS NULL OR c.campus_id=%s)),'{}') AS class_ids
                FROM users u
                LEFT JOIN teacher_campuses tc ON tc.org_id=u.org_id AND tc.user_id=u.id
                LEFT JOIN campuses cp ON cp.org_id=tc.org_id AND cp.id=tc.campus_id
                LEFT JOIN user_versions uv ON uv.org_id=u.org_id AND uv.user_id=u.id
                LEFT JOIN user_credentials c ON c.org_id=u.org_id AND c.user_id=u.id
                WHERE u.org_id=%s AND (%s::text IS NULL OR cp.id=%s)
                  AND EXISTS(SELECT 1 FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher')
                  AND NOT EXISTS(SELECT 1 FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin')
                ORDER BY u.active DESC,u.display_name,u.id''', (actor.get('campus_id'), actor.get('campus_id'), actor['org_id'],
                 actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_campuses(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            return {'items': db.execute('SELECT id,name FROM campuses WHERE org_id=%s AND (%s::text IS NULL OR id=%s) ORDER BY name,id',
                                        (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()}
        return self.command(token, 'user', run)

    def admin_create_campus(self, token, name):
        def run(db, actor):
            self._require_admin(actor)
            row = db.execute('''INSERT INTO campuses(id,org_id,name) VALUES(%s,%s,%s)
                ON CONFLICT(org_id,name) DO NOTHING RETURNING id,name''',
                (uid(), actor['org_id'], name.strip())).fetchone()
            if not row:
                raise Problem(409, 'campus_exists', '该校区名称已存在')
            self._audit(db, actor, 'campus_created', row['id'], {'name': row['name']}, self.clock())
            return row
        return self.command(token, 'user', run)

    @staticmethod
    def _set_teacher_campus(db, org_id, user_id, campus_id):
        campus = db.execute('SELECT id,name FROM campuses WHERE org_id=%s AND id=%s',
                            (org_id, campus_id)).fetchone()
        if not campus:
            raise Problem(404, 'campus_missing', '未找到本机构的校区')
        db.execute('''INSERT INTO teacher_campuses VALUES(%s,%s,%s)
            ON CONFLICT(org_id,user_id) DO UPDATE SET campus_id=EXCLUDED.campus_id''',
            (org_id, user_id, campus_id))
        return campus

    def admin_set_teacher_campus(self, token, user_id, campus_id):
        def run(db, actor):
            self._require_admin(actor)
            self._lock_teacher_account(db, actor, user_id)
            campus = self._set_teacher_campus(db, actor['org_id'], user_id, campus_id)
            self._audit(db, actor, 'teacher_campus_set', user_id, {'campus_id': campus_id}, self.clock())
            return {'campus_id': campus_id, 'campus_name': campus['name']}
        return self.command(token, 'user', run)

    def admin_create_account(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_account', data)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._require_campus_match(actor, data.get('campus_id'))
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            try:
                login = identifier(data['login'])
                hashed = password_hash(data['password'])
            except (AttributeError, ValueError):
                raise Problem(422, 'invalid_account', '账号或密码格式不正确；密码须为 15 至 128 位') from None
            display_name, reason = data['display_name'].strip(), data['reason'].strip()
            if not display_name or not reason:
                raise Problem(422, 'invalid_account', '请填写教师姓名和操作原因')
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('runtime-login:' + login,))
            if db.execute('SELECT 1 FROM users WHERE login=%s', (login,)).fetchone():
                raise Problem(409, 'login_exists', '这个登录账号已被使用')
            now, user_id = self.clock(), uid()
            db.execute('''INSERT INTO users(id,org_id,login,display_name,active,created_at,password_reset_required)
                VALUES(%s,%s,%s,%s,TRUE,%s,FALSE)''',
                (user_id, actor['org_id'], login, display_name, now))
            db.execute('INSERT INTO roles VALUES(%s,%s,\'teacher\')',
                       (actor['org_id'], user_id))
            db.execute('INSERT INTO user_credentials VALUES(%s,%s,%s,%s)',
                       (actor['org_id'], user_id, hashed, now))
            db.execute('INSERT INTO user_versions VALUES(%s,%s,0,%s)',
                       (actor['org_id'], user_id, now))
            response = {'accepted': True, 'duplicate': False, 'user_id': user_id,
                        'login': login, 'display_name': display_name, 'active': True,
                        'roles': ['teacher'], 'version': 0, 'class_ids': [],
                        'password_returned': False, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            if data.get('campus_id'):
                campus = self._set_teacher_campus(db, actor['org_id'], user_id, data['campus_id'])
                response.update(campus_id=campus['id'], campus_name=campus['name'])
            self._audit(db, actor, 'admin_teacher_account_created', user_id,
                        {'login': login, 'display_name': display_name, 'roles': ['teacher'],
                         'active': True, 'version': 0, 'reason': reason,
                         'campus_id': data.get('campus_id'), 'password_recorded': False,
                         'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    @staticmethod
    def _lock_teacher_account(db, actor, user_id):
        account = db.execute('''SELECT u.id,u.login,u.display_name,u.active,
                COALESCE(uv.version,0) AS version,
                ARRAY(SELECT r.role FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id) AS roles
            FROM users u LEFT JOIN user_versions uv ON uv.org_id=u.org_id AND uv.user_id=u.id
            WHERE u.org_id=%s AND u.id=%s FOR UPDATE OF u''',
            (actor['org_id'], user_id)).fetchone()
        if not account or 'teacher' not in account['roles'] or 'admin' in account['roles']:
            raise Problem(404, 'teacher_account_missing', '未找到可管理的教师账号')
        return account

    @staticmethod
    def _revoke_user_sessions(db, org_id, user_id, now):
        count = db.execute('''SELECT count(*) AS n FROM auth_sessions
            WHERE org_id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>%s''',
            (org_id, user_id, now)).fetchone()['n']
        db.execute('''UPDATE auth_sessions SET revoked_at=%s
            WHERE org_id=%s AND user_id=%s AND revoked_at IS NULL''',
            (now, org_id, user_id))
        return count

    def admin_set_account_status(self, token, user_id, data):
        payload = dict(data, user_id=user_id)
        reference = data['command_ref']
        digest = canonical_digest('admin_set_account_status', payload)
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            account = self._lock_teacher_account(db, actor, user_id)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if account['version'] != data['expected_version']:
                raise Problem(409, 'account_changed', '账号已被其他操作更新，请刷新后重新核对')
            if account['active'] == data['active']:
                raise Problem(409, 'account_state_unchanged', '账号已经处于该状态')
            if not data['active'] and db.execute('''SELECT 1 FROM teacher_classes
                WHERE org_id=%s AND user_id=%s LIMIT 1''', (actor['org_id'], user_id)).fetchone():
                raise Problem(409, 'teaching_handoff_required', '该教师仍有任课班级，请先完成任课交接')
            now = self.clock()
            if not data['active'] and db.execute('''SELECT 1 FROM lesson_substitutions s JOIN lessons l
                ON l.org_id=s.org_id AND l.id=s.lesson_id
                WHERE s.org_id=%s AND s.user_id=%s AND l.state='scheduled' AND l.ends_at>%s LIMIT 1''',
                (actor['org_id'], user_id, now)).fetchone():
                raise Problem(409, 'teaching_handoff_required', '该教师仍有待完成的代课，请先调整代课安排')
            db.execute('UPDATE users SET active=%s WHERE org_id=%s AND id=%s',
                       (data['active'], actor['org_id'], user_id))
            version = account['version'] + 1
            db.execute('''INSERT INTO user_versions(org_id,user_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,user_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], user_id, version, now))
            revoked = self._revoke_user_sessions(db, actor['org_id'], user_id, now)
            response = {'accepted': True, 'duplicate': False, 'user_id': user_id,
                        'login': account['login'], 'display_name': account['display_name'],
                        'active': data['active'], 'previous_version': account['version'],
                        'version': version, 'sessions_revoked': revoked,
                        'assignments_changed': False, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_teacher_account_status_changed', user_id,
                        {'active': data['active'], 'previous_version': account['version'],
                         'version': version, 'sessions_revoked': revoked,
                         'reason': data['reason'], 'assignments_changed': False,
                         'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_reset_account_password(self, token, user_id, data):
        payload = dict(data, user_id=user_id)
        reference = data['command_ref']
        digest = canonical_digest('admin_reset_account_password', payload)
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            account = self._lock_teacher_account(db, actor, user_id)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if account['version'] != data['expected_version']:
                raise Problem(409, 'account_changed', '账号已被其他操作更新，请刷新后重新核对')
            try:
                hashed = password_hash(data['password'])
            except (AttributeError, ValueError):
                raise Problem(422, 'invalid_password', '密码须为 15 至 128 位') from None
            now = self.clock()
            db.execute('''INSERT INTO user_credentials(org_id,user_id,password_hash,changed_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,user_id) DO UPDATE
                SET password_hash=EXCLUDED.password_hash,changed_at=EXCLUDED.changed_at''',
                (actor['org_id'], user_id, hashed, now))
            db.execute('UPDATE users SET password_reset_required=FALSE WHERE org_id=%s AND id=%s',
                       (actor['org_id'], user_id))
            version = account['version'] + 1
            db.execute('''INSERT INTO user_versions(org_id,user_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,user_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], user_id, version, now))
            revoked = self._revoke_user_sessions(db, actor['org_id'], user_id, now)
            response = {'accepted': True, 'duplicate': False, 'user_id': user_id,
                        'login': account['login'], 'display_name': account['display_name'],
                        'active': account['active'], 'previous_version': account['version'],
                        'version': version, 'password_changed': True,
                        'password_returned': False, 'sessions_revoked': revoked,
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_teacher_password_reset', user_id,
                        {'previous_version': account['version'], 'version': version,
                         'password_changed': True, 'password_recorded': False,
                         'sessions_revoked': revoked, 'reason': data['reason'],
                         'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_teachers(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT u.id,u.display_name,u.login,
                    array_agg(tc.class_id ORDER BY tc.class_id)
                        FILTER (WHERE tc.class_id IS NOT NULL AND (%s::text IS NULL OR cl.campus_id=%s)) AS class_ids
                FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id
                LEFT JOIN teacher_classes tc ON tc.org_id=u.org_id AND tc.user_id=u.id
                LEFT JOIN classes cl ON cl.org_id=tc.org_id AND cl.id=tc.class_id
                LEFT JOIN teacher_campuses cp ON cp.org_id=u.org_id AND cp.user_id=u.id
                WHERE u.org_id=%s AND u.active=TRUE AND r.role='teacher'
                  AND (%s::text IS NULL OR cp.campus_id=%s)
                GROUP BY u.id ORDER BY u.display_name,u.id''', (actor.get('campus_id'), actor.get('campus_id'), actor['org_id'],
                 actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_set_class_teacher(self, token, class_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_set_class_teacher', {'class_id': class_id, **data})
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'classes', class_id)
            self._lock_command(db, actor, reference)
            self._lock_lesson_schedule(db, actor['org_id'])
            class_row = db.execute('SELECT id,name FROM classes WHERE org_id=%s AND id=%s FOR UPDATE',
                                   (actor['org_id'], class_id)).fetchone()
            if not class_row:
                raise Problem(404, 'class_missing', '未找到班级')
            teacher_ids = sorted(set(data['teacher_ids']))
            self._scope_teachers(db, actor, teacher_ids)
            if len(teacher_ids) != len(data['teacher_ids']):
                raise Problem(422, 'invalid_teachers', '教师不能重复')
            teachers = []
            if teacher_ids:
                teachers = db.execute('''SELECT u.id,u.display_name FROM users u
                    JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id
                    WHERE u.org_id=%s AND u.id=ANY(%s) AND u.active=TRUE AND r.role='teacher'
                    ORDER BY u.id FOR UPDATE OF u''',
                    (actor['org_id'], teacher_ids)).fetchall()
                if len(teachers) != len(teacher_ids):
                    raise Problem(409, 'teacher_scope', '教师不存在、已停用或没有教师角色')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            scheduled = db.execute('''SELECT st.id,st.teacher_id,u.display_name
                FROM class_schedule_templates st JOIN users u ON u.org_id=st.org_id AND u.id=st.teacher_id
                WHERE st.org_id=%s AND st.class_id=%s AND NOT (st.teacher_id=ANY(%s))
                ORDER BY st.teacher_id,st.id FOR UPDATE OF st''', (actor['org_id'], class_id, teacher_ids)).fetchall()
            if scheduled:
                names = list(dict.fromkeys(row['display_name'] for row in scheduled))
                raise Problem(409, 'schedule_teacher_conflict',
                              '排课模板仍引用教师：' + '、'.join(names) + '；请先调整模板')
            version_row = db.execute('''SELECT version FROM class_versions
                WHERE org_id=%s AND class_id=%s FOR UPDATE''', (actor['org_id'], class_id)).fetchone()
            current_version = version_row['version'] if version_row else 0
            if current_version != data['expected_version']:
                raise Problem(409, 'class_changed', '班级任课已被其他操作更新，请刷新后重新核对')
            previous = db.execute('''SELECT user_id FROM teacher_classes
                WHERE org_id=%s AND class_id=%s ORDER BY user_id''',
                (actor['org_id'], class_id)).fetchall()
            now = self.clock()
            future = db.execute('''SELECT id,schedule_id,starts_at,ends_at FROM lessons WHERE org_id=%s AND class_id=%s
                AND state='scheduled' AND starts_at>%s ORDER BY starts_at,id FOR UPDATE''',
                (actor['org_id'], class_id, now)).fetchall()
            updated, retained = [], []
            teacher_by_id = {row['id']: row for row in teachers}
            for lesson in future:
                processed = db.execute('''SELECT 1 FROM attendance_records
                    WHERE org_id=%s AND lesson_id=%s AND
                      (status<>'missing' OR revision<>0 OR last_event_id IS NOT NULL) LIMIT 1''',
                    (actor['org_id'], lesson['id'])).fetchone()
                if processed:
                    retained.append(lesson['id'])
                    continue
                assigned = None
                if lesson['schedule_id']:
                    scheduled_teacher = db.execute('''SELECT teacher_id FROM class_schedule_templates
                        WHERE org_id=%s AND id=%s''',
                        (actor['org_id'], lesson['schedule_id'])).fetchone()
                    if scheduled_teacher:
                        assigned = teacher_by_id.get(scheduled_teacher['teacher_id'])
                elif teachers:
                    assigned = teachers[0]
                if assigned and not db.execute('SELECT 1 FROM lesson_substitutions WHERE org_id=%s AND lesson_id=%s',
                                              (actor['org_id'], lesson['id'])).fetchone():
                    self._require_teacher_availability(db, actor['org_id'], [assigned['id']],
                                                       lesson['starts_at'], lesson['ends_at'], lesson['id'],
                                                       substitutes_only=True)
                db.execute('DELETE FROM lesson_teachers WHERE org_id=%s AND lesson_id=%s',
                           (actor['org_id'], lesson['id']))
                if assigned:
                    db.execute('''INSERT INTO lesson_teachers(org_id,lesson_id,user_id,teacher_name)
                        VALUES(%s,%s,%s,%s)''',
                        (actor['org_id'], lesson['id'], assigned['id'], assigned['display_name']))
                updated.append(lesson['id'])
            db.execute('DELETE FROM teacher_classes WHERE org_id=%s AND class_id=%s',
                       (actor['org_id'], class_id))
            for teacher in teachers:
                db.execute('INSERT INTO teacher_classes VALUES(%s,%s,%s)',
                           (actor['org_id'], teacher['id'], class_id))
            version = current_version + 1
            db.execute('''INSERT INTO class_versions(org_id,class_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,class_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], class_id, version, now))
            response = {'accepted': True, 'duplicate': False, 'class_id': class_id,
                        'class_name': class_row['name'],
                        'previous_teacher_ids': [row['user_id'] for row in previous],
                        'teacher_ids': teacher_ids,
                        'teacher_names': [row['display_name'] for row in teachers],
                        'teacher_id': teacher_ids[0] if teacher_ids else None,
                        'previous_version': current_version, 'version': version,
                        'future_lessons_updated': len(updated), 'updated_future_lesson_ids': updated,
                        'processed_future_lessons_retained': len(retained),
                        'retained_processed_lesson_ids': retained,
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_class_teacher_changed', class_id,
                        {'previous_teacher_ids': [row['user_id'] for row in previous],
                         'teacher_ids': teacher_ids, 'previous_version': current_version, 'version': version,
                         'reason': data['reason'], 'updated_future_lesson_ids': updated,
                         'retained_processed_lesson_ids': retained,
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    @staticmethod
    def _schedule_values(data):
        for key in ('weekday', 'interval_weeks', 'duration_minutes', 'end_day_offset'):
            if type(data[key]) is not int:
                raise Problem(422, 'invalid_schedule', '排课星期、周期间隔和时长必须是整数')
        if not 1 <= data['weekday'] <= 7 or data['interval_weeks'] not in (1, 2):
            raise Problem(422, 'invalid_schedule', '星期须为 1 至 7，周期间隔仅支持每周或隔周')
        if data['end_day_offset'] not in (0, 1):
            raise Problem(422, 'invalid_schedule', '结束日期仅支持当天或次日')
        try:
            anchor = datetime.strptime(data['anchor_date'], '%Y-%m-%d').date()
            effective = datetime.strptime(data['effective_date'], '%Y-%m-%d').date()
            if anchor.isoformat() != data['anchor_date'] or effective.isoformat() != data['effective_date']:
                raise ValueError()
        except (TypeError, ValueError):
            raise Problem(422, 'invalid_schedule_date', '锚点日期和生效日期格式应为 YYYY-MM-DD') from None
        try:
            start = datetime.strptime(data['start_time'], '%H:%M')
            end = datetime.strptime(data['end_time'], '%H:%M')
            if start.strftime('%H:%M') != data['start_time'] or end.strftime('%H:%M') != data['end_time']:
                raise ValueError()
        except (TypeError, ValueError):
            raise Problem(422, 'invalid_schedule_time', '开始和结束时间格式应为 HH:MM') from None
        start_minutes = start.hour * 60 + start.minute
        end_minutes = end.hour * 60 + end.minute + data['end_day_offset'] * 1440
        duration = end_minutes - start_minutes
        if not 1 <= duration <= 720 or duration != data['duration_minutes']:
            raise Problem(422, 'schedule_duration_mismatch', '结束时间与开始时间的时长不符，且须在 1 至 720 分钟内')
        if anchor.isoweekday() != data['weekday']:
            raise Problem(422, 'schedule_anchor_weekday', '锚点日期的星期必须与排课星期一致')
        if not data['reason'].strip():
            raise Problem(422, 'invalid_schedule_reason', '请填写排课操作原因')
        return {'weekday': data['weekday'], 'interval_weeks': data['interval_weeks'],
                'anchor_date': data['anchor_date'], 'start_time': data['start_time'],
                'end_time': data['end_time'], 'duration_minutes': duration,
                'end_day_offset': data['end_day_offset'], 'effective_date': data['effective_date']}

    @staticmethod
    def _schedule_class(db, actor, class_id, require_active_course=False):
        class_row = db.execute('''SELECT c.id,c.name,c.campus_id,co.name AS course_name,co.active AS course_active
            FROM classes c JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
            WHERE c.org_id=%s AND c.id=%s FOR UPDATE OF c''', (actor['org_id'], class_id)).fetchone()
        if not class_row:
            raise Problem(404, 'class_missing', '未找到排课班级')
        if require_active_course and not class_row['course_active']:
            raise Problem(409, 'course_inactive', '课程已停用，不能新增排课模板')
        RuntimeStore._require_campus_match(actor, class_row['campus_id'])
        return class_row

    @staticmethod
    def _schedule_teacher(db, actor, class_id, teacher_id):
        RuntimeStore._scope_teachers(db, actor, [teacher_id])
        teacher = db.execute('''SELECT u.id,u.display_name FROM users u
            JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id
            JOIN teacher_classes tc ON tc.org_id=u.org_id AND tc.user_id=u.id
            WHERE u.org_id=%s AND u.id=%s AND tc.class_id=%s
              AND u.active=TRUE AND r.role='teacher' FOR UPDATE OF u''',
            (actor['org_id'], teacher_id, class_id)).fetchone()
        if not teacher:
            raise Problem(409, 'teacher_scope', '排课教师必须在职且已授权给该班级')
        return teacher

    @staticmethod
    def _schedule_duplicate(db, org_id, class_id, values, exclude_id=None):
        row = db.execute('''SELECT id FROM class_schedule_templates
            WHERE org_id=%s AND class_id=%s AND weekday=%s AND interval_weeks=%s
              AND anchor_date=%s AND start_time=%s AND end_time=%s AND end_day_offset=%s
              AND effective_date=%s AND (%s::text IS NULL OR id<>%s)''',
            (org_id, class_id, values['weekday'], values['interval_weeks'], values['anchor_date'],
             values['start_time'], values['end_time'], values['end_day_offset'],
             values['effective_date'], exclude_id, exclude_id)).fetchone()
        if row:
            raise Problem(409, 'schedule_conflict', '该班级已有相同的每周排课模板')

    def admin_schedule_templates(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT st.id,st.class_id,st.weekday,st.interval_weeks,st.anchor_date,
                    st.start_time,st.end_time,st.duration_minutes,st.end_day_offset,st.effective_date,
                    st.teacher_id,c.name AS class_name,co.name AS course_name,co.active AS course_active,
                    u.display_name AS teacher_name,
                    (SELECT count(*) FROM lessons l WHERE l.org_id=st.org_id AND l.schedule_id=st.id) AS linked_lesson_count
                FROM class_schedule_templates st
                JOIN classes c ON c.org_id=st.org_id AND c.id=st.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                JOIN users u ON u.org_id=st.org_id AND u.id=st.teacher_id
                WHERE st.org_id=%s AND (%s::text IS NULL OR c.campus_id=%s)
                ORDER BY c.name,st.weekday,st.start_time,st.anchor_date,st.id''', (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows], 'timezone': 'Asia/Shanghai'}
        return self.command(token, 'user', run)

    def admin_create_schedule_template(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_schedule_template', data)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            values = self._schedule_values(data)
            class_row = self._schedule_class(db, actor, data['class_id'], True)
            teacher = self._schedule_teacher(db, actor, data['class_id'], data['teacher_id'])
            self._schedule_duplicate(db, actor['org_id'], data['class_id'], values)
            schedule_id, now = uid(), self.clock()
            db.execute('''INSERT INTO class_schedule_templates
                (id,org_id,class_id,weekday,interval_weeks,anchor_date,start_time,end_time,
                 duration_minutes,end_day_offset,effective_date,teacher_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (schedule_id, actor['org_id'], data['class_id'], values['weekday'],
                 values['interval_weeks'], values['anchor_date'], values['start_time'],
                 values['end_time'], values['duration_minutes'], values['end_day_offset'],
                 values['effective_date'], teacher['id']))
            response = {'accepted': True, 'duplicate': False, 'schedule_id': schedule_id,
                        'class_id': class_row['id'], 'class_name': class_row['name'],
                        'course_name': class_row['course_name'], 'teacher_id': teacher['id'],
                        'teacher_name': teacher['display_name'], **values, 'linked_lesson_count': 0,
                        'existing_lessons_changed': False, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_schedule_template_created', schedule_id,
                        {'class_id': class_row['id'], 'teacher_id': teacher['id'], **values,
                         'reason': data['reason'], 'existing_lessons_changed': False,
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_update_schedule_template(self, token, schedule_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_update_schedule_template', {'schedule_id': schedule_id, **data})
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            existing = db.execute('''SELECT class_id FROM class_schedule_templates
                WHERE org_id=%s AND id=%s''', (actor['org_id'], schedule_id)).fetchone()
            if not existing:
                raise Problem(404, 'schedule_missing', '未找到排课模板')
            values = self._schedule_values(data)
            # Class identity is immutable so existing lesson.schedule_id links stay meaningful.
            class_row = self._schedule_class(db, actor, existing['class_id'])
            teacher = self._schedule_teacher(db, actor, existing['class_id'], data['teacher_id'])
            current = db.execute('''SELECT * FROM class_schedule_templates
                WHERE org_id=%s AND id=%s FOR UPDATE''', (actor['org_id'], schedule_id)).fetchone()
            if not current:
                raise Problem(404, 'schedule_missing', '未找到排课模板')
            self._schedule_duplicate(db, actor['org_id'], existing['class_id'], values, schedule_id)
            db.execute('''UPDATE class_schedule_templates SET weekday=%s,interval_weeks=%s,
                anchor_date=%s,start_time=%s,end_time=%s,duration_minutes=%s,end_day_offset=%s,
                effective_date=%s,teacher_id=%s WHERE org_id=%s AND id=%s''',
                (values['weekday'], values['interval_weeks'], values['anchor_date'], values['start_time'],
                 values['end_time'], values['duration_minutes'], values['end_day_offset'],
                 values['effective_date'], teacher['id'], actor['org_id'], schedule_id))
            linked = db.execute('''SELECT count(*) AS n FROM lessons
                WHERE org_id=%s AND schedule_id=%s''', (actor['org_id'], schedule_id)).fetchone()['n']
            now = self.clock()
            response = {'accepted': True, 'duplicate': False, 'schedule_id': schedule_id,
                        'class_id': class_row['id'], 'class_name': class_row['name'],
                        'course_name': class_row['course_name'], 'teacher_id': teacher['id'],
                        'teacher_name': teacher['display_name'], 'previous': dict(current), **values,
                        'linked_lesson_count': linked, 'existing_lessons_changed': False,
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_schedule_template_updated', schedule_id,
                        {'previous': dict(current), 'class_id': class_row['id'],
                         'teacher_id': teacher['id'], **values, 'reason': data['reason'],
                         'linked_lesson_count': linked, 'existing_lessons_changed': False,
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_delete_schedule_template(self, token, schedule_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_delete_schedule_template', {'schedule_id': schedule_id, **data})
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            existing = db.execute('''SELECT class_id FROM class_schedule_templates
                WHERE org_id=%s AND id=%s''', (actor['org_id'], schedule_id)).fetchone()
            if not existing:
                raise Problem(404, 'schedule_missing', '未找到排课模板')
            class_row = self._schedule_class(db, actor, existing['class_id'])
            current = db.execute('''SELECT * FROM class_schedule_templates
                WHERE org_id=%s AND id=%s FOR UPDATE''', (actor['org_id'], schedule_id)).fetchone()
            if not current:
                raise Problem(404, 'schedule_missing', '未找到排课模板')
            linked = db.execute('''SELECT count(*) AS n FROM lessons
                WHERE org_id=%s AND schedule_id=%s''', (actor['org_id'], schedule_id)).fetchone()['n']
            if linked:
                raise Problem(409, 'schedule_in_use', '该模板已被课次引用，不能删除；可编辑模板，已有课次保持不变')
            if not data['reason'].strip():
                raise Problem(422, 'invalid_schedule_reason', '请填写删除原因')
            now = self.clock()
            db.execute('DELETE FROM class_schedule_templates WHERE org_id=%s AND id=%s',
                       (actor['org_id'], schedule_id))
            response = {'accepted': True, 'duplicate': False, 'deleted': True,
                        'schedule_id': schedule_id, 'class_id': class_row['id'],
                        'class_name': class_row['name'], 'previous': dict(current),
                        'linked_lesson_count': 0, 'existing_lessons_changed': False,
                        'balance_changed': False, 'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_schedule_template_deleted', schedule_id,
                        {'previous': dict(current), 'reason': data['reason'],
                         'existing_lessons_changed': False, 'balance_changed': False,
                         'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_lessons(self, token, day=None):
        def run(db, actor):
            self._require_campus_operations(actor)
            try:
                selected = (datetime.strptime(day, '%Y-%m-%d').date() if day is not None
                            else datetime.fromtimestamp(self.clock(), CHINA_TZ).date())
                if day is not None and selected.isoformat() != day:
                    raise ValueError()
            except (TypeError, ValueError):
                raise Problem(422, 'invalid_date', '日期格式应为 YYYY-MM-DD') from None
            start = int(datetime.combine(selected, datetime.min.time(), CHINA_TZ).timestamp())
            rows = db.execute('''SELECT l.id,l.class_id,l.starts_at,l.ends_at,l.state,
                    c.name AS class_name,co.name AS course_name,cr.charge_units,
                    cr.revision AS charge_rule_revision,COALESCE(lv.version,0) AS version,
                    (SELECT string_agg(u.display_name,'、' ORDER BY u.display_name,u.id)
                     FROM effective_lesson_teachers lt JOIN users u ON u.org_id=lt.org_id AND u.id=lt.user_id
                     WHERE lt.org_id=l.org_id AND lt.lesson_id=l.id) AS teacher_names,
                    (SELECT s.user_id FROM lesson_substitutions s
                     WHERE s.org_id=l.org_id AND s.lesson_id=l.id) AS substitute_id,
                    count(ls.student_id) FILTER (WHERE ls.expected) AS expected_count,
                    count(ls.student_id) FILTER (WHERE ar.status='arrived') AS arrived_count,
                    count(ls.student_id) FILTER (WHERE ar.status='leave') AS leave_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='missing') AS missing_count,
                    count(ar.student_id) FILTER (WHERE ar.status<>'missing' OR ar.revision<>0
                        OR ar.last_event_id IS NOT NULL) AS processed_count
                FROM lessons l JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_charge_rules cr ON cr.org_id=l.org_id AND cr.lesson_id=l.id
                LEFT JOIN lesson_versions lv ON lv.org_id=l.org_id AND lv.lesson_id=l.id
                LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                    AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                WHERE l.org_id=%s AND l.starts_at>=%s AND l.starts_at<%s
                  AND (%s::text IS NULL OR l.campus_id=%s)
                GROUP BY l.id,c.id,co.id,cr.org_id,cr.lesson_id,lv.org_id,lv.lesson_id
                ORDER BY l.starts_at,l.id''',
                (actor['org_id'], start, start + 86400, actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'date': selected.isoformat(), 'timezone': 'Asia/Shanghai',
                    'server_time': self.clock(), 'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_lesson(self, token, lesson_id):
        """Return the complete roster for an administrator's cancelled-lesson review."""
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'lessons', lesson_id)
            lesson = db.execute('''SELECT l.id,l.org_id,l.class_id,l.starts_at,l.ends_at,l.state,
                    l.created_at,c.name AS class_name,co.name AS course_name,COALESCE(lv.version,0) AS version
                FROM lessons l JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_versions lv ON lv.org_id=l.org_id AND lv.lesson_id=l.id
                WHERE l.org_id=%s AND l.id=%s''', (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到该课次')
            students = db.execute('''SELECT ls.student_id,s.name,s.external_ref,ls.expected,ls.charge_units,
                    ar.status,ar.revision AS attendance_revision,sb.units AS balance_units,
                    sb.revision AS balance_revision
                FROM lesson_students ls JOIN students s ON s.org_id=ls.org_id AND s.id=ls.student_id
                JOIN attendance_records ar ON ar.org_id=ls.org_id AND ar.lesson_id=ls.lesson_id
                    AND ar.student_id=ls.student_id
                JOIN student_balances sb ON sb.org_id=ls.org_id AND sb.student_id=ls.student_id
                WHERE ls.org_id=%s AND ls.lesson_id=%s ORDER BY s.name,s.id''',
                (actor['org_id'], lesson_id)).fetchall()
            if actor.get('campus_id'):
                students = [dict(row, balance_units=None, balance_revision=None) for row in students]
            return {'lesson': dict(lesson), 'students': [dict(row) for row in students],
                    'server_time': self.clock()}
        return self.command(token, 'user', run)

    def admin_cancelled_correction(self, token, lesson_id, kind, data):
        """Resolve a pre-cancellation attendance conflict without reopening the lesson.

        Arrival uses the original roster charge and debits the server exactly once;
        leave never debits. The cancelled lesson remains excluded from reports.
        """
        if kind not in {'makeup', 'leave'}:
            raise ValueError('Unsupported correction')
        reference = data['event_ref']
        payload = dict(data, lesson_id=lesson_id, kind=kind)
        digest = canonical_digest('admin_cancelled_' + kind, payload)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'lessons', lesson_id)
            self._lock_command(db, actor, reference)
            fact = self._lesson_student(db, actor['org_id'], lesson_id, data['student_id'])
            if not fact:
                raise Problem(404, 'lesson_student_missing', '未找到该课堂学生')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                if actor.get('campus_id'):
                    return dict(prior, balance_units=None, balance_revision=None)
                return prior
            if fact['state'] != 'cancelled':
                raise Problem(409, 'lesson_not_cancelled', '仅取消课次的签到冲突可由管理员在此处理')
            self._require_missing(fact, data['expected_revision'])
            now = self.clock()
            event_id = self._event(db, actor, fact, kind, reference, data['reason'], now, now)
            db.execute('''UPDATE attendance_records SET status=%s,revision=%s,last_event_id=%s,updated_at=%s
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s AND revision=%s''',
                ('arrived' if kind == 'makeup' else 'leave', data['expected_revision'] + 1,
                 event_id, now, actor['org_id'], lesson_id, data['student_id'], data['expected_revision']))
            units = fact['balance_units']
            balance_revision = fact['balance_revision']
            if kind == 'makeup':
                units, balance_revision = self._debit(db, actor, fact, 'makeup', reference,
                                                      balance_revision, now, data['reason'])
            response = dict(accepted=True, duplicate=False, event_id=event_id,
                            event_ref=reference, lesson_id=lesson_id, student_id=data['student_id'],
                            status='arrived' if kind == 'makeup' else 'leave',
                            attendance_revision=data['expected_revision'] + 1,
                            balance_revision=balance_revision, balance_units=units,
                            cancelled_lesson=True, report_excluded=True)
            if actor.get('campus_id'):
                response.update(balance_units=None, balance_revision=None)
            self._audit(db, actor, 'admin_cancelled_' + kind, event_id,
                        {'lesson_id': lesson_id, 'student_id': data['student_id'],
                         'reason': data['reason'], 'cancelled_lesson': True,
                         'report_excluded': True}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_create_lesson(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_lesson', data)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            self._lock_lesson_schedule(db, actor['org_id'])
            self._scope_record(db, actor, 'classes', data['class_id'])
            self._scope_teachers(db, actor, [data['teacher_id']])
            class_row = db.execute('''SELECT c.id,c.name,co.name AS course_name,co.active AS course_active,c.campus_id FROM classes c
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                WHERE c.org_id=%s AND c.id=%s FOR UPDATE OF c''',
                (actor['org_id'], data['class_id'])).fetchone()
            if not class_row:
                raise Problem(404, 'class_missing', '未找到开课班级')
            if not class_row['course_active']:
                raise Problem(409, 'course_inactive', '课程已停用，不能新建课次；历史课次不受影响')
            teacher = db.execute('''SELECT u.id,u.display_name FROM users u
                JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id
                JOIN teacher_classes tc ON tc.org_id=u.org_id AND tc.user_id=u.id
                WHERE u.org_id=%s AND u.id=%s AND tc.class_id=%s
                  AND u.active=TRUE AND r.role='teacher' FOR UPDATE OF u''',
                (actor['org_id'], data['teacher_id'], data['class_id'])).fetchone()
            if not teacher:
                raise Problem(409, 'teacher_scope', '教师未获得该班级授权或已停用')
            schedule_id = data.get('schedule_id')
            if schedule_id is not None:
                schedule = db.execute('''SELECT id,teacher_id FROM class_schedule_templates
                    WHERE org_id=%s AND id=%s AND class_id=%s''',
                    (actor['org_id'], schedule_id, data['class_id'])).fetchone()
                if not schedule or schedule['teacher_id'] != teacher['id']:
                    raise Problem(409, 'schedule_teacher_mismatch', '课次教师必须与所选排课模板一致')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            if data['starts_at'] <= now:
                raise Problem(409, 'lesson_clock', '只能创建尚未开始的未来课次')
            if not 0 < data['ends_at'] - data['starts_at'] <= 12 * 3600:
                raise Problem(422, 'invalid_lesson_time', '结束时间必须晚于开始时间，且时长不能超过 12 小时')
            self._require_teacher_availability(db, actor['org_id'], [teacher['id']],
                                               data['starts_at'], data['ends_at'], substitutes_only=True)
            if not 0 < data['charge_units'] <= MAX_UNITS:
                raise Problem(422, 'invalid_charge_rule', '扣课单位必须是正整数')
            if db.execute('''SELECT 1 FROM lessons WHERE org_id=%s AND class_id=%s
                AND starts_at=%s AND state='scheduled' ''',
                          (actor['org_id'], data['class_id'], data['starts_at'])).fetchone():
                raise Problem(409, 'lesson_time_conflict', '该班级在同一时间已有课次')
            students = db.execute('''SELECT s.id FROM student_class_enrollments e
                JOIN students s ON s.org_id=e.org_id AND s.id=e.student_id
                WHERE e.org_id=%s AND e.class_id=%s AND e.left_at IS NULL AND s.active=TRUE ORDER BY s.id''',
                (actor['org_id'], data['class_id'])).fetchall()
            lesson_id = uid()
            db.execute('''INSERT INTO lessons(id,org_id,class_id,schedule_id,starts_at,ends_at,state,created_at,campus_id)
                VALUES(%s,%s,%s,%s,%s,%s,'scheduled',%s,%s)''',
                (lesson_id, actor['org_id'], data['class_id'], schedule_id, data['starts_at'],
                 data['ends_at'], now, class_row['campus_id']))
            db.execute('''INSERT INTO lesson_versions(org_id,lesson_id,version,updated_at)
                VALUES(%s,%s,0,%s)''', (actor['org_id'], lesson_id, now))
            db.execute('''INSERT INTO lesson_charge_rules
                (org_id,lesson_id,charge_units,revision,updated_at) VALUES(%s,%s,%s,1,%s)''',
                (actor['org_id'], lesson_id, data['charge_units'], now))
            db.execute('''INSERT INTO lesson_teachers
                (org_id,lesson_id,user_id,teacher_name) VALUES(%s,%s,%s,%s)''',
                (actor['org_id'], lesson_id, teacher['id'], teacher['display_name']))
            for student in students:
                db.execute('''INSERT INTO lesson_students
                    (org_id,class_id,lesson_id,student_id,expected,charge_units)
                    VALUES(%s,%s,%s,%s,TRUE,%s)''',
                    (actor['org_id'], data['class_id'], lesson_id, student['id'], data['charge_units']))
                db.execute('''INSERT INTO attendance_records
                    (org_id,lesson_id,student_id,status,revision,last_event_id,updated_at)
                    VALUES(%s,%s,%s,'missing',0,NULL,%s)''',
                    (actor['org_id'], lesson_id, student['id'], now))
            response = {'accepted': True, 'duplicate': False, 'lesson_id': lesson_id,
                        'class_id': data['class_id'], 'class_name': class_row['name'],
                        'course_name': class_row['course_name'], 'teacher_id': teacher['id'],
                        'teacher_name': teacher['display_name'], 'starts_at': data['starts_at'],
                        'ends_at': data['ends_at'], 'state': 'scheduled', 'version': 0,
                        'charge_units': data['charge_units'], 'charge_rule_revision': 1,
                        'roster_count': len(students), 'attendance_created': len(students),
                        'processed_count': 0, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_lesson_created', lesson_id,
                        {'class_id': data['class_id'], 'teacher_id': teacher['id'],
                         'starts_at': data['starts_at'], 'ends_at': data['ends_at'],
                         'charge_units': data['charge_units'],
                         'roster_student_ids': [row['id'] for row in students],
                         'balance_changed': False, 'history_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    @staticmethod
    def _require_pristine_future_lesson(db, actor, lesson, now):
        if lesson['state'] != 'scheduled' or lesson['starts_at'] <= now:
            raise Problem(409, 'lesson_not_editable', '只能调整尚未开始的有效课次')
        processed = db.execute('''SELECT count(*) AS n FROM attendance_records
            WHERE org_id=%s AND lesson_id=%s AND
              (status<>'missing' OR revision<>0 OR last_event_id IS NOT NULL)''',
            (actor['org_id'], lesson['id'])).fetchone()['n']
        if processed:
            raise Problem(409, 'lesson_attendance_conflict',
                          '课次已有签到、请假或撤销痕迹，请保留原课次并人工核对')
        return processed

    @staticmethod
    def _lock_lesson_schedule(db, org_id):
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                   ('runtime-substitution:' + org_id,))

    @staticmethod
    def _require_teacher_availability(db, org_id, teacher_ids, starts_at, ends_at,
                                      lesson_id='', *, substitutes_only=False):
        if db.execute('''SELECT 1 FROM effective_lesson_teachers t JOIN lessons l
            ON l.org_id=t.org_id AND l.id=t.lesson_id
            WHERE t.org_id=%s AND t.user_id=ANY(%s) AND l.id<>%s AND l.state='scheduled'
              AND l.starts_at<%s AND l.ends_at>%s AND (NOT %s OR t.is_substitute) LIMIT 1''',
            (org_id, teacher_ids, lesson_id, ends_at, starts_at, substitutes_only)).fetchone():
            raise Problem(409, 'teacher_time_conflict', '教师在此时段已有其他课堂')

    def admin_substitute_lesson(self, token, lesson_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_substitute_lesson', dict(data, lesson_id=lesson_id))
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'lessons', lesson_id)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            org_id, now = actor['org_id'], self.clock()
            self._lock_lesson_schedule(db, org_id)
            lesson = db.execute('''SELECT l.starts_at,l.ends_at,l.state,COALESCE(lv.version,0) AS version
                FROM lessons l LEFT JOIN lesson_versions lv ON lv.org_id=l.org_id AND lv.lesson_id=l.id
                WHERE l.org_id=%s AND l.id=%s FOR UPDATE OF l''', (org_id, lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到该课次')
            if lesson['state'] != 'scheduled' or lesson['starts_at'] <= now:
                raise Problem(409, 'lesson_not_editable', '只能为尚未开始的有效课次安排代课')
            if lesson['version'] != data['expected_version']:
                raise Problem(409, 'lesson_changed', '课次已更新，请刷新后重试')
            current = db.execute('SELECT user_id FROM lesson_substitutions WHERE org_id=%s AND lesson_id=%s',
                                 (org_id, lesson_id)).fetchone()
            teacher_id = data['teacher_id']
            self._scope_teachers(db, actor, [teacher_id] if teacher_id else [])
            previous_id = current['user_id'] if current else None
            if teacher_id == previous_id:
                raise Problem(409, 'substitute_unchanged', '代课安排没有变化')
            if teacher_id is not None:
                teacher = db.execute('''SELECT u.id FROM users u JOIN roles r
                    ON r.org_id=u.org_id AND r.user_id=u.id
                    WHERE u.org_id=%s AND u.id=%s AND u.active AND r.role='teacher' FOR UPDATE OF u''',
                    (org_id, teacher_id)).fetchone()
                if not teacher:
                    raise Problem(409, 'teacher_scope', '请选择本机构的在用教师账号')
                if db.execute('SELECT 1 FROM lesson_teachers WHERE org_id=%s AND lesson_id=%s AND user_id=%s',
                              (org_id, lesson_id, teacher_id)).fetchone():
                    raise Problem(409, 'original_teacher', '请选择其他教师，或恢复原任课安排')
                self._require_teacher_availability(db, org_id, [teacher_id],
                                                   lesson['starts_at'], lesson['ends_at'], lesson_id)
                db.execute('''INSERT INTO lesson_substitutions VALUES(%s,%s,%s)
                    ON CONFLICT(org_id,lesson_id) DO UPDATE SET user_id=EXCLUDED.user_id''',
                    (org_id, lesson_id, teacher_id))
            else:
                original = db.execute('SELECT user_id FROM lesson_teachers WHERE org_id=%s AND lesson_id=%s',
                                      (org_id, lesson_id)).fetchall()
                self._require_teacher_availability(db, org_id, [row['user_id'] for row in original],
                                                   lesson['starts_at'], lesson['ends_at'], lesson_id,
                                                   substitutes_only=True)
                db.execute('DELETE FROM lesson_substitutions WHERE org_id=%s AND lesson_id=%s', (org_id, lesson_id))
            version = lesson['version'] + 1
            db.execute('''INSERT INTO lesson_versions VALUES(%s,%s,%s,%s)
                ON CONFLICT(org_id,lesson_id) DO UPDATE SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (org_id, lesson_id, version, now))
            result = {'lesson_id': lesson_id, 'substitute_id': teacher_id, 'version': version, 'duplicate': False}
            self._audit(db, actor, 'lesson_substitution_changed', lesson_id,
                        {'previous_teacher_id': previous_id, 'teacher_id': teacher_id, 'reason': data['reason']}, now)
            self._save_command(db, actor, reference, digest, result, now)
            return result
        return self.command(token, 'user', run)

    def admin_reschedule_lesson(self, token, lesson_id, data):
        payload = dict(data, lesson_id=lesson_id)
        reference = data['command_ref']
        digest = canonical_digest('admin_reschedule_lesson', payload)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'lessons', lesson_id)
            self._lock_command(db, actor, reference)
            self._lock_lesson_schedule(db, actor['org_id'])
            lesson = db.execute('''SELECT l.id,l.class_id,l.starts_at,l.ends_at,l.state,
                    c.name AS class_name,co.name AS course_name,COALESCE(lv.version,0) AS version
                FROM lessons l JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_versions lv ON lv.org_id=l.org_id AND lv.lesson_id=l.id
                WHERE l.org_id=%s AND l.id=%s FOR UPDATE OF l''',
                (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到该课次')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            self._require_pristine_future_lesson(db, actor, lesson, now)
            if lesson['version'] != data['expected_version']:
                raise Problem(409, 'lesson_changed', '课次已被其他操作更新，请刷新后重新核对')
            if data['starts_at'] <= now:
                raise Problem(409, 'lesson_clock', '新课次必须尚未开始')
            if not 0 < data['ends_at'] - data['starts_at'] <= 12 * 3600:
                raise Problem(422, 'invalid_lesson_time', '结束时间必须晚于开始时间，且时长不能超过 12 小时')
            if data['starts_at'] == lesson['starts_at'] and data['ends_at'] == lesson['ends_at']:
                raise Problem(409, 'lesson_time_unchanged', '课次时间没有变化')
            teachers = db.execute('SELECT user_id,is_substitute FROM effective_lesson_teachers WHERE org_id=%s AND lesson_id=%s',
                                  (actor['org_id'], lesson_id)).fetchall()
            self._require_teacher_availability(db, actor['org_id'], [row['user_id'] for row in teachers],
                                               data['starts_at'], data['ends_at'], lesson_id,
                                               substitutes_only=not any(row['is_substitute'] for row in teachers))
            if db.execute('''SELECT 1 FROM lessons WHERE org_id=%s AND class_id=%s
                AND starts_at=%s AND state='scheduled' AND id<>%s''',
                (actor['org_id'], lesson['class_id'], data['starts_at'], lesson_id)).fetchone():
                raise Problem(409, 'lesson_time_conflict', '该班级在新时间已有有效课次')
            db.execute('UPDATE lessons SET starts_at=%s,ends_at=%s WHERE org_id=%s AND id=%s',
                       (data['starts_at'], data['ends_at'], actor['org_id'], lesson_id))
            version = lesson['version'] + 1
            db.execute('''INSERT INTO lesson_versions(org_id,lesson_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,lesson_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], lesson_id, version, now))
            response = {'accepted': True, 'duplicate': False, 'lesson_id': lesson_id,
                        'class_id': lesson['class_id'], 'class_name': lesson['class_name'],
                        'previous_starts_at': lesson['starts_at'], 'previous_ends_at': lesson['ends_at'],
                        'starts_at': data['starts_at'], 'ends_at': data['ends_at'],
                        'state': 'scheduled', 'previous_version': lesson['version'],
                        'version': version, 'roster_changed': False, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_lesson_rescheduled', lesson_id,
                        {'previous_starts_at': lesson['starts_at'],
                         'previous_ends_at': lesson['ends_at'], 'starts_at': data['starts_at'],
                         'ends_at': data['ends_at'], 'previous_version': lesson['version'],
                         'version': version, 'reason': data['reason'],
                         'roster_changed': False, 'balance_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_cancel_lesson(self, token, lesson_id, data):
        payload = dict(data, lesson_id=lesson_id)
        reference = data['command_ref']
        digest = canonical_digest('admin_cancel_lesson', payload)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'lessons', lesson_id)
            self._lock_command(db, actor, reference)
            lesson = db.execute('''SELECT l.id,l.class_id,l.starts_at,l.ends_at,l.state,
                    c.name AS class_name,COALESCE(lv.version,0) AS version
                FROM lessons l JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_versions lv ON lv.org_id=l.org_id AND lv.lesson_id=l.id
                WHERE l.org_id=%s AND l.id=%s FOR UPDATE OF l''',
                (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到该课次')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            self._require_pristine_future_lesson(db, actor, lesson, self.clock())
            if lesson['version'] != data['expected_version']:
                raise Problem(409, 'lesson_changed', '课次已被其他操作更新，请刷新后重新核对')
            now = self.clock()
            db.execute("UPDATE lessons SET state='cancelled' WHERE org_id=%s AND id=%s",
                       (actor['org_id'], lesson_id))
            version = lesson['version'] + 1
            db.execute('''INSERT INTO lesson_versions(org_id,lesson_id,version,updated_at)
                VALUES(%s,%s,%s,%s) ON CONFLICT(org_id,lesson_id) DO UPDATE
                SET version=EXCLUDED.version,updated_at=EXCLUDED.updated_at''',
                (actor['org_id'], lesson_id, version, now))
            roster_count = db.execute('''SELECT count(*) AS n FROM lesson_students
                WHERE org_id=%s AND lesson_id=%s''', (actor['org_id'], lesson_id)).fetchone()['n']
            response = {'accepted': True, 'duplicate': False, 'lesson_id': lesson_id,
                        'class_id': lesson['class_id'], 'class_name': lesson['class_name'],
                        'starts_at': lesson['starts_at'], 'ends_at': lesson['ends_at'],
                        'state': 'cancelled', 'previous_version': lesson['version'],
                        'version': version, 'roster_count': roster_count,
                        'roster_retained': True, 'report_excluded': True,
                        'hours_reversed': False, 'balance_changed': False,
                        'history_changed': False, 'audited': True}
            self._audit(db, actor, 'admin_lesson_cancelled', lesson_id,
                        {'starts_at': lesson['starts_at'], 'previous_version': lesson['version'],
                         'version': version, 'reason': data['reason'], 'roster_count': roster_count,
                         'roster_retained': True, 'report_excluded': True,
                         'hours_reversed': False, 'balance_changed': False}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def admin_devices(self, token):
        def run(db, actor):
            self._require_campus_operations(actor)
            rows = db.execute('''SELECT d.id,d.name,d.active,d.created_at,
                    c.generation,c.expires_at AS credential_expires_at,
                    (SELECT json_agg(dc.class_id ORDER BY dc.class_id) FROM device_classes dc
                     WHERE dc.org_id=d.org_id AND dc.device_id=d.id) AS class_ids,
                    EXISTS(SELECT 1 FROM device_enrollments e WHERE e.org_id=d.org_id
                        AND e.device_id=d.id AND e.consumed_at IS NULL AND e.expires_at>%s) AS activation_pending
                FROM devices d LEFT JOIN device_credentials c ON c.org_id=d.org_id AND c.device_id=d.id
                WHERE d.org_id=%s AND (%s::text IS NULL OR d.campus_id=%s) ORDER BY d.created_at,d.id''', (self.clock(), actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_create_device(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_device', data)
        code = 'wxa_' + secrets.token_urlsafe(32)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                # The one-time code is deliberately not retained in command receipts.
                prior['activation_code'] = None
                prior['code_recoverable'] = False
                return prior
            now = self.clock()
            class_ids = data['class_ids']
            if not isinstance(class_ids, list) or not 1 <= len(class_ids) <= 200 or len(set(class_ids)) != len(class_ids):
                raise Problem(422, 'invalid_device_scope', '请至少选择一个授权班级')
            found = db.execute('SELECT id,campus_id FROM classes WHERE org_id=%s AND id=ANY(%s)',
                               (actor['org_id'], class_ids)).fetchall()
            if len(found) != len(class_ids):
                raise Problem(403, 'device_scope', '只能授权本机构已有班级')
            campuses = {item['campus_id'] for item in found}
            campus_id = next(iter(campuses)) if len(campuses) == 1 else None
            self._require_campus_match(actor, campus_id)
            if actor.get('managed_access') and campus_id is None:
                raise Problem(422, 'device_campus_required', '设备只能授权给同一校区的班级')
            device_id, generation = uid(), uid()
            db.execute('''INSERT INTO devices(id,org_id,name,active,created_at,token_reissue_required,campus_id)
                VALUES(%s,%s,%s,FALSE,%s,TRUE,%s)''', (device_id, actor['org_id'], data['name'], now, campus_id))
            with db.cursor() as cursor:
                cursor.executemany('INSERT INTO device_classes VALUES(%s,%s,%s)',
                                   [(actor['org_id'], device_id, item) for item in class_ids])
            db.execute('''INSERT INTO device_enrollments
                (id,org_id,device_id,generation,code_hash,issued_by,issued_at,expires_at,consumed_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL)''',
                (uid(), actor['org_id'], device_id, generation, token_digest(code), actor['id'], now, now + 900))
            response = {'protocol': 2, 'device_id': device_id, 'name': data['name'],
                        'class_ids': sorted(class_ids), 'activation_expires_at': now + 900,
                        'activation_required': True, 'duplicate': False}
            self._save_command(db, actor, reference, digest, response, now)
            self._audit(db, actor, 'device_enrollment_issued', device_id,
                        {'class_ids': sorted(class_ids), 'expires_at': now + 900}, now)
            return dict(response, activation_code=code, code_recoverable=True)
        return self.command(token, 'user', run)

    def admin_reissue_device(self, token, device_id, data):
        reference = data['command_ref']
        payload = dict(data, device_id=device_id)
        digest = canonical_digest('admin_reissue_device', payload)
        code = 'wxa_' + secrets.token_urlsafe(32)
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'devices', device_id)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return dict(prior, activation_code=None, code_recoverable=False)
            now = self.clock()
            device = db.execute('SELECT * FROM devices WHERE org_id=%s AND id=%s FOR UPDATE',
                                (actor['org_id'], device_id)).fetchone()
            if not device:
                raise Problem(404, 'device_not_found', '未找到该平板')
            generation = uid()
            db.execute('UPDATE device_credentials SET revoked_at=%s WHERE org_id=%s AND device_id=%s AND revoked_at IS NULL',
                       (now, actor['org_id'], device_id))
            db.execute('UPDATE devices SET active=FALSE,token_reissue_required=TRUE WHERE org_id=%s AND id=%s',
                       (actor['org_id'], device_id))
            db.execute('DELETE FROM device_enrollments WHERE org_id=%s AND device_id=%s AND consumed_at IS NULL',
                       (actor['org_id'], device_id))
            db.execute('''INSERT INTO device_enrollments
                (id,org_id,device_id,generation,code_hash,issued_by,issued_at,expires_at,consumed_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL)''',
                (uid(), actor['org_id'], device_id, generation, token_digest(code), actor['id'], now, now + 900))
            response = {'protocol': 2, 'device_id': device_id, 'name': device['name'],
                        'activation_expires_at': now + 900, 'activation_required': True,
                        'previous_credentials_revoked': True, 'duplicate': False}
            self._save_command(db, actor, reference, digest, response, now)
            self._audit(db, actor, 'device_enrollment_reissued', device_id,
                        {'expires_at': now + 900, 'previous_credentials_revoked': True}, now)
            return dict(response, activation_code=code, code_recoverable=True)
        return self.command(token, 'user', run)

    def admin_disable_device(self, token, device_id, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_disable_device', dict(data, device_id=device_id))
        def run(db, actor):
            self._require_campus_operations(actor)
            self._scope_record(db, actor, 'devices', device_id)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            device = db.execute('SELECT * FROM devices WHERE org_id=%s AND id=%s FOR UPDATE',
                                (actor['org_id'], device_id)).fetchone()
            if not device:
                raise Problem(404, 'device_not_found', '未找到该平板')
            db.execute('UPDATE device_credentials SET revoked_at=%s WHERE org_id=%s AND device_id=%s AND revoked_at IS NULL',
                       (now, actor['org_id'], device_id))
            db.execute('UPDATE devices SET active=FALSE,token_reissue_required=TRUE WHERE org_id=%s AND id=%s',
                       (actor['org_id'], device_id))
            db.execute('DELETE FROM device_enrollments WHERE org_id=%s AND device_id=%s AND consumed_at IS NULL',
                       (actor['org_id'], device_id))
            response = {'device_id': device_id, 'active': False, 'credentials_revoked': True, 'duplicate': False}
            self._save_command(db, actor, reference, digest, response, now)
            self._audit(db, actor, 'device_disabled', device_id, {'credentials_revoked': True}, now)
            return response
        return self.command(token, 'user', run)

    def activate_tablet(self, code, *, expected_org_id=None, expected_device_id=None):
        if not isinstance(code, str) or len(code) > 128:
            raise Problem(401, 'invalid_activation', '开通码无效或已使用')
        if (expected_org_id is None) != (expected_device_id is None):
            raise Problem(422, 'invalid_activation_identity', '换绑须同时提供原机构和设备编号')
        with self.connect() as connection:
            with connection.transaction():
                db = connection
                now = self.clock()
                row = db.execute('''SELECT e.*,d.name FROM device_enrollments e
                    JOIN devices d ON d.org_id=e.org_id AND d.id=e.device_id
                    WHERE e.code_hash=%s AND e.consumed_at IS NULL AND d.active=FALSE
                      AND (%s::text IS NULL OR (e.org_id=%s AND e.device_id=%s))
                    FOR UPDATE OF e,d''',
                    (token_digest(code), expected_org_id, expected_org_id, expected_device_id)).fetchone()
                if not row:
                    raise Problem(401, 'invalid_activation', '开通码无效或已使用')
                if row['expires_at'] <= now:
                    raise Problem(410, 'activation_expired', '开通码已过期，请由管理员重新开通')
                token = new_token('device')
                db.execute('UPDATE device_enrollments SET consumed_at=%s WHERE id=%s', (now, row['id']))
                db.execute('UPDATE devices SET active=TRUE,token_reissue_required=FALSE WHERE org_id=%s AND id=%s',
                           (row['org_id'], row['device_id']))
                db.execute('''INSERT INTO device_credentials
                    (org_id,device_id,generation,token_hash,issued_at,expires_at,revoked_at)
                    VALUES(%s,%s,%s,%s,%s,%s,NULL)
                    ON CONFLICT(org_id,device_id) DO UPDATE SET
                    generation=excluded.generation,token_hash=excluded.token_hash,
                    issued_at=excluded.issued_at,expires_at=excluded.expires_at,revoked_at=NULL''',
                           (row['org_id'], row['device_id'], row['generation'], token_digest(token), now, now + 180 * 86400))
                actor = {'kind': 'device', 'id': row['device_id'], 'org_id': row['org_id']}
                self._audit(db, actor, 'device_activated', row['device_id'],
                            {'generation': row['generation'], 'credential_expires_at': now + 180 * 86400}, now)
                return {'protocol': 2, 'access_token': token, 'org_id': row['org_id'],
                        'device_id': row['device_id'], 'name': row['name'], 'generation': row['generation'],
                        'expires_at': now + 180 * 86400}

    def tablet_bootstrap(self, token):
        def run(db, actor):
            self._require_kind(actor, 'device')
            now = self.clock()
            classes = db.execute('''SELECT c.id,c.name,co.name AS course_name FROM device_classes dc
                JOIN classes c ON c.org_id=dc.org_id AND c.id=dc.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                WHERE dc.org_id=%s AND dc.device_id=%s ORDER BY c.name,c.id''',
                (actor['org_id'], actor['id'])).fetchall()
            rows = db.execute('''SELECT l.id AS lesson_id,l.class_id,l.starts_at,l.ends_at,l.state,
                    ls.student_id,ls.expected,ls.charge_units,s.name AS student_name,s.external_ref,
                    ar.status,ar.revision AS attendance_revision,
                    sb.units AS balance_units,sb.revision AS balance_revision
                FROM device_classes dc
                JOIN lessons l ON l.org_id=dc.org_id AND l.class_id=dc.class_id
                LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                LEFT JOIN students s ON s.org_id=ls.org_id AND s.id=ls.student_id
                LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                    AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                LEFT JOIN student_balances sb ON sb.org_id=ls.org_id AND sb.student_id=ls.student_id
                WHERE dc.org_id=%s AND dc.device_id=%s AND l.ends_at>=%s AND l.starts_at<=%s
                ORDER BY l.starts_at,l.id,s.name,s.id''',
                (actor['org_id'], actor['id'], now - 35 * 86400, now + 120 * 86400)).fetchall()
            lessons = {}
            for row in rows:
                lesson = lessons.setdefault(row['lesson_id'], {
                    'id': row['lesson_id'], 'class_id': row['class_id'],
                    'starts_at': row['starts_at'], 'ends_at': row['ends_at'],
                    'state': row['state'], 'students': []})
                if row['student_id'] is not None:
                    if row['status'] is None or row['balance_units'] is None:
                        raise Problem(409, 'bootstrap_incomplete', '课堂名单尚未完整初始化')
                    lesson['students'].append({
                        'student_id': row['student_id'], 'name': row['student_name'],
                        'external_ref': row['external_ref'],
                        'expected': row['expected'], 'charge_units': row['charge_units'],
                        'status': row['status'], 'attendance_revision': row['attendance_revision'],
                        'balance_units': row['balance_units'], 'balance_revision': row['balance_revision']})
            return {'device': {'id': actor['id'], 'name': actor['name'],
                               'generation': actor['generation']},
                    'snapshot_at': now, 'classes': [dict(row) for row in classes],
                    'lessons': list(lessons.values())}
        return self.command(token, 'device', run)

    def tablet_face_profiles(self, token):
        """Decrypt only active profiles already exposed by this device's roster.

        The pinned native transport supplies authentication. No consent records,
        ciphertext, original photos or unrelated class profiles enter the response.
        """
        def run(db, actor):
            self._require_kind(actor, 'device')
            now = self.clock()
            rows = db.execute('''SELECT DISTINCT fp.id,fp.student_id,fp.model_version,
                    fp.descriptor_ciphertext
                FROM device_classes dc
                JOIN lessons l ON l.org_id=dc.org_id AND l.class_id=dc.class_id
                    AND l.ends_at>=%s AND l.starts_at<=%s
                JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                JOIN students s ON s.org_id=ls.org_id AND s.id=ls.student_id AND s.active=TRUE
                JOIN face_profiles fp ON fp.org_id=s.org_id AND fp.student_id=s.id AND fp.active=TRUE
                WHERE dc.org_id=%s AND dc.device_id=%s
                ORDER BY fp.student_id,fp.id''',
                (now - 35 * 86400, now + 120 * 86400, actor['org_id'], actor['id'])).fetchall()
            if len(rows) > 2000:
                raise Problem(409, 'face_profiles_limit', '人脸档案数量超出单次同步范围，请核对设备班级授权')
            items = []
            if rows:
                cipher = FaceCipher(self.face_key)
                for row in rows:
                    if row['model_version'] != FACE_MODEL:
                        continue
                    items.append({'profile_id': row['id'], 'student_id': row['student_id'],
                                  'model_version': FACE_MODEL,
                                  'descriptors': cipher.decrypt(actor['org_id'], row['student_id'],
                                                               row['descriptor_ciphertext'])})
            return {'protocol': 2, 'org_id': actor['org_id'], 'device_id': actor['id'],
                    'generation': actor['generation'], 'snapshot_at': now,
                    'model_version': FACE_MODEL, 'items': items}
        return self.command(token, 'device', run)

    def tablet_checkin(self, token, data):
        action = 'tablet_checkin'
        reference = data['event_ref']
        digest = canonical_digest(action, data)
        def run(db, actor):
            self._require_kind(actor, 'device')
            self._lock_command(db, actor, reference)
            fact = self._lesson_student(db, actor['org_id'], data['lesson_id'], data['student_id'])
            if not fact:
                raise Problem(404, 'lesson_student_missing', '未找到该课堂学生')
            if not db.execute('''SELECT 1 FROM device_classes
                    WHERE org_id=%s AND device_id=%s AND class_id=%s''',
                    (actor['org_id'], actor['id'], fact['class_id'])).fetchone():
                raise Problem(403, 'checkin_scope', '此平板没有该班级的签到权限')
            # Permission is rechecked even for a duplicate response.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return dict(prior, generation=actor['generation'])
            now = self.clock()
            if fact['state'] != 'scheduled':
                raise Problem(409, 'lesson_unavailable', '该课堂已取消')
            self._require_missing(fact, data['attendance_revision'])
            if data['occurred_at'] > now + 120:
                raise Problem(409, 'device_clock', '签到时间超前，请核对平板时间')
            if datetime.fromtimestamp(data['occurred_at'], CHINA_TZ).date() != datetime.fromtimestamp(fact['starts_at'], CHINA_TZ).date():
                raise Problem(409, 'lesson_day', '签到日期与课堂日期不同，请由教师补签')
            if (fact['balance_revision'] != data['balance_revision']
                    or fact['balance_units'] != data['previous_balance_units']
                    or fact['charge_units'] != data['consumed_units']):
                raise Problem(409, 'balance_changed', '服务器余额或扣课规则已变化，本次未覆盖课时')
            event_id = self._event(db, actor, fact, 'tablet_checkin', reference, None, data['occurred_at'], now)
            db.execute('''UPDATE attendance_records SET status='arrived',revision=%s,last_event_id=%s,updated_at=%s
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s AND revision=%s''',
                (data['attendance_revision'] + 1, event_id, now, actor['org_id'],
                 data['lesson_id'], data['student_id'], data['attendance_revision']))
            units, balance_revision = self._debit(db, actor, fact, 'checkin', reference,
                                                  data['balance_revision'], now, '平板签到扣课时')
            response = dict(protocol=2, org_id=actor['org_id'], device_id=actor['id'],
                            generation=actor['generation'], accepted=True, duplicate=False, event_id=event_id,
                            event_ref=reference, lesson_id=data['lesson_id'], student_id=data['student_id'],
                            status='arrived', attendance_revision=data['attendance_revision'] + 1,
                            balance_revision=balance_revision, balance_units=units,
                            apply_local_debit=False)
            self._audit(db, actor, 'tablet_checkin_accepted', event_id,
                        {'lesson_id': data['lesson_id'], 'student_id': data['student_id'],
                         'consumed_units': data['consumed_units'], 'occurred_at': data['occurred_at']}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'device', run)

    def tablet_resolve_checkin(self, token, data):
        """Archive a blocked local upload only after the web attendance is final.

        This never creates attendance or debit. It returns the current, already
        committed web result so the tablet can retain evidence and resume bootstrap.
        """
        action = 'tablet_resolve_checkin'
        reference = 'resolve:' + data['event_ref']
        digest = canonical_digest(action, data)
        def run(db, actor):
            self._require_kind(actor, 'device')
            self._lock_command(db, actor, reference)
            fact = self._lesson_student(db, actor['org_id'], data['lesson_id'], data['student_id'])
            if not fact:
                raise Problem(404, 'lesson_student_missing', '未找到该课堂学生')
            if not db.execute('''SELECT 1 FROM device_classes
                    WHERE org_id=%s AND device_id=%s AND class_id=%s''',
                    (actor['org_id'], actor['id'], fact['class_id'])).fetchone():
                raise Problem(403, 'checkin_scope', '此平板没有该班级的签到权限')
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return dict(prior, generation=actor['generation'])
            current = db.execute('''SELECT last_event_id FROM attendance_records
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s''',
                (actor['org_id'], data['lesson_id'], data['student_id'])).fetchone()
            if (not fact['expected'] or fact['status'] not in {'arrived', 'leave'}
                    or fact['attendance_revision'] <= data['attendance_revision']
                    or not current or not current['last_event_id']):
                raise Problem(409, 'resolution_pending', '网页尚未完成补签或请假，暂不能归档')
            now = self.clock()
            response = dict(protocol=2, org_id=actor['org_id'], device_id=actor['id'],
                            generation=actor['generation'], accepted=True, resolved=True,
                            duplicate=False, event_ref=data['event_ref'],
                            event_id=current['last_event_id'], lesson_id=data['lesson_id'],
                            student_id=data['student_id'], status=fact['status'],
                            attendance_revision=fact['attendance_revision'],
                            balance_revision=fact['balance_revision'],
                            balance_units=fact['balance_units'], apply_local_debit=False)
            self._audit(db, actor, 'tablet_checkin_resolved', current['last_event_id'],
                        {'event_ref': data['event_ref'], 'lesson_id': data['lesson_id'],
                         'student_id': data['student_id'], 'status': fact['status']}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'device', run)

    def teacher_correction(self, token, lesson_id, kind, data):
        if kind not in {'makeup', 'leave'}:
            raise ValueError('Unsupported correction')
        reference = data['event_ref']
        payload = dict(data, lesson_id=lesson_id, kind=kind)
        digest = canonical_digest('teacher_' + kind, payload)
        def run(db, actor):
            self._require_kind(actor, 'user')
            self._lock_command(db, actor, reference)
            fact = self._lesson_student(db, actor['org_id'], lesson_id, data['student_id'])
            if not fact:
                raise Problem(404, 'lesson_student_missing', '未找到该课堂学生')
            self._teacher_lesson_allowed(db, actor, lesson_id, fact['class_id'])
            # Permission is rechecked even for a duplicate response.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            if fact['state'] != 'scheduled':
                raise Problem(409, 'lesson_unavailable', '该课堂已取消')
            self._require_missing(fact, data['expected_revision'])
            if kind == 'makeup' and now < fact['starts_at']:
                raise Problem(409, 'future_makeup', '课程尚未开始，不能补签')
            event_id = self._event(db, actor, fact, kind, reference, data['reason'], now, now)
            db.execute('''UPDATE attendance_records SET status=%s,revision=%s,last_event_id=%s,updated_at=%s
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s AND revision=%s''',
                ('arrived' if kind == 'makeup' else 'leave', data['expected_revision'] + 1,
                 event_id, now, actor['org_id'], lesson_id, data['student_id'], data['expected_revision']))
            units = fact['balance_units']
            balance_revision = fact['balance_revision']
            if kind == 'makeup':
                units, balance_revision = self._debit(db, actor, fact, 'makeup', reference,
                                                      balance_revision, now, data['reason'])
            response = dict(accepted=True, duplicate=False, event_id=event_id,
                            event_ref=reference, lesson_id=lesson_id, student_id=data['student_id'],
                            status='arrived' if kind == 'makeup' else 'leave',
                            attendance_revision=data['expected_revision'] + 1,
                            balance_revision=balance_revision, balance_units=units)
            self._audit(db, actor, 'teacher_' + kind, event_id,
                        {'lesson_id': lesson_id, 'student_id': data['student_id'], 'reason': data['reason']}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def teacher_adjust_balance(self, token, student_id, data):
        reference = data['command_ref']
        payload = dict(data, student_id=student_id)
        digest = canonical_digest('teacher_balance_adjustment', payload)
        def run(db, actor):
            self._require_kind(actor, 'user')
            self._lock_command(db, actor, reference)
            balance = db.execute('''SELECT sb.units,sb.revision FROM students s
                JOIN student_balances sb ON sb.org_id=s.org_id AND sb.student_id=s.id
                WHERE s.org_id=%s AND s.id=%s FOR UPDATE OF sb''',
                (actor['org_id'], student_id)).fetchone()
            if not balance:
                raise Problem(404, 'student_missing', '未找到学生或期初余额')
            if 'teacher' not in actor['roles'] or not db.execute('''SELECT 1 FROM student_class_enrollments e
                    JOIN teacher_classes tc ON tc.org_id=e.org_id AND tc.class_id=e.class_id
                    WHERE e.org_id=%s AND e.student_id=%s AND e.left_at IS NULL AND tc.user_id=%s LIMIT 1''',
                    (actor['org_id'], student_id, actor['id'])).fetchone():
                raise Problem(403, 'student_scope', '只能调整本人班级学生的课时')
            # Permission is rechecked even for a duplicate response.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if balance['revision'] != data['expected_revision']:
                raise Problem(409, 'balance_changed', '余额已更新，请刷新后重新核对')
            if balance['units'] == data['units']:
                raise Problem(409, 'balance_unchanged', '新余额与当前余额相同')
            now = self.clock()
            delta = data['units'] - balance['units']
            revision = balance['revision'] + 1
            db.execute('''UPDATE student_balances SET units=%s,revision=%s,updated_at=%s
                WHERE org_id=%s AND student_id=%s AND revision=%s''',
                (data['units'], revision, now, actor['org_id'], student_id, data['expected_revision']))
            ledger_id = uid()
            db.execute('''INSERT INTO balance_ledger
                (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
                 previous_revision,revision,actor_type,actor_id,reason,created_at)
                VALUES(%s,%s,%s,'adjustment',%s,%s,%s,%s,%s,%s,'user',%s,%s,%s)''',
                (ledger_id, actor['org_id'], student_id, reference, balance['units'], delta,
                 data['units'], balance['revision'], revision, actor['id'], data['reason'], now))
            response = dict(student_id=student_id, units=data['units'], revision=revision,
                            duplicate=False, ledger_id=ledger_id, audited=True)
            self._audit(db, actor, 'teacher_balance_adjusted', ledger_id,
                        {'student_id': student_id, 'previous_units': balance['units'],
                         'units': data['units'], 'reason': data['reason']}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def teacher_lessons(self, token, day=None):
        def run(db, actor):
            self._require_kind(actor, 'user')
            if 'teacher' not in actor['roles']:
                raise Problem(403, 'teacher_required', '需要教师身份')
            try:
                selected = (datetime.strptime(day, '%Y-%m-%d').date() if day is not None
                            else datetime.fromtimestamp(self.clock(), CHINA_TZ).date())
            except (TypeError, ValueError):
                raise Problem(422, 'invalid_date', '日期格式应为 YYYY-MM-DD') from None
            start = int(datetime.combine(selected, datetime.min.time(), CHINA_TZ).timestamp())
            rows = db.execute('''SELECT l.id,l.class_id,l.starts_at,l.ends_at,l.state,lt.is_substitute,
                    c.name AS class_name,co.name AS course_name,
                    count(ls.student_id) FILTER (WHERE ls.expected) AS expected_count,
                    count(ls.student_id) FILTER (WHERE ar.status='arrived') AS arrived_count,
                    count(ls.student_id) FILTER (WHERE ar.status='leave') AS leave_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='missing') AS missing_count
                FROM effective_lesson_teachers lt
                JOIN lessons l ON l.org_id=lt.org_id AND l.id=lt.lesson_id
                LEFT JOIN teacher_classes tc ON tc.org_id=lt.org_id AND tc.user_id=lt.user_id AND tc.class_id=l.class_id
                JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                    AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                WHERE lt.org_id=%s AND lt.user_id=%s AND l.starts_at>=%s AND l.starts_at<%s
                  AND (lt.is_substitute OR tc.class_id IS NOT NULL)
                GROUP BY l.id,c.id,co.id,lt.is_substitute ORDER BY l.starts_at,l.id''',
                (actor['org_id'], actor['id'], start, start + 86400)).fetchall()
            return {'date': selected.isoformat(), 'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def _weekly_period(self, week_start):
        try:
            selected = (datetime.strptime(week_start, '%Y-%m-%d').date() if week_start is not None
                        else datetime.fromtimestamp(self.clock(), CHINA_TZ).date())
            monday = selected - timedelta(days=selected.weekday())
            if week_start is not None and week_start != monday.isoformat():
                raise ValueError()
        except (TypeError, ValueError):
            raise Problem(422, 'invalid_week', '请选择周一开始的周次') from None
        start = int(datetime.combine(monday, datetime.min.time(), CHINA_TZ).timestamp())
        return monday, start

    def _monthly_period(self, month):
        try:
            selected = (datetime.strptime(month + '-01', '%Y-%m-%d').date() if month is not None
                        else datetime.fromtimestamp(self.clock(), CHINA_TZ).date().replace(day=1))
            if month is not None and selected.strftime('%Y-%m') != month:
                raise ValueError()
        except (TypeError, ValueError):
            raise Problem(422, 'invalid_month', '月份格式应为 YYYY-MM') from None
        next_month = (selected.replace(day=28) + timedelta(days=4)).replace(day=1)
        start = int(datetime.combine(selected, datetime.min.time(), CHINA_TZ).timestamp())
        end = int(datetime.combine(next_month, datetime.min.time(), CHINA_TZ).timestamp())
        return selected, start, end

    def teaching_fees(self, token, period, day=None, *, administrator=False):
        def run(db, actor):
            self._require_kind(actor, 'user')
            if administrator:
                self._require_campus_operations(actor)
            elif 'teacher' not in actor['roles']:
                raise Problem(403, 'teacher_required', '需要教师身份')
            return self._teaching_fee_data(db, actor['org_id'], self.clock(), period, day,
                                           teacher_filter=None if administrator else actor['id'],
                                           campus_id=actor.get('campus_id') if administrator else None)
        return self.command(token, 'user', run)

    def _teaching_fee_data(self, db, org_id, now, period, day, *, teacher_filter=None, campus_id=None):
        try:
            selected = (datetime.strptime(day, '%Y-%m-%d').date() if day is not None
                        else datetime.fromtimestamp(now, CHINA_TZ).date())
            if day is not None and selected.isoformat() != day:
                raise ValueError()
        except (TypeError, ValueError):
            raise Problem(422, 'invalid_date', '日期格式应为 YYYY-MM-DD') from None
        if period == 'week':
            first, start = self._weekly_period((selected - timedelta(days=selected.weekday())).isoformat())
            end = start + 7 * 86400
        else:
            first, start, end = self._monthly_period(selected.isoformat()[:7])
        teachers = db.execute('''SELECT u.id,u.display_name,cp.name AS campus_name
            FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher'
            LEFT JOIN teacher_campuses tc ON tc.org_id=u.org_id AND tc.user_id=u.id
            LEFT JOIN campuses cp ON cp.org_id=tc.org_id AND cp.id=tc.campus_id
            WHERE u.org_id=%s AND (%s::text IS NULL OR u.id=%s)
            ORDER BY u.display_name,u.id''', (org_id, teacher_filter, teacher_filter)).fetchall()
        results = {row['id']: dict(row, total_cents=0, unrated_lessons=0, lessons=[]) for row in teachers}
        rows = db.execute('''SELECT lt.user_id,l.id,l.starts_at,l.ends_at,c.name AS class_name,cp.name AS lesson_campus_name,
                (SELECT rate_cents FROM course_teaching_fee_rates f WHERE f.org_id=l.org_id
                 AND f.course_id=COALESCE(fc.course_id,c.course_id) AND f.effective_at<=l.starts_at
                 ORDER BY f.effective_at DESC,f.version DESC LIMIT 1) AS custom_rate_cents,
                lt.is_substitute,count(ar.student_id) FILTER (WHERE ar.status='arrived') AS arrived_count
            FROM effective_lesson_teachers lt JOIN lessons l ON l.org_id=lt.org_id AND l.id=lt.lesson_id
            JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
            LEFT JOIN campuses cp ON cp.org_id=l.org_id AND cp.id=l.campus_id
            LEFT JOIN lesson_fee_courses fc ON fc.org_id=l.org_id AND fc.lesson_id=l.id
            JOIN roles r ON r.org_id=lt.org_id AND r.user_id=lt.user_id AND r.role='teacher'
            LEFT JOIN attendance_records ar ON ar.org_id=l.org_id AND ar.lesson_id=l.id
            WHERE l.org_id=%s AND l.state='scheduled' AND l.starts_at>=%s AND l.starts_at<%s
              AND l.ends_at<=%s AND (%s::text IS NULL OR lt.user_id=%s)
              AND (%s::text IS NULL OR l.campus_id=%s)
            GROUP BY lt.user_id,l.id,c.id,fc.course_id,cp.id,lt.is_substitute ORDER BY l.starts_at,l.id''',
            (org_id, start, end, now, teacher_filter, teacher_filter, campus_id, campus_id)).fetchall()
        for row in rows:
            minutes, remainder = divmod(row['ends_at'] - row['starts_at'], 60)
            rate = row['custom_rate_cents']
            if rate is None and remainder == 0:
                rate = {90: 300, 120: 450}.get(minutes)
            teacher = results[row['user_id']]
            total = None if rate is None else rate * row['arrived_count']
            if total is None:
                teacher['unrated_lessons'] += 1
            else:
                teacher['total_cents'] += total
            teacher['lessons'].append(dict(row, duration_minutes=minutes, rate_cents=rate, total_cents=total))
        if campus_id is not None:
            results = {key: dict(item, campus_name=None) for key, item in results.items() if item['lessons']}
        closed = now >= end and (campus_id is None or not db.execute('''SELECT 1 FROM lessons
            WHERE org_id=%s AND campus_id=%s AND state='scheduled' AND starts_at>=%s AND starts_at<%s
              AND ends_at>%s LIMIT 1''', (org_id, campus_id, start, end, now)).fetchone())
        return {'period': period, 'start': first.isoformat(), 'closed': closed,
                'end': datetime.fromtimestamp(end - 1, CHINA_TZ).date().isoformat(),
                'total_cents': sum(item['total_cents'] for item in results.values()),
                'items': list(results.values())}

    @staticmethod
    def _weekly_result(monday, now, rows):
        overall = {key: 0 for key in ('expected_count', 'arrived_count', 'leave_count',
                                      'missing_count', 'completed_lessons', 'pending_lessons')}
        classes = []
        for row in rows:
            item = dict(row)
            for key in overall:
                overall[key] += item[key]
            item['rate_percent'] = (round(item['arrived_count'] / item['expected_count'] * 100, 1)
                                    if item['expected_count'] else None)
            classes.append(item)
        overall['rate_percent'] = (round(overall['arrived_count'] / overall['expected_count'] * 100, 1)
                                   if overall['expected_count'] else None)
        start = int(datetime.combine(monday, datetime.min.time(), CHINA_TZ).timestamp())
        return {'week_start': monday.isoformat(), 'week_end': (monday + timedelta(days=6)).isoformat(),
                'timezone': 'Asia/Shanghai', 'generated_at': now, 'final_period': now >= start + 7 * 86400,
                'overall': overall, 'classes': classes}

    def teacher_weekly_report(self, token, week_start=None):
        def run(db, actor):
            self._require_kind(actor, 'user')
            if 'teacher' not in actor['roles']:
                raise Problem(403, 'teacher_required', '需要教师身份')
            monday, start = self._weekly_period(week_start)
            now = self.clock()
            rows = db.execute('''SELECT c.id AS class_id,c.name AS class_name,co.name AS course_name,
                    count(ls.student_id) FILTER (WHERE ls.expected AND l.state='scheduled' AND l.ends_at<=%s) AS expected_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='arrived' AND l.state='scheduled' AND l.ends_at<=%s) AS arrived_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='leave' AND l.state='scheduled' AND l.ends_at<=%s) AS leave_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='missing' AND l.state='scheduled' AND l.ends_at<=%s) AS missing_count,
                    count(DISTINCT l.id) FILTER (WHERE l.state='scheduled' AND l.ends_at<=%s) AS completed_lessons,
                    count(DISTINCT l.id) FILTER (WHERE l.state='scheduled' AND l.ends_at>%s) AS pending_lessons
                FROM effective_lesson_teachers lt
                JOIN lessons l ON l.org_id=lt.org_id AND l.id=lt.lesson_id
                LEFT JOIN teacher_classes tc ON tc.org_id=lt.org_id AND tc.user_id=lt.user_id AND tc.class_id=l.class_id
                JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                JOIN courses co ON co.org_id=c.org_id AND co.id=c.course_id
                LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                    AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                WHERE lt.org_id=%s AND lt.user_id=%s AND l.starts_at>=%s AND l.starts_at<%s
                  AND (lt.is_substitute OR tc.class_id IS NOT NULL)
                GROUP BY c.id,co.id ORDER BY c.name,c.id''',
                (now, now, now, now, now, now, actor['org_id'], actor['id'], start, start + 7 * 86400)).fetchall()
            return self._weekly_result(monday, now, rows)
        return self.command(token, 'user', run)

    def _admin_weekly_report_data(self, db, org_id, monday, start, now):
        rows = db.execute('''SELECT c.id AS class_id,c.name AS class_name,co.name AS course_name,
                count(ls.student_id) FILTER (WHERE ls.expected AND l.state='scheduled' AND l.ends_at<=%s) AS expected_count,
                count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='arrived' AND l.state='scheduled' AND l.ends_at<=%s) AS arrived_count,
                count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='leave' AND l.state='scheduled' AND l.ends_at<=%s) AS leave_count,
                count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='missing' AND l.state='scheduled' AND l.ends_at<=%s) AS missing_count,
                count(DISTINCT l.id) FILTER (WHERE l.state='scheduled' AND l.ends_at<=%s) AS completed_lessons,
                count(DISTINCT l.id) FILTER (WHERE l.state='scheduled' AND l.ends_at>%s) AS pending_lessons
            FROM lessons l
            JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
            JOIN courses co ON co.org_id=l.org_id AND co.id=c.course_id
            LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
            LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
            WHERE l.org_id=%s AND l.starts_at>=%s AND l.starts_at<%s
            GROUP BY c.id,co.id ORDER BY c.name,c.id''',
            (now, now, now, now, now, now, org_id, start, start + 7 * 86400)).fetchall()
        return self._weekly_result(monday, now, rows)

    def admin_weekly_report(self, token, week_start=None):
        def run(db, actor):
            self._require_admin(actor)
            monday, start = self._weekly_period(week_start)
            return self._admin_weekly_report_data(db, actor['org_id'], monday, start, self.clock())
        return self.command(token, 'user', run)

    def _monthly_report_data(self, db, org_id, selected, start, end, now):
        enrolled = db.execute('''SELECT count(*) AS new_students FROM enrollment_records
            WHERE org_id=%s AND occurred_at>=%s AND occurred_at<%s''',
            (org_id, start, end)).fetchone()
        renewed = db.execute('''SELECT count(DISTINCT student_id) AS renewal_students,
                count(*) AS renewal_events,coalesce(sum(units_added),0) AS renewal_units,
                coalesce(sum(amount_cents),0) AS renewal_amount_cents
            FROM renewal_records WHERE org_id=%s AND occurred_at>=%s AND occurred_at<%s''',
            (org_id, start, end)).fetchone()
        enrollments = db.execute('''SELECT e.id,e.student_id,s.name AS student_name,e.class_id,
                c.name AS class_name,e.occurred_at,e.source_ref,u.display_name AS actor_name
            FROM enrollment_records e
            JOIN students s ON s.org_id=e.org_id AND s.id=e.student_id
            JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
            JOIN users u ON u.org_id=e.org_id AND u.id=e.actor_id
            WHERE e.org_id=%s AND e.occurred_at>=%s AND e.occurred_at<%s
            ORDER BY e.occurred_at,e.id LIMIT 1001''', (org_id, start, end)).fetchall()
        renewals = db.execute('''SELECT r.id,r.student_id,s.name AS student_name,
                r.course_id,co.name AS course_name,r.units_added,r.amount_cents,r.unit_price_cents,
                r.occurred_at,r.source_ref,r.reason,u.display_name AS actor_name
            FROM renewal_records r
            JOIN students s ON s.org_id=r.org_id AND s.id=r.student_id
            JOIN courses co ON co.org_id=r.org_id AND co.id=r.course_id
            JOIN users u ON u.org_id=r.org_id AND u.id=r.actor_id
            WHERE r.org_id=%s AND r.occurred_at>=%s AND r.occurred_at<%s
            ORDER BY r.occurred_at,r.id LIMIT 1001''', (org_id, start, end)).fetchall()
        truncated = len(enrollments) > 1000 or len(renewals) > 1000
        overall = dict(enrolled)
        overall.update(dict(renewed))
        next_month = (selected.replace(day=28) + timedelta(days=4)).replace(day=1)
        return {'month': selected.strftime('%Y-%m'), 'month_start': selected.isoformat(),
                'month_end': (next_month - timedelta(days=1)).isoformat(),
                'timezone': 'Asia/Shanghai', 'generated_at': now, 'final_period': now >= end,
                'overall': overall, 'enrollments': [dict(row) for row in enrollments[:1000]],
                'renewals': [dict(row) for row in renewals[:1000]], 'details_truncated': truncated}

    def admin_monthly_report(self, token, month=None):
        def run(db, actor):
            self._require_admin(actor)
            selected, start, end = self._monthly_period(month)
            return self._monthly_report_data(db, actor['org_id'], selected, start, end, self.clock())
        return self.command(token, 'user', run)


    def _normalize_report_period(self, kind, period_start):
        if kind == 'weekly':
            return self._weekly_period(period_start)[0].isoformat()
        if kind == 'monthly':
            return self._monthly_period(period_start)[0].strftime('%Y-%m')
        raise Problem(422, 'invalid_report_kind', '报表类型仅支持 weekly 或 monthly')

    def _report_payload(self, db, org_id, kind, period_start, now):
        if kind == 'weekly':
            monday, start = self._weekly_period(period_start)
            return self._admin_weekly_report_data(db, org_id, monday, start, now)
        if kind == 'monthly':
            selected, start, end = self._monthly_period(period_start)
            return self._monthly_report_data(db, org_id, selected, start, end, now)
        raise Problem(422, 'invalid_report_kind', '报表类型仅支持 weekly 或 monthly')

    @staticmethod
    def _report_metadata(row):
        return {'id': row['id'], 'kind': row['kind'], 'period_start': row['period_start'],
                'version': row['version'], 'status': row['status'], 'error': row['error'],
                'requested_by': row['requested_by'], 'requested_by_name': row.get('requested_by_name'),
                'request_source': 'manual' if row['requested_by'] else 'automatic',
                'final_period': row.get('final_period'), 'created_at': row['created_at'],
                'completed_at': row['completed_at']}

    @staticmethod
    def _report_row(db, org_id, run_id):
        return db.execute('''SELECT r.*,u.display_name AS requested_by_name,
                (r.payload->>'final_period')::boolean AS final_period
            FROM report_runs r LEFT JOIN users u ON u.org_id=r.org_id AND u.id=r.requested_by
            WHERE r.org_id=%s AND r.id=%s''', (org_id, run_id)).fetchone()

    def admin_report_runs(self, token, kind, period_start):
        def run(db, actor):
            self._require_admin(actor)
            period = self._normalize_report_period(kind, period_start)
            rows = db.execute('''SELECT r.id,r.kind,r.period_start,r.version,r.status,r.error,
                    r.requested_by,u.display_name AS requested_by_name,r.created_at,r.completed_at,
                    (r.payload->>'final_period')::boolean AS final_period
                FROM report_runs r LEFT JOIN users u ON u.org_id=r.org_id AND u.id=r.requested_by
                WHERE r.org_id=%s AND r.kind=%s AND r.period_start=%s
                ORDER BY r.version DESC''', (actor['org_id'], kind, period)).fetchall()
            return {'kind': kind, 'period_start': period,
                    'items': [self._report_metadata(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_report_run(self, token, run_id):
        def run(db, actor):
            self._require_admin(actor)
            row = self._report_row(db, actor['org_id'], run_id)
            if not row:
                raise Problem(404, 'report_missing', '未找到该报表版本')
            return {'run': self._report_metadata(row), 'payload': row['payload']}
        return self.command(token, 'user', run)

    def _validate_report_period_not_future(self, kind, period_start):
        today = datetime.fromtimestamp(self.clock(), CHINA_TZ).date()
        current = (today - timedelta(days=today.weekday())).isoformat() if kind == 'weekly' else today.strftime('%Y-%m')
        if period_start > current:
            raise Problem(422, 'report_period_future', '不能生成尚未开始的报表周期')

    @staticmethod
    def _session_report_lock(db, key, unlock=False):
        function = 'pg_advisory_unlock' if unlock else 'pg_advisory_lock'
        db.execute('SELECT ' + function + '(hashtextextended(%s,0))', (key,))

    def _generate_report(self, kind, period_start, *, token=None, reference=None, digest=None,
                         retry_run_id=None, automatic=False, org_id=None):
        """Persist running, then complete/failed in separate transactions.

        Session locks cover the whole multi-transaction operation. Therefore an
        identical retry waits for the original receipt, and concurrent generators
        cannot allocate the same period version.
        """
        locks = []
        with self.connect() as connection:
            try:
                with connection.transaction():
                    if automatic:
                        actor = {'kind': 'system', 'id': None, 'org_id': org_id}
                    else:
                        actor = self.authenticate(connection, token, 'user')
                        self._require_admin(actor)
                if not automatic:
                    command_key = json.dumps([actor['org_id'], actor['kind'], actor['id'], reference],
                                             ensure_ascii=True, separators=(',', ':'))
                    with connection.transaction():
                        self._session_report_lock(connection, command_key)
                    locks.append(command_key)
                with connection.transaction():
                    if not automatic:
                        actor = self.authenticate(connection, token, 'user')
                        self._require_admin(actor)
                        prior = self._prior(connection, actor, reference, digest)
                        if prior:
                            return prior
                    if retry_run_id is not None:
                        previous = connection.execute('''SELECT * FROM report_runs
                            WHERE org_id=%s AND id=%s FOR UPDATE''',
                            (actor['org_id'], retry_run_id)).fetchone()
                        if not previous:
                            raise Problem(404, 'report_missing', '未找到待重试的报表版本')
                        if previous['status'] != 'failed':
                            raise Problem(409, 'report_retry_conflict', '仅失败的报表版本可以重试')
                        kind, period_start = previous['kind'], previous['period_start']
                    period_start = self._normalize_report_period(kind, period_start)
                    self._validate_report_period_not_future(kind, period_start)
                period_key = json.dumps(['report', actor['org_id'], kind, period_start],
                                        ensure_ascii=True, separators=(',', ':'))
                with connection.transaction():
                    self._session_report_lock(connection, period_key)
                locks.append(period_key)
                now = self.clock()
                with connection.transaction():
                    latest = connection.execute('''SELECT * FROM report_runs
                        WHERE org_id=%s AND kind=%s AND period_start=%s
                        ORDER BY version DESC LIMIT 1''',
                        (actor['org_id'], kind, period_start)).fetchone()
                    if automatic and latest and latest['status'] in {'complete', 'running'}:
                        row = self._report_row(connection, actor['org_id'], latest['id'])
                        return {'accepted': False, 'duplicate': True, 'skipped': True,
                                'run': self._report_metadata(row), 'payload': None,
                                'retryable': False, 'audited': False}
                    if latest and latest['status'] == 'running':
                        raise Problem(409, 'report_running', '该周期已有报表正在生成，请稍后刷新')
                    run_id = uid()
                    version = latest['version'] + 1 if latest else 1
                    connection.execute('''INSERT INTO report_runs
                        (id,org_id,kind,period_start,version,status,payload,error,requested_by,created_at,completed_at)
                        VALUES(%s,%s,%s,%s,%s,'running',NULL,NULL,%s,%s,NULL)''',
                        (run_id, actor['org_id'], kind, period_start, version,
                         None if automatic else actor['id'], now))
                try:
                    with connection.transaction():
                        payload = self._report_payload(connection, actor['org_id'], kind,
                                                       period_start, self.clock())
                    with connection.transaction():
                        completed_at = self.clock()
                        connection.execute('''UPDATE report_runs SET status='complete',payload=%s,
                            error=NULL,completed_at=%s WHERE org_id=%s AND id=%s AND status='running' ''',
                            (Jsonb(payload), completed_at, actor['org_id'], run_id))
                        row = self._report_row(connection, actor['org_id'], run_id)
                        response = {'accepted': True, 'duplicate': False, 'skipped': False,
                                    'run': self._report_metadata(row), 'payload': payload,
                                    'retryable': False, 'audited': True}
                        self._audit(connection, actor,
                                    'report_generation_succeeded' if automatic else 'admin_report_generation_succeeded',
                                    run_id, {'kind': kind, 'period_start': period_start, 'version': version,
                                             'source': 'automatic' if automatic else 'manual',
                                             'retry_of': retry_run_id}, completed_at)
                        if not automatic:
                            self._save_command(connection, actor, reference, digest, response, completed_at)
                    return response
                except Exception:
                    # The failed run is committed independently of the aborted calculation.
                    with connection.transaction():
                        completed_at = self.clock()
                        connection.execute('''UPDATE report_runs SET status='failed',payload=NULL,
                            error='report_generation_failed',completed_at=%s
                            WHERE org_id=%s AND id=%s AND status='running' ''',
                            (completed_at, actor['org_id'], run_id))
                        row = self._report_row(connection, actor['org_id'], run_id)
                        response = {'accepted': True, 'duplicate': False, 'skipped': False,
                                    'run': self._report_metadata(row), 'payload': None,
                                    'retryable': True, 'audited': True}
                        self._audit(connection, actor,
                                    'report_generation_failed' if automatic else 'admin_report_generation_failed',
                                    run_id, {'kind': kind, 'period_start': period_start, 'version': version,
                                             'source': 'automatic' if automatic else 'manual',
                                             'retry_of': retry_run_id, 'error': 'report_generation_failed'}, completed_at)
                        if not automatic:
                            self._save_command(connection, actor, reference, digest, response, completed_at)
                    return response
            finally:
                for key in reversed(locks):
                    try:
                        with connection.transaction():
                            self._session_report_lock(connection, key, True)
                    except Exception:
                        # Closing the connection also releases every session lock.
                        pass

    def admin_generate_report(self, token, data):
        digest = canonical_digest('admin_generate_report', data)
        return self._generate_report(data['kind'], data['period_start'], token=token,
                                     reference=data['command_ref'], digest=digest)

    def admin_retry_report(self, token, run_id, data):
        digest = canonical_digest('admin_retry_report', {'run_id': run_id, **data})
        return self._generate_report(None, None, token=token, reference=data['command_ref'],
                                     digest=digest, retry_run_id=run_id)

    def generate_automatic_report(self, org_id, kind, period_start):
        return self._generate_report(kind, period_start, automatic=True, org_id=org_id)

    def teacher_lesson(self, token, lesson_id):
        def run(db, actor):
            lesson = db.execute('SELECT * FROM lessons WHERE org_id=%s AND id=%s',
                                (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到课堂')
            self._teacher_lesson_allowed(db, actor, lesson_id, lesson['class_id'])
            students = db.execute('''SELECT ls.student_id,s.name,ls.expected,ls.charge_units,
                    ar.status,ar.revision AS attendance_revision,sb.units AS balance_units,
                    sb.revision AS balance_revision
                FROM lesson_students ls JOIN students s ON s.org_id=ls.org_id AND s.id=ls.student_id
                JOIN attendance_records ar ON ar.org_id=ls.org_id AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                JOIN student_balances sb ON sb.org_id=ls.org_id AND sb.student_id=ls.student_id
                WHERE ls.org_id=%s AND ls.lesson_id=%s ORDER BY s.name,s.id''',
                (actor['org_id'], lesson_id)).fetchall()
            return {'lesson': dict(lesson), 'students': [dict(row) for row in students]}
        return self.command(token, 'user', run)

    def admin_face_profile(self, token, student_id, data):
        reference = data['command_ref']
        payload = dict(data, student_id=student_id)
        digest = canonical_digest('admin_face_profile', payload)
        def run(db, actor):
            self._require_admin(actor)
            self._lock_command(db, actor, reference)
            student = db.execute('''SELECT id,name FROM students
                WHERE org_id=%s AND id=%s AND active=TRUE FOR UPDATE''',
                (actor['org_id'], student_id)).fetchone()
            if not student:
                raise Problem(404, 'student_missing', '未找到在读学生')
            # Authorization and student existence are rechecked even for retries.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if data['model_version'] != FACE_MODEL:
                raise Problem(409, 'face_model_mismatch', '人脸模型版本不一致，请刷新页面后重新采集')
            frames = validate_frames(data['descriptors'])
            cipher = FaceCipher(self.face_key)
            now = self.clock()
            retired = db.execute('''UPDATE face_profiles SET active=FALSE,retired_at=%s
                WHERE org_id=%s AND student_id=%s AND active=TRUE''',
                (now, actor['org_id'], student_id)).rowcount
            profile_id = uid()
            encrypted = cipher.encrypt(actor['org_id'], student_id, frames, secrets.token_bytes(12))
            db.execute('''INSERT INTO face_profiles
                (id,org_id,student_id,model_version,descriptor_ciphertext,consent_ref,active,created_at,retired_at)
                VALUES(%s,%s,%s,%s,%s,%s,TRUE,%s,NULL)''',
                (profile_id, actor['org_id'], student_id, FACE_MODEL, encrypted,
                 data['consent_ref'], now))
            response = {'profile_id': profile_id, 'student_id': student_id,
                        'student_name': student['name'], 'model_version': FACE_MODEL,
                        'sample_count': len(frames), 'active': True,
                        'replaced_profiles': retired, 'duplicate': False}
            self._audit(db, actor, 'admin_face_profile_saved', profile_id,
                        {'student_id': student_id, 'model_version': FACE_MODEL,
                         'sample_count': len(frames), 'consent_ref': data['consent_ref'],
                         'replaced_profiles': retired}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def teacher_face_checkin(self, token, lesson_id, data):
        reference = data['event_ref']
        payload = dict(data, lesson_id=lesson_id)
        digest = canonical_digest('teacher_face_checkin', payload)
        def run(db, actor):
            self._require_kind(actor, 'user')
            self._lock_command(db, actor, reference)
            lesson = db.execute('SELECT * FROM lessons WHERE org_id=%s AND id=%s',
                                (actor['org_id'], lesson_id)).fetchone()
            if not lesson:
                raise Problem(404, 'lesson_missing', '未找到课堂')
            self._teacher_lesson_allowed(db, actor, lesson_id, lesson['class_id'])
            # Permission is rechecked before returning an idempotent response.
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            now = self.clock()
            if lesson['state'] != 'scheduled':
                raise Problem(409, 'lesson_unavailable', '该课堂已取消')
            if now < lesson['starts_at']:
                raise Problem(409, 'future_checkin', '课程尚未开始，不能人脸签到')
            if datetime.fromtimestamp(now, CHINA_TZ).date() != datetime.fromtimestamp(lesson['starts_at'], CHINA_TZ).date():
                raise Problem(409, 'lesson_day', '人脸签到仅限课堂当天，其他情况请使用补签')
            if data['model_version'] != FACE_MODEL:
                raise Problem(409, 'face_model_mismatch', '人脸模型版本不一致，请刷新页面后重新采集')
            frames = validate_frames(data['descriptors'])
            rows = db.execute('''SELECT fp.student_id,s.name AS student_name,
                    fp.model_version,fp.descriptor_ciphertext
                FROM face_profiles fp
                JOIN lesson_students ls ON ls.org_id=fp.org_id AND ls.student_id=fp.student_id
                JOIN students s ON s.org_id=fp.org_id AND s.id=fp.student_id
                WHERE fp.org_id=%s AND ls.lesson_id=%s AND ls.expected=TRUE
                  AND fp.active=TRUE AND s.active=TRUE
                ORDER BY fp.student_id FOR UPDATE OF fp''',
                (actor['org_id'], lesson_id)).fetchall()
            cipher = FaceCipher(self.face_key)
            candidates = []
            for row in rows:
                if row['model_version'] != FACE_MODEL:
                    continue
                candidates.append({'student_id': row['student_id'],
                                   'student_name': row['student_name'],
                                   'frames': cipher.decrypt(actor['org_id'], row['student_id'],
                                                            row['descriptor_ciphertext'])})
            matched = match_face(frames, candidates)
            fact = self._lesson_student(db, actor['org_id'], lesson_id, matched['student_id'])
            if not fact:
                raise Problem(404, 'lesson_student_missing', '未找到该课堂学生')
            self._require_missing(fact, fact['attendance_revision'])
            event_id = self._event(db, actor, fact, 'web_face_checkin', reference,
                                   '教师网页人脸识别签到', now, now)
            attendance_revision = fact['attendance_revision'] + 1
            db.execute('''UPDATE attendance_records SET status='arrived',revision=%s,
                last_event_id=%s,updated_at=%s
                WHERE org_id=%s AND lesson_id=%s AND student_id=%s AND revision=%s''',
                (attendance_revision, event_id, now, actor['org_id'], lesson_id,
                 matched['student_id'], fact['attendance_revision']))
            units, balance_revision = self._debit(db, actor, fact, 'checkin', reference,
                                                  fact['balance_revision'], now,
                                                  '教师网页人脸签到扣课时')
            response = dict(accepted=True, duplicate=False, event_id=event_id,
                            event_ref=reference, lesson_id=lesson_id,
                            student_id=matched['student_id'], student_name=matched['student_name'],
                            status='arrived', attendance_revision=attendance_revision,
                            balance_revision=balance_revision, balance_units=units,
                            model_version=FACE_MODEL, match_score=matched['score'])
            self._audit(db, actor, 'teacher_web_face_checkin', event_id,
                        {'lesson_id': lesson_id, 'student_id': matched['student_id'],
                         'model_version': FACE_MODEL, 'match_score': matched['score'],
                         'match_margin': matched['margin'], 'consumed_units': fact['charge_units']}, now)
            self._save_command(db, actor, reference, digest, response, now)
            return response
        return self.command(token, 'user', run)

    def me(self, token):
        def run(db, actor):
            result = {key: actor[key] for key in ('id', 'org_id', 'kind')}
            if actor['kind'] == 'user':
                result.update(login=actor['login'], display_name=actor['display_name'], roles=sorted(actor['roles']))
                if actor['staff_role']:
                    result.update(campus_id=actor['campus_id'], campus_name=actor['staff_campus_name'])
                if 'teacher' in actor['roles']:
                    campus = db.execute('''SELECT c.name FROM teacher_campuses tc
                        JOIN campuses c ON c.org_id=tc.org_id AND c.id=tc.campus_id
                        WHERE tc.org_id=%s AND tc.user_id=%s''', (actor['org_id'], actor['id'])).fetchone()
                    result['campus_name'] = campus['name'] if campus else None
            else:
                result['name'] = actor['name']
            return result
        return self.command(token, None, run)

    def logout(self, token):
        def run(db, actor):
            self._require_kind(actor, 'user')
            db.execute('UPDATE auth_sessions SET revoked_at=%s WHERE token_hash=%s AND revoked_at IS NULL',
                       (self.clock(), actor['session_digest']))
            return {'logged_out': True}
        return self.command(token, 'user', run)
