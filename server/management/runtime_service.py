"""PostgreSQL runtime transactions for tablet check-in and scoped teachers.

This service is deliberately separate from the SQLite candidate management
system. It never creates a schema, provisions credentials, or targets a server
implicitly; the caller supplies an initialized runtime database connection.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from business.security import new_token, token_digest, valid_token, verify_password
from business.store import Problem, uid


CHINA_TZ = timezone(timedelta(hours=8))
MIN_UNITS = -1_000_000
MAX_UNITS = 10_000_000


def canonical_digest(action, payload):
    raw = json.dumps({'action': action, 'payload': payload}, ensure_ascii=True,
                     sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class RuntimeStore:
    def __init__(self, conninfo, clock=None):
        self.conninfo = conninfo
        self.clock = clock or (lambda: int(time.time()))

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
            row = db.execute('''SELECT u.id,u.org_id,u.login,u.display_name,s.id AS session_id
                FROM auth_sessions s JOIN users u ON u.org_id=s.org_id AND u.id=s.user_id
                WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at>%s AND u.active=TRUE''',
                (digest, now)).fetchone()
            if row:
                roles = {r['role'] for r in db.execute(
                    'SELECT role FROM roles WHERE org_id=%s AND user_id=%s', (row['org_id'], row['id']))}
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
        allowed = db.execute('''SELECT 1 FROM lesson_teachers lt
            JOIN teacher_classes tc ON tc.org_id=lt.org_id AND tc.user_id=lt.user_id
            WHERE lt.org_id=%s AND lt.user_id=%s AND lt.lesson_id=%s AND tc.class_id=%s''',
            (actor['org_id'], actor['id'], lesson_id, class_id)).fetchone()
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
        buckets = [('ip:' + token_digest(source), 30), ('account:' + token_digest(organization + ':' + login), 5)]
        result, failure = None, None
        with self.connect() as connection:
            with connection.transaction():
                db = connection
                db.execute('DELETE FROM runtime_login_limits WHERE window_start<%s AND blocked_until<%s', (now - 86400, now))
                limits = [(bucket, maximum, db.execute('SELECT * FROM runtime_login_limits WHERE bucket=%s', (bucket,)).fetchone()) for bucket, maximum in buckets]
                if any(row and row['blocked_until'] > now for _, _, row in limits):
                    failure = Problem(429, 'login_limited', '尝试过于频繁，请稍后再试')
                else:
                    user = db.execute('''SELECT u.*,c.password_hash FROM organizations o
                        JOIN users u ON u.org_id=o.id LEFT JOIN user_credentials c ON c.org_id=u.org_id AND c.user_id=u.id
                        WHERE o.slug=%s AND u.login=%s''', (organization, login)).fetchone()
                    valid = verify_password(password, user['password_hash'] if user else None)
                    if not valid or not user or not user['active']:
                        for bucket, maximum, row in limits:
                            fresh = not row or now - row['window_start'] >= 300
                            count, start = (1, now) if fresh else (row['failures'] + 1, row['window_start'])
                            blocked = now + 300 if count >= maximum else 0
                            db.execute('''INSERT INTO runtime_login_limits VALUES(%s,%s,%s,%s)
                                ON CONFLICT(bucket) DO UPDATE SET failures=excluded.failures,
                                window_start=excluded.window_start,blocked_until=excluded.blocked_until''', (bucket, count, start, blocked))
                        failure = Problem(401, 'invalid_login', '机构、账号或密码不正确')
                    else:
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
            raise Problem(403, 'admin_required', '仅管理员可以开通平板')

    def admin_classes(self, token):
        def run(db, actor):
            self._require_admin(actor)
            rows = db.execute('SELECT id,name,course_name FROM classes WHERE org_id=%s ORDER BY name,id', (actor['org_id'],)).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_devices(self, token):
        def run(db, actor):
            self._require_admin(actor)
            rows = db.execute('''SELECT d.id,d.name,d.active,d.created_at,
                    c.generation,c.expires_at AS credential_expires_at,
                    (SELECT json_agg(dc.class_id ORDER BY dc.class_id) FROM device_classes dc
                     WHERE dc.org_id=d.org_id AND dc.device_id=d.id) AS class_ids,
                    EXISTS(SELECT 1 FROM device_enrollments e WHERE e.org_id=d.org_id
                        AND e.device_id=d.id AND e.consumed_at IS NULL AND e.expires_at>%s) AS activation_pending
                FROM devices d LEFT JOIN device_credentials c ON c.org_id=d.org_id AND c.device_id=d.id
                WHERE d.org_id=%s ORDER BY d.created_at,d.id''', (self.clock(), actor['org_id'])).fetchall()
            return {'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

    def admin_create_device(self, token, data):
        reference = data['command_ref']
        digest = canonical_digest('admin_create_device', data)
        code = 'wxa_' + secrets.token_urlsafe(32)
        def run(db, actor):
            self._require_admin(actor)
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
            found = db.execute('SELECT id FROM classes WHERE org_id=%s AND id=ANY(%s)',
                               (actor['org_id'], class_ids)).fetchall()
            if len(found) != len(class_ids):
                raise Problem(403, 'device_scope', '只能授权本机构已有班级')
            device_id, generation = uid(), uid()
            db.execute('INSERT INTO devices VALUES(%s,%s,%s,FALSE,%s,TRUE)',
                       (device_id, actor['org_id'], data['name'], now))
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
            self._require_admin(actor)
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
            self._require_admin(actor)
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

    def activate_tablet(self, code):
        if not isinstance(code, str) or len(code) > 128:
            raise Problem(401, 'invalid_activation', '开通码无效或已使用')
        with self.connect() as connection:
            with connection.transaction():
                db = connection
                now = self.clock()
                row = db.execute('''SELECT e.*,d.name FROM device_enrollments e
                    JOIN devices d ON d.org_id=e.org_id AND d.id=e.device_id
                    WHERE e.code_hash=%s AND e.consumed_at IS NULL AND d.active=FALSE
                    FOR UPDATE OF e,d''', (token_digest(code),)).fetchone()
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
            classes = db.execute('''SELECT c.id,c.name,c.course_name FROM device_classes dc
                JOIN classes c ON c.org_id=dc.org_id AND c.id=dc.class_id
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
                return prior
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
            balance = db.execute('''SELECT s.class_id,sb.units,sb.revision FROM students s
                JOIN student_balances sb ON sb.org_id=s.org_id AND sb.student_id=s.id
                WHERE s.org_id=%s AND s.id=%s FOR UPDATE OF sb''',
                (actor['org_id'], student_id)).fetchone()
            if not balance:
                raise Problem(404, 'student_missing', '未找到学生或期初余额')
            if 'teacher' not in actor['roles'] or not db.execute('''SELECT 1 FROM teacher_classes
                    WHERE org_id=%s AND user_id=%s AND class_id=%s''',
                    (actor['org_id'], actor['id'], balance['class_id'])).fetchone():
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
            rows = db.execute('''SELECT l.id,l.class_id,l.starts_at,l.ends_at,l.state,
                    c.name AS class_name,c.course_name,
                    count(ls.student_id) FILTER (WHERE ls.expected) AS expected_count,
                    count(ls.student_id) FILTER (WHERE ar.status='arrived') AS arrived_count,
                    count(ls.student_id) FILTER (WHERE ar.status='leave') AS leave_count,
                    count(ls.student_id) FILTER (WHERE ls.expected AND ar.status='missing') AS missing_count
                FROM lesson_teachers lt
                JOIN lessons l ON l.org_id=lt.org_id AND l.id=lt.lesson_id
                JOIN teacher_classes tc ON tc.org_id=lt.org_id AND tc.user_id=lt.user_id AND tc.class_id=l.class_id
                JOIN classes c ON c.org_id=l.org_id AND c.id=l.class_id
                LEFT JOIN lesson_students ls ON ls.org_id=l.org_id AND ls.lesson_id=l.id
                LEFT JOIN attendance_records ar ON ar.org_id=ls.org_id
                    AND ar.lesson_id=ls.lesson_id AND ar.student_id=ls.student_id
                WHERE lt.org_id=%s AND lt.user_id=%s AND l.starts_at>=%s AND l.starts_at<%s
                GROUP BY l.id,c.id ORDER BY l.starts_at,l.id''',
                (actor['org_id'], actor['id'], start, start + 86400)).fetchall()
            return {'date': selected.isoformat(), 'items': [dict(row) for row in rows]}
        return self.command(token, 'user', run)

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

    def me(self, token):
        def run(db, actor):
            result = {key: actor[key] for key in ('id', 'org_id', 'kind')}
            if actor['kind'] == 'user':
                result.update(login=actor['login'], display_name=actor['display_name'], roles=sorted(actor['roles']))
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
