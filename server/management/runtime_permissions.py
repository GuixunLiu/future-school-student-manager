"""Campus boundaries and the fee review workflow for the unified runtime."""
from psycopg import sql
from psycopg.types.json import Jsonb

from business.security import identifier, password_hash
from business.store import Problem, uid


def provision_first_owner(db, organization, login):
    """Assign an existing administrator from a trusted maintenance session."""
    with db.transaction():
        org = db.execute('SELECT id FROM organizations WHERE slug=%s FOR UPDATE',
                         (identifier(organization),)).fetchone()
        if not org:
            raise ValueError('Organization does not exist')
        user = db.execute('''SELECT u.id FROM users u JOIN roles r
            ON r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin'
            JOIN user_credentials c ON c.org_id=u.org_id AND c.user_id=u.id
            WHERE u.org_id=%s AND u.login=%s AND u.active''', (org['id'], identifier(login))).fetchone()
        if not user:
            raise ValueError('Choose an active existing administrator with a password')
        current = db.execute("SELECT user_id FROM organization_staff_access WHERE org_id=%s AND role='owner'",
                             (org['id'],)).fetchone()
        if current:
            if current['user_id'] == user['id']:
                return
            raise ValueError('An owner is already assigned')
        db.execute("INSERT INTO organization_staff_access VALUES(%s,%s,'owner',NULL)", (org['id'], user['id']))
        db.execute('''INSERT INTO audit_log(org_id,actor_type,actor_id,action,entity_id,detail,created_at)
            VALUES(%s,'user',%s,'staff_owner_provisioned',%s,%s,EXTRACT(EPOCH FROM clock_timestamp())::BIGINT)''',
            (org['id'], user['id'], user['id'], Jsonb({'source': 'maintenance'})))


