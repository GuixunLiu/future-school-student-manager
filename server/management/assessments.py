"""Versioned assessment materials, explicit periods and audited deadline exceptions.

Candidate only. Deadlines are copied into periods; edits to defaults never rewrite
history. A returned item remains deadline-locked until an administrator unlocks it.
Scores are manually recorded with a basis, never generated from an invented rubric.
"""
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from business.store import Problem, uid
from .approvals import ApprovalStore, digest, encoded, label
from .deadlines import DEFAULTS, Rule, deadline, may_edit, submission_status
from .policy import require, workspace

KINDS = frozenset((*DEFAULTS, 'enrolment_list', 'renewal_list'))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS assessment_rules (
 org_id TEXT NOT NULL, kind TEXT NOT NULL, rule TEXT NOT NULL,
 revision INTEGER NOT NULL, PRIMARY KEY(org_id,kind)
);
CREATE TABLE IF NOT EXISTS assessment_periods (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, kind TEXT NOT NULL,
 starts_on TEXT NOT NULL, due_at INTEGER NOT NULL, rule_snapshot TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(org_id,kind,starts_on), UNIQUE(org_id,id)
);
CREATE TABLE IF NOT EXISTS assessment_materials (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, period_id TEXT NOT NULL,
 owner_id TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL CHECK(state IN ('draft','submitted','returned')),
 revision INTEGER NOT NULL DEFAULT 0, submitted_at INTEGER,
 unlocked_until INTEGER, updated_at INTEGER NOT NULL,
 UNIQUE(org_id,period_id,owner_id), UNIQUE(org_id,id),
 FOREIGN KEY(org_id,period_id) REFERENCES assessment_periods(org_id,id),
 FOREIGN KEY(org_id,owner_id) REFERENCES users(org_id,id)
);
CREATE TABLE IF NOT EXISTS assessment_versions (
 org_id TEXT NOT NULL, material_id TEXT NOT NULL, revision INTEGER NOT NULL,
 content TEXT NOT NULL, submitted_at INTEGER NOT NULL, late INTEGER NOT NULL,
 PRIMARY KEY(org_id,material_id,revision),
 FOREIGN KEY(org_id,material_id) REFERENCES assessment_materials(org_id,id)
);
CREATE TABLE IF NOT EXISTS assessment_scores (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, material_id TEXT NOT NULL,
 material_revision INTEGER NOT NULL, value TEXT NOT NULL, basis TEXT NOT NULL,
 recorded_by TEXT NOT NULL, recorded_at INTEGER NOT NULL,
 FOREIGN KEY(org_id,material_id,material_revision)
 REFERENCES assessment_versions(org_id,material_id,revision)
);
CREATE TABLE IF NOT EXISTS assessment_commands (
 org_id TEXT NOT NULL, actor_id TEXT NOT NULL, command_ref TEXT NOT NULL,
 digest TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(org_id,actor_id,command_ref)
);
CREATE TRIGGER IF NOT EXISTS assessment_versions_no_update BEFORE UPDATE ON assessment_versions
 BEGIN SELECT RAISE(ABORT,'immutable submission'); END;
CREATE TRIGGER IF NOT EXISTS assessment_versions_no_delete BEFORE DELETE ON assessment_versions
 BEGIN SELECT RAISE(ABORT,'immutable submission'); END;
CREATE TRIGGER IF NOT EXISTS assessment_scores_no_update BEFORE UPDATE ON assessment_scores
 BEGIN SELECT RAISE(ABORT,'immutable score'); END;
CREATE TRIGGER IF NOT EXISTS assessment_scores_no_delete BEFORE DELETE ON assessment_scores
 BEGIN SELECT RAISE(ABORT,'immutable score'); END;
