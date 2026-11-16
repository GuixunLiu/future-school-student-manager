"""Owner balance corrections and campus requests; teachers never edit balances."""
import hashlib
import json

from business.store import Problem, uid


def digest(action, data):
    return hashlib.sha256(json.dumps({'action': action, 'data': data},
        ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class BalancePermissions:
    @staticmethod
    def _lock_balance_workflow(db, org_id, student_id):
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('balance-workflow:'+org_id+':'+student_id,))

    @staticmethod
    def _balance_input(data):
        if type(data['units']) is not int or not -1_000_000 <= data['units'] <= 10_000_000:
            raise Problem(422, 'balance_limit', '请输入允许范围内的课时')
        if not data['reason'].strip():
            raise Problem(422, 'balance_reason_required', '请填写调整原因')

    @staticmethod
    def _lock_balance(db, org_id, student_id):
        balance = db.execute('''SELECT sb.units,sb.revision FROM student_balances sb
            WHERE sb.org_id=%s AND sb.student_id=%s FOR UPDATE''', (org_id, student_id)).fetchone()
        if not balance:
            raise Problem(404, 'student_missing', '未找到学生或已核对的期初余额')
        return balance

    def _apply_balance_adjustment(self, db, actor, student_id, balance, data, reference):
        if balance['revision'] != data['expected_revision']:
            raise Problem(409, 'balance_changed', '余额已更新，请刷新后重新核对')
        if balance['units'] == data['units']:
            raise Problem(409, 'balance_unchanged', '新余额与当前余额相同')
        now, ledger_id = self.clock(), uid()
        revision = balance['revision'] + 1
        db.execute('''UPDATE student_balances SET units=%s,revision=%s,updated_at=%s
            WHERE org_id=%s AND student_id=%s''', (data['units'], revision, now, actor['org_id'], student_id))
        db.execute('''INSERT INTO balance_ledger
            (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
             previous_revision,revision,actor_type,actor_id,reason,created_at)
            VALUES(%s,%s,%s,'adjustment',%s,%s,%s,%s,%s,%s,'user',%s,%s,%s)''',
            (ledger_id, actor['org_id'], student_id, reference, balance['units'],
             data['units']-balance['units'], data['units'], balance['revision'], revision,
             actor['id'], data['reason'], now))
        self._audit(db, actor, 'owner_balance_adjusted', ledger_id,
                    {'student_id': student_id, 'previous_units': balance['units'],
                     'units': data['units'], 'reason': data['reason']}, now)
        return dict(student_id=student_id, units=data['units'], revision=revision,
                    duplicate=False, ledger_id=ledger_id, audited=True)

    def owner_adjust_balance(self, token, student_id, data):
        reference = data['command_ref']
        hashed = digest('owner_balance', dict(data, student_id=student_id))
        def run(db, actor):
            self._require_owner(actor)
            self._balance_input(data)
            self._lock_command(db, actor, reference)
            prior = self._prior(db, actor, reference, hashed)
            if prior:
                return prior
            balance = self._lock_balance(db, actor['org_id'], student_id)
            result = self._apply_balance_adjustment(db, actor, student_id, balance, data, reference)
            self._save_command(db, actor, reference, hashed, result, self.clock())
            return result
        return self.command(token, 'user', run)

    def balance_requests(self, token):
        def run(db, actor):
            if not {'owner', 'campus_admin'} & actor['roles']:
                raise Problem(403, 'balance_request_required', '需要校区管理员或老板权限')
            rows = db.execute('''SELECT r.id,r.student_id,s.name AS student_name,s.external_ref,
                    r.campus_id,c.name AS campus_name,r.units,r.previous_units,r.balance_revision,
                    r.reason,r.status,r.version,r.requested_at,u.display_name AS requested_by_name,
                    r.decision_reason
                FROM balance_adjustment_requests r JOIN students s ON s.org_id=r.org_id AND s.id=r.student_id
                JOIN campuses c ON c.org_id=r.org_id AND c.id=r.campus_id
                JOIN users u ON u.org_id=r.org_id AND u.id=r.requested_by
                WHERE r.org_id=%s AND (%s::text IS NULL OR r.campus_id=%s)
                ORDER BY r.requested_at DESC,r.id''',
                (actor['org_id'], actor.get('campus_id'), actor.get('campus_id'))).fetchall()
            return {'items': rows}
        return self.command(token, 'user', run)

    def submit_balance_request(self, token, student_id, data):
        reference = data['command_ref']
        hashed = digest('balance_request', dict(data, student_id=student_id))
        def run(db, actor):
            if 'campus_admin' not in actor['roles']:
                raise Problem(403, 'campus_admin_required', '仅校区管理员可以提交调整申请')
            self._balance_input(data)
            self._lock_command(db, actor, reference)
            self._lock_balance_workflow(db, actor['org_id'], student_id)
            db.execute('SELECT id FROM students WHERE org_id=%s AND id=%s FOR NO KEY UPDATE',
                       (actor['org_id'], student_id))
            self._scope_student_write(db, actor, student_id)
            prior = self._prior(db, actor, reference, hashed)
            if prior:
                return prior
            balance = self._lock_balance(db, actor['org_id'], student_id)
            if balance['revision'] != data['expected_revision']:
                raise Problem(409, 'balance_changed', '余额已变化，请刷新后重新申请')
            if balance['units'] == data['units']:
                raise Problem(409, 'balance_unchanged', '申请课时与当前余额相同')
            result = db.execute('''INSERT INTO balance_adjustment_requests
                (org_id,id,student_id,campus_id,requested_by,requested_at,previous_units,units,
                 balance_revision,reason,status,version)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',1)
                ON CONFLICT(org_id,student_id) WHERE status='pending' DO NOTHING RETURNING id,status,version''',
                (actor['org_id'], uid(), student_id, actor['campus_id'], actor['id'], self.clock(),
                 balance['units'], data['units'], balance['revision'], data['reason'])).fetchone()
            if not result:
                raise Problem(409, 'balance_request_pending', '该学生已有待处理的调整申请')
            result = dict(result, duplicate=False, balance_changed=False)
            self._audit(db, actor, 'balance_adjustment_requested', result['id'],
                        {'student_id': student_id, 'campus_id': actor['campus_id'],
                         'units': data['units'], 'reason': data['reason']}, self.clock())
            self._save_command(db, actor, reference, hashed, result, self.clock())
            return result
        return self.command(token, 'user', run)

    def decide_balance_request(self, token, request_id, data):
        def run(db, actor):
            self._require_owner(actor)
            if data['action'] not in {'approved', 'rejected'} or not data['reason'].strip():
                raise Problem(422, 'invalid_balance_decision', '请核对审批状态并填写说明')
            target = db.execute('SELECT student_id FROM balance_adjustment_requests WHERE org_id=%s AND id=%s',
                                (actor['org_id'], request_id)).fetchone()
            if not target:
                raise Problem(404, 'balance_request_missing', '未找到调整申请')
            self._lock_balance_workflow(db, actor['org_id'], target['student_id'])
            row = db.execute('''SELECT * FROM balance_adjustment_requests
                WHERE org_id=%s AND id=%s FOR UPDATE''', (actor['org_id'], request_id)).fetchone()
            if not row:
                raise Problem(404, 'balance_request_missing', '未找到调整申请')
            if row['status'] != 'pending' or row['version'] != data['expected_version']:
                raise Problem(409, 'balance_request_changed', '申请状态已变化，请刷新后核对')
            result = None
            if data['action'] == 'approved':
                balance = self._lock_balance(db, actor['org_id'], row['student_id'])
                result = self._apply_balance_adjustment(db, actor, row['student_id'], balance,
                    {'units': row['units'], 'expected_revision': row['balance_revision'],
                     'reason': row['reason']+'；审批说明：'+data['reason']}, 'balance-request:'+request_id)
            db.execute('''UPDATE balance_adjustment_requests SET status=%s,version=version+1,
                decided_by=%s,decided_at=%s,decision_reason=%s WHERE org_id=%s AND id=%s''',
                (data['action'], actor['id'], self.clock(), data['reason'], actor['org_id'], request_id))
            self._audit(db, actor, 'balance_adjustment_'+data['action'], request_id,
                        {'student_id': row['student_id'], 'reason': data['reason']}, self.clock())
            return {'id': request_id, 'status': data['action'], 'version': row['version']+1,
                    'balance': result}
        return self.command(token, 'user', run)