class CampusPermissions:
    @staticmethod
    def _require_owner(actor):
        if 'owner' not in actor['roles']:
            raise Problem(403, 'owner_required', '仅老板可以执行此操作')

    @staticmethod
    def _require_campus_operations(actor):
        if not {'admin', 'campus_admin'} & actor['roles']:
            raise Problem(403, 'admin_required', '需要校区管理员权限')

    @staticmethod
    def _require_campus_match(actor, campus_id):
        if actor.get('campus_id') is not None and actor['campus_id'] != campus_id:
            raise Problem(404, 'campus_scope', '在当前校区找不到该资料')

    def _scope_record(self, db, actor, table, record_id):
        if actor.get('campus_id') is None:
            return
        row = db.execute(sql.SQL('SELECT campus_id FROM {} WHERE org_id=%s AND id=%s').format(sql.Identifier(table)),
                         (actor['org_id'], record_id)).fetchone()
        self._require_campus_match(actor, row['campus_id'] if row else None)

    @staticmethod
    def _scope_teachers(db, actor, teacher_ids):
        if actor.get('campus_id') is None or not teacher_ids:
            return
        rows = db.execute('''SELECT user_id FROM teacher_campuses
            WHERE org_id=%s AND campus_id=%s AND user_id=ANY(%s)''',
            (actor['org_id'], actor['campus_id'], list(teacher_ids))).fetchall()
        if {row['user_id'] for row in rows} != set(teacher_ids):
            raise Problem(403, 'teacher_campus_scope', '跨校区教师安排须由老板处理')

    @staticmethod
    def _scope_student_write(db, actor, student_id):
        if actor.get('campus_id') is None:
            return
        rows = db.execute('''SELECT DISTINCT c.campus_id FROM student_class_enrollments e
            JOIN classes c ON c.org_id=e.org_id AND c.id=e.class_id
            WHERE e.org_id=%s AND e.student_id=%s AND e.left_at IS NULL''',
            (actor['org_id'], student_id)).fetchall()
        campuses = {row['campus_id'] for row in rows}
        if actor['campus_id'] not in campuses:
            raise Problem(404, 'campus_scope', '在当前校区找不到该学生')
        if len(campuses) != 1:
            raise Problem(403, 'shared_student', '跨校区学生资料须由老板处理')

    @staticmethod
    def _campus_exists(db, org_id, campus_id):
        row = db.execute('SELECT id,name FROM campuses WHERE org_id=%s AND id=%s',
                         (org_id, campus_id)).fetchone()
        if not row:
            raise Problem(404, 'campus_missing', '未找到本机构的校区')
        return row

    def staff_access(self, token):
        def run(db, actor):
            self._require_owner(actor)
            rows = db.execute('''SELECT u.id,u.login,u.display_name,u.active,a.role,a.campus_id,c.name AS campus_name
                FROM users u LEFT JOIN organization_staff_access a ON a.org_id=u.org_id AND a.user_id=u.id
                LEFT JOIN campuses c ON c.org_id=a.org_id AND c.id=a.campus_id
                WHERE u.org_id=%s AND (a.role IS NOT NULL OR EXISTS(SELECT 1 FROM roles r
                    WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role='admin'))
                ORDER BY a.role,u.display_name,u.id''', (actor['org_id'],)).fetchall()
            return {'items': rows}
        return self.command(token, 'user', run)

    def owner_access_audit(self, token):
        def run(db, actor):
            self._require_owner(actor)
            rows = db.execute('''SELECT a.action,a.entity_id,a.created_at,u.display_name AS operator,
                    a.detail->>'reason' AS reason
                FROM audit_log a LEFT JOIN users u ON u.org_id=a.org_id AND u.id=a.actor_id
                WHERE a.org_id=%s AND (a.action IN ('staff_owner_provisioned','staff_access_set','class_campus_assigned',
                    'course_teaching_fee_changed','course_fee_proposed','course_fee_proposal_approved',
                    'course_fee_proposal_returned','teaching_fees_submitted','teaching_fees_returned',
                    'teaching_fees_approved','teaching_fees_settled','lesson_substitution_changed'))
                ORDER BY a.created_at DESC,a.seq DESC LIMIT 100''', (actor['org_id'],)).fetchall()
            return {'items': rows}
        return self.command(token, 'user', run)

    def set_staff_access(self, token, data):
        def run(db, actor):
            self._require_owner(actor)
            org_id, role, campus_id = actor['org_id'], data['role'], data['campus_id']
            if (role == 'campus_admin') != (campus_id is not None):
                raise Problem(422, 'staff_campus_required', '校区管理员须绑定一个校区')
            if campus_id:
                self._campus_exists(db, org_id, campus_id)
            login = identifier(data['login'])
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('runtime-login:' + login,))
            user = db.execute('SELECT id,active FROM users WHERE org_id=%s AND login=%s FOR UPDATE',
                              (org_id, login)).fetchone()
            now = self.clock()
            if user:
                if db.execute("""SELECT 1 FROM organization_staff_access WHERE org_id=%s AND user_id=%s AND role='owner'
                    UNION ALL SELECT 1 FROM roles WHERE org_id=%s AND user_id=%s AND role='teacher'""",
                    (org_id, user['id'], org_id, user['id'])).fetchone():
                    raise Problem(409, 'staff_account_protected', '请使用单独的管理账号')
                if data.get('password') or data.get('display_name'):
                    raise Problem(409, 'login_exists', '账号已存在，请只调整权限')
                user_id = user['id']
            else:
                if role is None or not data.get('password') or not data.get('display_name'):
                    raise Problem(422, 'staff_account_required', '新账号需要姓名、密码和权限')
                if db.execute('SELECT 1 FROM users WHERE login=%s', (login,)).fetchone():
                    raise Problem(409, 'login_exists', '这个登录账号已被使用')
                user_id = uid()
                db.execute('''INSERT INTO users(id,org_id,login,display_name,active,created_at,password_reset_required)
                    VALUES(%s,%s,%s,%s,TRUE,%s,FALSE)''', (user_id, org_id, login, data['display_name'], now))
                db.execute('INSERT INTO user_credentials VALUES(%s,%s,%s,%s)',
                           (org_id, user_id, password_hash(data['password']), now))
            if role is None:
                db.execute('DELETE FROM organization_staff_access WHERE org_id=%s AND user_id=%s', (org_id, user_id))
            else:
                db.execute('''INSERT INTO organization_staff_access VALUES(%s,%s,%s,%s)
                    ON CONFLICT(org_id,user_id) DO UPDATE SET role=EXCLUDED.role,campus_id=EXCLUDED.campus_id''',
                    (org_id, user_id, role, campus_id))
            self._revoke_user_sessions(db, org_id, user_id, now)
            self._audit(db, actor, 'staff_access_set', user_id,
                        {'role': role, 'campus_id': campus_id, 'reason': data['reason']}, now)
            return {'user_id': user_id, 'login': login, 'role': role, 'campus_id': campus_id}
        return self.command(token, 'user', run)

    def assign_class_campus(self, token, class_id, data):
        def run(db, actor):
            self._require_owner(actor)
            org_id, campus_id, now = actor['org_id'], data['campus_id'], self.clock()
            self._campus_exists(db, org_id, campus_id)
            self._lock_lesson_schedule(db, org_id)
            row = db.execute('SELECT campus_id FROM classes WHERE org_id=%s AND id=%s FOR UPDATE',
                             (org_id, class_id)).fetchone()
            if not row:
                raise Problem(404, 'class_missing', '未找到班级')
            if row['campus_id'] not in (None, campus_id):
                raise Problem(409, 'class_campus_locked', '已有校区的班级须通过新的班级安排转移')
            db.execute('UPDATE classes SET campus_id=%s WHERE org_id=%s AND id=%s', (campus_id, org_id, class_id))
            lessons = db.execute('''UPDATE lessons SET campus_id=%s WHERE org_id=%s AND class_id=%s AND campus_id IS NULL
                AND (%s OR starts_at>%s)''', (campus_id, org_id, class_id, data['include_history'], now)).rowcount
            # Existing devices remain unassigned until every granted class belongs to the same campus.
            devices = db.execute('''UPDATE devices d SET campus_id=%s WHERE d.org_id=%s AND d.campus_id IS NULL
                AND EXISTS(SELECT 1 FROM device_classes x WHERE x.org_id=d.org_id AND x.device_id=d.id AND x.class_id=%s)
                AND NOT EXISTS(SELECT 1 FROM device_classes x JOIN classes c ON c.org_id=x.org_id AND c.id=x.class_id
                    WHERE x.org_id=d.org_id AND x.device_id=d.id AND c.campus_id IS DISTINCT FROM %s)''',
                (campus_id, org_id, class_id, campus_id)).rowcount
            self._audit(db, actor, 'class_campus_assigned', class_id, {'campus_id': campus_id,
                        'include_history': data['include_history'], 'reason': data['reason']}, now)
            return {'campus_id': campus_id, 'lessons_assigned': lessons, 'devices_assigned': devices}
        return self.command(token, 'user', run)

    def course_fee_proposals(self, token):
        def run(db, actor):
            if not {'owner', 'campus_admin'} & actor['roles']:
                raise Problem(403, 'fee_proposal_required', '需要费率申请权限')
            rows = db.execute('''SELECT p.id,p.rate_cents,p.fee_version,p.status,p.reason,p.decision_reason,
                    c.name AS course_name,cp.name AS campus_name
                FROM course_fee_proposals p JOIN courses c ON c.org_id=p.org_id AND c.id=p.course_id
                JOIN campuses cp ON cp.org_id=p.org_id AND cp.id=p.campus_id
                WHERE p.org_id=%s AND (%s::text IS NULL OR p.campus_id=%s)
                ORDER BY p.submitted_at DESC,p.id''',
                (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': rows}
        return self.command(token, 'user', run)

    def submit_course_fee_proposal(self, token, course_id, data):
        def run(db, actor):
            if 'campus_admin' not in actor['roles']:
                raise Problem(403, 'campus_admin_required', '仅校区管理员可以申请调整费率')
            org_id, campus_id, now = actor['org_id'], actor['campus_id'], self.clock()
            reference = data['command_ref']
            digest = self._course_fee_proposal_digest(course_id, data)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, digest)
            if prior:
                return prior
            if not db.execute('SELECT 1 FROM classes WHERE org_id=%s AND campus_id=%s AND course_id=%s',
                              (org_id, campus_id, course_id)).fetchone():
                raise Problem(404, 'course_missing', '本校区没有使用该课程')
            row = db.execute('''INSERT INTO course_fee_proposals(org_id,id,campus_id,course_id,rate_cents,
                    fee_version,status,reason,submitted_by,submitted_at)
                VALUES(%s,%s,%s,%s,%s,%s,'submitted',%s,%s,%s)
                ON CONFLICT(org_id,campus_id,course_id) WHERE status='submitted' DO NOTHING RETURNING id,status''',
                (org_id, uid(), campus_id, course_id, data['rate_cents'], data['expected_version'],
                 data['reason'], actor['id'], now)).fetchone()
            if not row:
                raise Problem(409, 'fee_proposal_pending', '这门课程已有待处理的费率申请')
            result = dict(row, duplicate=False)
            self._audit(db, actor, 'course_fee_proposed', row['id'], {'course_id': course_id, 'campus_id': campus_id,
                        'rate_cents': data['rate_cents'], 'reason': data['reason']}, now)
            self._save_command(db, actor, reference, digest, result, now)
            return result
        return self.command(token, 'user', run)

    def decide_course_fee_proposal(self, token, proposal_id, data):
        def run(db, actor):
            self._require_owner(actor)
            org_id, action = actor['org_id'], data['action']
            proposal = db.execute('SELECT * FROM course_fee_proposals WHERE org_id=%s AND id=%s FOR UPDATE',
                                  (org_id, proposal_id)).fetchone()
            if not proposal or proposal['status'] != 'submitted':
                raise Problem(409, 'fee_proposal_changed', '费率申请已处理，请刷新后重试')
            if action == 'approved':
                self._write_course_teaching_fee(db, actor, proposal['course_id'], {
                    'rate_cents': proposal['rate_cents'], 'expected_version': proposal['fee_version'],
                    'reason': data['reason']}, self.clock())
            db.execute('UPDATE course_fee_proposals SET status=%s,decision_reason=%s WHERE org_id=%s AND id=%s',
                       (action, data['reason'], org_id, proposal_id))
            self._audit(db, actor, 'course_fee_proposal_' + action, proposal_id,
                        {'course_id': proposal['course_id'], 'reason': data['reason']}, self.clock())
            return {'id': proposal_id, 'status': action}
        return self.command(token, 'user', run)

    def fee_reviews(self, token, *, include_snapshots=True):
        def run(db, actor):
            self._require_fee_review_role(actor)
            fields = sql.SQL(',f.snapshot' if include_snapshots else '')
            rows = db.execute(sql.SQL('''SELECT f.id,f.campus_id,c.name AS campus_name,f.period,f.period_start,
                    f.status,f.version,f.reason,f.updated_at{}
                FROM teaching_fee_reviews f JOIN campuses c ON c.org_id=f.org_id AND c.id=f.campus_id
                WHERE f.org_id=%s AND (%s::text IS NULL OR f.campus_id=%s)
                ORDER BY f.period_start DESC,c.name,f.period''').format(fields),
                (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            campuses = db.execute('''SELECT id,name FROM campuses WHERE org_id=%s
                AND (%s::text IS NULL OR id=%s) ORDER BY name,id''',
                (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': rows, 'campuses': campuses}
        return self.command(token, 'user', run)

    def fee_review_detail(self, token, review_id):
        def run(db, actor):
            self._require_fee_review_role(actor)
            campus_id = actor.get('campus_id')
            row = db.execute('''SELECT version,snapshot FROM teaching_fee_reviews
                WHERE org_id=%s AND id=%s AND (%s::text IS NULL OR campus_id=%s)''',
                (actor['org_id'], review_id, campus_id, campus_id)).fetchone()
            if not row:
                raise Problem(404, 'review_missing', '未找到审核单')
            return row
        return self.command(token, 'user', run)

    def _fee_review_snapshot(self, db, org_id, now, period, date, campus_id):
        data = self._teaching_fee_data(db, org_id, now, period, date, campus_id=campus_id)
        lesson_fields = ('id', 'starts_at', 'class_name', 'lesson_campus_name', 'is_substitute',
                         'duration_minutes', 'arrived_count', 'rate_cents', 'total_cents')
        data['items'] = [
            {**{key: item[key] for key in ('id', 'display_name', 'total_cents', 'unrated_lessons')},
             'lessons': [{key: lesson[key] for key in lesson_fields} for lesson in item['lessons']]}
            for item in data['items']
        ]
        return data

    def preview_fee_review(self, token, campus_id, period, date):
        def run(db, actor):
            self._require_fee_review_role(actor)
            self._require_campus_match(actor, campus_id)
            self._campus_exists(db, actor['org_id'], campus_id)
            return self._fee_review_snapshot(db, actor['org_id'], self.clock(), period, date, campus_id)
        return self.command(token, 'user', run)

    @staticmethod
    def _require_fee_review_role(actor):
        if not {'owner', 'campus_admin', 'performance_reviewer'} & actor['roles']:
            raise Problem(403, 'review_required', '需要课时费审核权限')

    def submit_fee_review(self, token, data):
        def run(db, actor):
            if not {'owner', 'campus_admin'} & actor['roles']:
                raise Problem(403, 'campus_admin_required', '仅校区管理员或老板可以提交审核')
            org_id, campus_id, period = actor['org_id'], data['campus_id'], data['period']
            self._require_campus_match(actor, campus_id)
            self._campus_exists(db, org_id, campus_id)
            now = self.clock()
            snapshot = self._fee_review_snapshot(db, org_id, now, period, data['date'], campus_id)
            if not snapshot['closed']:
                raise Problem(409, 'review_period_open', '统计周期结束后才能提交')
            if any(item['unrated_lessons'] for item in snapshot['items']):
                raise Problem(409, 'review_rates_missing', '仍有课次缺少费率，请核对后再提交')
            if not snapshot['items']:
                raise Problem(409, 'review_empty', '本期没有可提交的授课记录')
            row = db.execute('''INSERT INTO teaching_fee_reviews VALUES(%s,%s,%s,%s,%s,'submitted',1,%s,%s,%s,%s)
                ON CONFLICT(org_id,campus_id,period,period_start) DO UPDATE SET status='submitted',
                    version=teaching_fee_reviews.version+1,snapshot=EXCLUDED.snapshot,updated_by=EXCLUDED.updated_by,
                    updated_at=EXCLUDED.updated_at,reason=EXCLUDED.reason
                WHERE teaching_fee_reviews.status='returned' RETURNING id,status,version''',
                (org_id, uid(), campus_id, period, snapshot['start'], Jsonb(snapshot), actor['id'], now, data['reason'])).fetchone()
            if not row:
                raise Problem(409, 'review_exists', '这个校区的本期明细已提交')
            self._audit(db, actor, 'teaching_fees_submitted', row['id'], {'version': row['version'],
                        'campus_id': campus_id, 'reason': data['reason']}, now)
            return row
        return self.command(token, 'user', run)

    def decide_fee_review(self, token, review_id, data):
        def run(db, actor):
            action = data['action']
            role = 'owner' if action == 'settled' else 'performance_reviewer'
            if role not in actor['roles']:
                raise Problem(403, 'review_role_required', '当前身份无法执行这个审核操作')
            org_id, now = actor['org_id'], self.clock()
            row = db.execute('SELECT * FROM teaching_fee_reviews WHERE org_id=%s AND id=%s FOR UPDATE',
                             (org_id, review_id)).fetchone()
            if not row:
                raise Problem(404, 'review_missing', '未找到审核单')
            allowed_states = {'approved': {'submitted'}, 'returned': {'submitted', 'approved'}, 'settled': {'approved'}}
            if row['version'] != data['expected_version'] or row['status'] not in allowed_states[action]:
                raise Problem(409, 'review_changed', '审核状态已变更，请刷新后重试')
            if action == 'settled' and row['period'] != 'month':
                raise Problem(409, 'review_month_required', '仅月度审核单可以确认结算')
            if action == 'settled':
                current = self._fee_review_snapshot(db, org_id, now, 'month', row['period_start'], row['campus_id'])
                if row['snapshot'] != current:
                    raise Problem(409, 'review_snapshot_changed', '课时费明细已变化，请由审核员退回后重新提交')
            version = row['version'] + 1
            db.execute('''UPDATE teaching_fee_reviews SET status=%s,version=%s,updated_by=%s,updated_at=%s,reason=%s
                WHERE org_id=%s AND id=%s''', (action, version, actor['id'], now, data['reason'], org_id, review_id))
            self._audit(db, actor, 'teaching_fees_' + action, review_id,
                        {'version': version, 'reason': data['reason'], 'campus_id': row['campus_id']}, now)
            return {'id': review_id, 'status': action, 'version': version}
        return self.command(token, 'user', run)