'''


def revision_number(value):
    if type(value) is not int or value < 0:
        raise Problem(422, 'invalid_revision', '资料版本不正确')
    return value


class AssessmentStore(ApprovalStore):
    def initialize(self):
        super().initialize()
        db = self.connect()
        try:
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
        finally:
            db.close()

    def once(self, db, actor, role, ref, payload, operation):
        ref = label(ref, 80)
        hashed = digest({'role': role, **payload})
        old = db.execute('SELECT digest,result FROM assessment_commands WHERE org_id=? AND actor_id=? AND command_ref=?',
                         (actor['org_id'], actor['id'], ref)).fetchone()
        if old:
            if old['digest'] != hashed:
                raise Problem(409, 'idempotency_conflict', '操作编号已用于其他内容')
            return {**json.loads(old['result']), 'duplicate': True}
        result = operation()
        db.execute('INSERT INTO assessment_commands VALUES(?,?,?,?,?)',
                   (actor['org_id'], actor['id'], ref, hashed, encoded(result)))
        return {**result, 'duplicate': False}

    def configure_rule(self, token, role, *, kind, rule, expected_revision, command_ref):
        revision_number(expected_revision)
        if kind not in DEFAULTS:
            raise Problem(422, 'custom_period_required', '招生、续费名单请指定实际周期与截止时间')
        try:
            validated = Rule(**rule)
            if validated.frequency != DEFAULTS[kind].frequency:
                raise ValueError('frequency mismatch')
        except (ValueError, TypeError):
            raise Problem(422, 'invalid_deadline_rule', '截止规则不正确')
        canonical = asdict(validated)
        def run(db, actor):
            selected = workspace(actor, role)
            require(actor, selected, 'settings.manage')
            def apply():
                old = db.execute('SELECT revision FROM assessment_rules WHERE org_id=? AND kind=?', (actor['org_id'], kind)).fetchone()
                if (old['revision'] if old else 0) != expected_revision:
                    raise Problem(409, 'rule_changed', '截止规则已更新，请刷新')
                db.execute('INSERT INTO assessment_rules VALUES(?,?,?,?) ON CONFLICT(org_id,kind) DO UPDATE SET rule=excluded.rule,revision=excluded.revision',
                           (actor['org_id'], kind, encoded(canonical), expected_revision + 1))
                self.record_audit(db, actor, selected, 'assessment_rule_changed', kind, rule=canonical, revision=expected_revision + 1)
                return {'kind': kind, 'rule': canonical, 'revision': expected_revision + 1}
            return self.once(db, actor, selected, command_ref, dict(action='rule', kind=kind, rule=canonical, revision=expected_revision), apply)
        return self.command(token, run)

    def open_period(self, token, role, *, kind, starts_on, owner_ids, command_ref, due_at=None):
        if kind not in KINDS:
            raise Problem(422, 'invalid_kind', '考核项目不正确')
        try:
            start = date.fromisoformat(starts_on)
            if start.isoformat() != starts_on:
                raise ValueError('noncanonical date')
        except (ValueError, TypeError):
            raise Problem(422, 'invalid_period', '请填写正确的周期开始日期')
        if not isinstance(owner_ids, list) or not 1 <= len(owner_ids) <= 1000 or any(not isinstance(x, str) or not x for x in owner_ids) or len(set(owner_ids)) != len(owner_ids):
            raise Problem(422, 'invalid_owners', '请选择不重复的教师名单')
        if kind in DEFAULTS and due_at is not None:
            raise Problem(422, 'configured_deadline', '该项目的截止时间由管理员规则计算')
        if kind not in DEFAULTS and (type(due_at) is not int or due_at <= 0 or due_at > 253402271999):
            raise Problem(422, 'deadline_required', '请设置实际周期截止时间')
        def run(db, actor):
            selected = workspace(actor, role)
            require(actor, selected, 'settings.manage')
            def apply():
                rule = None
                due = due_at
                if kind in DEFAULTS:
                    row = db.execute('SELECT rule FROM assessment_rules WHERE org_id=? AND kind=?', (actor['org_id'], kind)).fetchone()
                    rule = json.loads(row['rule']) if row else asdict(DEFAULTS[kind])
                    try:
                        due = deadline(start, Rule(**rule))
                    except ValueError:
                        raise Problem(422, 'invalid_period', '周周期从周一开始，月周期从每月一日开始')
                else:
                    from datetime import datetime, time
                    from .deadlines import TZ
                    if due < int(datetime.combine(start, time(), TZ).timestamp()):
                        raise Problem(422, 'invalid_period', '截止时间不能早于周期开始')
                if db.execute('SELECT 1 FROM assessment_periods WHERE org_id=? AND kind=? AND starts_on=?', (actor['org_id'], kind, starts_on)).fetchone():
                    raise Problem(409, 'period_exists', '该周期已经创建')
                for owner in owner_ids:
                    if not db.execute("SELECT 1 FROM users u JOIN roles r ON r.org_id=u.org_id AND r.user_id=u.id WHERE u.org_id=? AND u.id=? AND u.active=1 AND r.role='teacher'", (actor['org_id'], owner)).fetchone():
                        raise Problem(422, 'invalid_teacher', '名单包含未启用或不属于本机构的教师')
                key, now = uid(), self.clock()
                db.execute('INSERT INTO assessment_periods VALUES(?,?,?,?,?,?,?,?)', (key, actor['org_id'], kind, starts_on, due, encoded(rule), actor['id'], now))
                materials = []
                for owner in owner_ids:
                    item = uid()
                    db.execute("INSERT INTO assessment_materials(id,org_id,period_id,owner_id,state,updated_at) VALUES(?,?,?,?,'draft',?)", (item, actor['org_id'], key, owner, now))
                    materials.append({'id': item, 'owner_id': owner})
                self.record_audit(db, actor, selected, 'assessment_period_opened', key, due_at=due, owner_ids=owner_ids)
                return {'id': key, 'due_at': due, 'materials': materials}
            return self.once(db, actor, selected, command_ref, dict(action='period', kind=kind, starts_on=starts_on, owner_ids=sorted(owner_ids), due_at=due_at), apply)
        return self.command(token, run)

    def material(self, db, org, key):
        row = db.execute('SELECT m.*,p.kind,p.starts_on,p.due_at FROM assessment_materials m JOIN assessment_periods p ON p.org_id=m.org_id AND p.id=m.period_id WHERE m.org_id=? AND m.id=?', (org, key)).fetchone()
        if row is None:
            raise Problem(404, 'material_missing', '未找到考核材料')
        return row

    def view(self, row, now):
        result = dict(row)
        result['status'] = submission_status(now=now, due=row['due_at'], submitted_at=row['submitted_at'] if row['state'] == 'submitted' else None)
        result['editable'] = row['state'] != 'submitted' and may_edit(now=now, due=row['due_at'], unlocked_until=row['unlocked_until'])
        result['seconds_remaining'] = max(0, row['due_at'] - now)
        return result

    def change_material(self, token, role, *, material_id, action, expected_revision, command_ref, content=None, reason=None, unlocked_until=None):
        revision_number(expected_revision)
        permissions = {'save': 'submissions.write', 'submit': 'submissions.submit', 'return': 'submissions.return', 'unlock': 'submissions.unlock'}
        if action not in permissions:
            raise Problem(422, 'invalid_action', '操作不正确')
        if action == 'save':
            if not isinstance(content, str) or len(content) > 2000 or any(ord(c) < 32 and c not in '\r\n\t' for c in content):
                raise Problem(422, 'invalid_content', '材料内容过长或包含无效字符')
        elif content is not None:
            raise Problem(422, 'unexpected_content', '请先保存内容，再执行此操作')
        if action in {'return', 'unlock'}:
            reason = label(reason, 500)
        if action != 'unlock' and unlocked_until is not None:
            raise Problem(422, 'unexpected_unlock', '此操作不能更改解锁时间')
        def run(db, actor):
            selected = workspace(actor, role)
            row = self.material(db, actor['org_id'], material_id)
            require(actor, selected, permissions[action], target_org=row['org_id'], owner_id=row['owner_id'])
            def apply():
                if row['revision'] != expected_revision:
                    raise Problem(409, 'material_changed', '材料已更新，请刷新后操作')
                now, rev = self.clock(), row['revision'] + 1
                if action in {'save', 'submit'}:
                    if not self.view(row, now)['editable']:
                        raise Problem(409, 'material_locked', '材料已提交或超过截止时间；逾期请联系管理员解锁')
                    if action == 'save':
                        db.execute('UPDATE assessment_materials SET content=?,revision=?,updated_at=? WHERE id=?', (content, rev, now, material_id))
                    else:
                        if not row['content'].strip():
                            raise Problem(422, 'empty_material', '请先填写并保存材料')
                        db.execute("UPDATE assessment_materials SET state='submitted',submitted_at=?,unlocked_until=NULL,revision=?,updated_at=? WHERE id=?", (now, rev, now, material_id))
                        db.execute('INSERT INTO assessment_versions VALUES(?,?,?,?,?,?)', (actor['org_id'], material_id, rev, row['content'], now, int(now >= row['due_at'])))
                elif action == 'return':
                    if row['state'] != 'submitted':
                        raise Problem(409, 'not_submitted', '只能退回已提交的材料')
                    db.execute("UPDATE assessment_materials SET state='returned',unlocked_until=NULL,revision=?,updated_at=? WHERE id=?", (rev, now, material_id))
                else:
                    if row['state'] == 'submitted' or now < row['due_at']:
                        raise Problem(409, 'unlock_not_needed', '只能解锁已逾期且尚待提交的材料；已提交材料需先退回')
                    if type(unlocked_until) is not int or unlocked_until <= now or unlocked_until > now + 31 * 86400:
                        raise Problem(422, 'invalid_unlock', '解锁期限需在当前时间之后、31天之内')
                    db.execute('UPDATE assessment_materials SET unlocked_until=?,revision=?,updated_at=? WHERE id=?', (unlocked_until, rev, now, material_id))
                self.record_audit(db, actor, selected, 'assessment_' + action, material_id, owner_id=row['owner_id'], revision=rev, reason=reason, unlocked_until=unlocked_until)
                return self.view(self.material(db, actor['org_id'], material_id), now)
            return self.once(db, actor, selected, command_ref, dict(action=action, material_id=material_id, revision=expected_revision, content=content, reason=reason, unlocked_until=unlocked_until), apply)
        return self.command(token, run)

    def record_score(self, token, role, *, material_id, expected_revision, value, basis, command_ref):
        revision_number(expected_revision)
        basis = label(basis, 500)
        try:
            if not isinstance(value, str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?', value):
                raise ValueError('invalid decimal')
            number = Decimal(value)
            if not number.is_finite() or abs(number) > 1000000 or number.as_tuple().exponent < -2:
                raise ValueError('invalid score')
            canonical = format(number, 'f')
        except (ValueError, InvalidOperation):
            raise Problem(422, 'invalid_score', '分数须为最多两位小数的有效数字')
        def run(db, actor):
            selected = workspace(actor, role)
            require(actor, selected, 'scores.record')
            row = self.material(db, actor['org_id'], material_id)
            def apply():
                if row['state'] != 'submitted' or row['revision'] != expected_revision:
                    raise Problem(409, 'score_target_changed', '只能对当前已提交的材料版本录分')
                key = uid()
                db.execute('INSERT INTO assessment_scores VALUES(?,?,?,?,?,?,?,?)', (key, actor['org_id'], material_id, expected_revision, canonical, basis, actor['id'], self.clock()))
                self.record_audit(db, actor, selected, 'assessment_score_recorded', material_id, score_id=key, material_revision=expected_revision)
                return {'id': key, 'material_id': material_id, 'material_revision': expected_revision, 'value': canonical, 'basis': basis, 'method': 'manual'}
            return self.once(db, actor, selected, command_ref, dict(action='score', material_id=material_id, revision=expected_revision, value=canonical, basis=basis), apply)
        return self.command(token, run)

    def read_material(self, token, role, material_id):
        def run(db, actor):
            selected = workspace(actor, role)
            row = self.material(db, actor['org_id'], material_id)
            require(actor, selected, 'submissions.read', target_org=row['org_id'], owner_id=row['owner_id'])
            versions = [dict(r) for r in db.execute('SELECT revision,content,submitted_at,late FROM assessment_versions WHERE org_id=? AND material_id=? ORDER BY revision', (actor['org_id'], material_id))]
            scores = [dict(r) for r in db.execute('SELECT id,material_revision,value,basis,recorded_by,recorded_at FROM assessment_scores WHERE org_id=? AND material_id=? ORDER BY rowid', (actor['org_id'], material_id))]
            for score in scores:
                score['current'] = row['state'] == 'submitted' and score['material_revision'] == row['revision'] and score['id'] == scores[-1]['id']
            returned = db.execute("SELECT detail,created_at FROM management_audit WHERE org_id=? AND entity_id=? AND action='assessment_return' ORDER BY seq DESC LIMIT 1", (actor['org_id'], material_id)).fetchone()
            return {**self.view(row, self.clock()), 'versions': versions, 'scores': scores,
                    'return_reason': json.loads(returned['detail'])['reason'] if returned else None,
                    'returned_at': returned['created_at'] if returned else None}
        return self.command(token, run)
