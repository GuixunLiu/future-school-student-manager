"""Candidate management API. Not routed by the current public gateway.

Start only against an explicitly migrated candidate database. Authentication
uses existing account hashes; this factory neither resets nor creates users.
"""
from typing import Annotated, Literal

from fastapi import Depends, Header, Query
from pydantic import Field

from business.api import create_app as base_app
from business.models import Input
from business.store import Problem
from .delivery import DeliveryStore
from .policy import permits, require, workspace

Role = Literal['principal', 'admin', 'teacher', 'assessor']


class RequestInput(Input):
    workspace_role: Role
    lesson_id: str = Field(min_length=1, max_length=200)
    student_id: str = Field(min_length=1, max_length=80)
    kind: Literal['leave', 'makeup']
    reason: str = Field(min_length=1, max_length=200)
    request_ref: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=0)


class DecisionInput(Input):
    workspace_role: Role
    approve: bool
    reason: str = Field(min_length=1, max_length=200)
    decision_ref: str = Field(min_length=1, max_length=80)


class PeriodInput(Input):
    workspace_role: Role
    kind: Literal['weekly_plan', 'weekly_report', 'monthly_plan', 'monthly_report', 'enrolment_list', 'renewal_list']
    starts_on: str = Field(min_length=10, max_length=10)
    owner_ids: list[str] = Field(min_length=1, max_length=1000)
    due_at: int | None = None
    command_ref: str = Field(min_length=1, max_length=80)


class RuleInput(Input):
    frequency: Literal['weekly', 'monthly']
    day: int | Literal['last']
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class RuleChangeInput(Input):
    workspace_role: Role
    kind: Literal['weekly_plan', 'weekly_report', 'monthly_plan', 'monthly_report']
    rule: RuleInput
    expected_revision: int = Field(ge=0)
    command_ref: str = Field(min_length=1, max_length=80)


class MaterialChangeInput(Input):
    workspace_role: Role
    action: Literal['save', 'submit', 'return', 'unlock']
    expected_revision: int = Field(ge=0)
    command_ref: str = Field(min_length=1, max_length=80)
    content: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=500)
    unlocked_until: int | None = None


class ScoreInput(Input):
    workspace_role: Role
    expected_revision: int = Field(ge=0)
    value: str = Field(max_length=24)
    basis: str = Field(min_length=1, max_length=500)
    command_ref: str = Field(min_length=1, max_length=80)


class DeliveryReceipt(Input):
    generation: str = Field(min_length=1, max_length=80)
    event_id: str = Field(min_length=1, max_length=80)
    receipt_token: str = Field(pattern=r'^[0-9a-f]{32}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['applied', 'conflict']
    attendance_revision: int | None = Field(default=None, ge=0)
    balance_revision: int | None = Field(default=None, ge=0)
    balance_units: int | None = None
    conflict_code: Literal['mapping_changed', 'local_changes', 'attendance_changed',
                           'balance_changed', 'invalid_event', 'storage_failed'] | None = None


class CheckinInput(Input):
    generation: str = Field(min_length=1, max_length=80)
    event_ref: str = Field(min_length=1, max_length=80)
    student_id: str = Field(min_length=1, max_length=80)
    lesson_id: str = Field(min_length=1, max_length=200)
    attendance_revision: int = Field(ge=0)
    balance_revision: int = Field(ge=0)
    previous_balance_units: int
    consumed_units: int = Field(gt=0)
    remaining_units: int
    occurred_at: int = Field(ge=0, le=253402185599)


class EnrollmentReviewInput(Input):
    workspace_role: Role
    device_id: str = Field(min_length=1,max_length=80)
    generation: str = Field(min_length=1,max_length=80)
    baseline_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_captured_at: int = Field(ge=0)
    source: dict


class EnrollmentAuthorizeInput(Input):
    workspace_role: Role
    review_id: str = Field(min_length=1,max_length=80)
    profile_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    nonce: str = Field(pattern=r'^[a-f0-9]{32}$')


class EnrollmentBatchInput(Input):
    workspace_role: Role
    device_id: str = Field(min_length=1,max_length=80)
    generation: str = Field(min_length=1,max_length=80)
    baseline_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_captured_at: int = Field(ge=0)
    manifest: dict
    command_ref: str = Field(min_length=1,max_length=80)


class EnrollmentPageInput(Input):
    workspace_role: Role
    section: Literal['classes','students','facts']
    page_index: int = Field(ge=0,le=499)
    rows: list[dict] = Field(min_length=1,max_length=100)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class EnrollmentBatchFinalizeInput(Input):
    workspace_role: Role


class MigrationPrepareInput(Input):
    workspace_role: Role
    review_id: str = Field(min_length=1,max_length=80)
    profile_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    reason: str = Field(min_length=1,max_length=200)
    command_ref: str = Field(min_length=1,max_length=80)
    accept_database_mirror_pause: bool


class MigrationCancelInput(Input):
    workspace_role: Role
    reason: str = Field(min_length=1,max_length=200)
    command_ref: str = Field(min_length=1,max_length=80)


def create_app(database, clock=None, *, allow_test_client=False):
    store = DeliveryStore(database, clock)
    with store.transaction() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if 'management_initializations' in tables and db.execute("SELECT 1 FROM management_initializations WHERE phase='rehearsal_only' LIMIT 1").fetchone():
            raise ValueError('Offline rehearsal database cannot serve management or device requests')
        if not {'management_roles', 'management_approval_scopes', 'management_requests',
                'management_attendance', 'management_balances', 'management_outbox', 'management_audit',
                'assessment_rules', 'assessment_periods', 'assessment_materials', 'assessment_versions',
                'assessment_scores', 'assessment_commands', 'management_device_access',
                'management_device_students', 'management_deliveries', 'management_uploads', 'management_enrollment_proofs', 'management_enrollment_reviews', 'management_migration_holds',
                'management_enrollment_batches','management_enrollment_pages'} <= tables:
            raise ValueError('Explicit candidate migration required; no automatic production migration')
    app = base_app(database, clock, allow_test_client=allow_test_client)
    app.router.routes[:] = [r for r in app.router.routes if r.path in {'/v1/auth/login', '/v1/auth/logout'}]
    app.state.management_store = store

    from pathlib import Path
    from fastapi.responses import FileResponse
    assets = Path(__file__).parent / 'web'

    @app.get('/')
    def page():
        return FileResponse(assets / 'index.html', media_type='text/html')

    @app.get('/workspace.js')
    def script():
        return FileResponse(assets / 'workspace.js', media_type='application/javascript')

    @app.get('/workspace.css')
    def styles():
        return FileResponse(assets / 'workspace.css', media_type='text/css')

    def authorization(value: Annotated[str | None, Header(alias='Authorization')] = None):
        if not value or not value.startswith('Bearer ') or len(value) > 128:
            raise Problem(401, 'unauthorized', '请先登录')
        return value[7:]
    Token = Annotated[str, Depends(authorization)]

    @app.get('/v1/management/device/plan')
    def device_plan(token: Token, generation: Annotated[str, Query(min_length=1, max_length=80)]):
        return store.enrollment_plan(token, generation)

    @app.post('/v1/management/enrollment-reviews')
    def enrollment_review(data: EnrollmentReviewInput, token: Token):
        return store.review_enrollment(token,data.workspace_role,**data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/enrollment-authorizations')
    def enrollment_authorize(data: EnrollmentAuthorizeInput, token: Token):
        return store.authorize_enrollment(token,data.workspace_role,**data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/enrollment-batches')
    def enrollment_batch(data: EnrollmentBatchInput, token: Token):
        return store.begin_enrollment_batch(token,data.workspace_role,**data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/enrollment-batches/{batch_id}/pages')
    def enrollment_page(batch_id: str, data: EnrollmentPageInput, token: Token):
        return store.put_enrollment_page(token,data.workspace_role,batch_id=batch_id,**data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/enrollment-batches/{batch_id}/finalize')
    def enrollment_batch_finalize(batch_id: str, data: EnrollmentBatchFinalizeInput, token: Token):
        return store.finalize_enrollment_batch(token,data.workspace_role,batch_id=batch_id)

    @app.get('/v1/management/device/next')
    def device_next(token: Token, generation: Annotated[str, Query(min_length=1, max_length=80)]):
        return store.poll(token, generation)

    @app.post('/v1/management/device/receipt')
    def device_receipt(data: DeliveryReceipt, token: Token):
        return store.acknowledge(token, **data.model_dump())

    @app.post('/v1/management/device/checkin')
    def device_checkin(data: CheckinInput, token: Token):
        return store.upload_attendance(token, **data.model_dump())

    @app.get('/health/ready')
    def health():
        return {'status': 'ready', 'mode': 'management_candidate',
                'approval_mode': 'web_approval', 'tablet_delivery_enabled': False,
                'production_ready': False, 'wechat_sending_enabled': False}

    @app.get('/v1/me')
    def me(token: Token):
        def run(db, actor):
            store.require_user(actor)
            return store.me(db, actor)
        return store.command(token, run)

    @app.get('/v1/management/migration-holds')
    def migration_holds(token: Token,workspace_role: Annotated[Role,Query()]):
        def run(db,actor):
            require(actor,workspace(actor,workspace_role),'settings.manage')
            rows=db.execute('SELECT * FROM management_migration_holds WHERE org_id=? ORDER BY rowid DESC LIMIT 50',(actor['org_id'],))
            return {'items':[store.hold_receipt(r,db=db)|{'reason':r['reason'],'prepared_by':r['prepared_by']} for r in rows]}
        return store.command(token,run)

    @app.post('/v1/management/migration-holds')
    def prepare_migration(data: MigrationPrepareInput,token: Token):
        return store.prepare_migration(token,data.workspace_role,**data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/migration-holds/{hold_id}/cancel')
    def cancel_migration(hold_id: str,data: MigrationCancelInput,token: Token):
        return store.cancel_migration(token,data.workspace_role,hold_id=hold_id,**data.model_dump(exclude={'workspace_role'}))

    @app.get('/v1/management/workspace')
    def current(token: Token, workspace_role: Annotated[Role, Query()]):
        def run(db, actor):
            role = workspace(actor, workspace_role)
            scope = store.scope(db, actor)
            return {'workspace_role': role,
                    'teacher_class_ids': sorted(scope.teacher_classes) if role == 'teacher' else [],
                    'approval_class_ids': sorted(scope.approval_classes) if role == 'assessor' else [],
                    'can_score': permits(actor, role, 'scores.record'),
                    'can_manage': permits(actor, role, 'accounts.manage'),
                    'can_unlock': permits(actor, role, 'submissions.unlock')}
        return store.command(token, run)

    @app.get('/v1/management/requests')
    def requests(token: Token, workspace_role: Annotated[Role, Query()],
                 limit: Annotated[int, Query(ge=1, le=100)] = 50,
                 before: Annotated[str | None, Query(max_length=32)] = None):
        def run(db, actor):
            role = workspace(actor, workspace_role)
            if role not in {'admin', 'teacher', 'assessor'}:
                raise Problem(403, 'permission_denied', '此身份不处理审批申请')
            where = ['r.org_id=?']
            params = [actor['org_id']]
            if role == 'teacher':
                where.append('r.requested_by=? AND EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=r.org_id AND t.user_id=? AND t.class_id=r.class_id)')
                params.extend([actor['id'], actor['id']])
            if role == 'assessor':
                where.append('EXISTS(SELECT 1 FROM management_approval_scopes s WHERE s.org_id=r.org_id AND s.user_id=? AND s.class_id=r.class_id)')
                params.append(actor['id'])
            if before:
                where.append('r.rowid < COALESCE((SELECT rowid FROM management_requests WHERE org_id=? AND id=?),0)')
                params.extend([actor['org_id'], before])
            rows = db.execute('SELECT r.id,r.lesson_id,r.student_id,r.class_id,r.kind,r.reason,r.status,r.requested_by,r.created_at,r.decided_at,r.decided_by,r.decision_reason,s.name AS student_name,c.name AS class_name FROM management_requests r JOIN students s ON s.org_id=r.org_id AND s.id=r.student_id JOIN classes c ON c.org_id=r.org_id AND c.id=r.class_id WHERE '
                              + ' AND '.join(where) + ' ORDER BY r.rowid DESC LIMIT ?', (*params, limit + 1)).fetchall()
            items = [dict(r) for r in rows[:limit]]
            for item in items:
                item['tablet_delivery'] = store.delivery_status(db, actor['org_id'], item['id'], item['status'])
            return {'workspace_role': role, 'items': items,
                    'next_before': items[-1]['id'] if len(rows) > limit else None}
        return store.command(token, run)

    @app.get('/v1/management/attendance')
    def attendance(token: Token, workspace_role: Annotated[Role, Query()],
                   on: Annotated[str, Query(min_length=10, max_length=10)],
                   limit: Annotated[int, Query(ge=1, le=100)] = 100,
                   before: Annotated[int | None, Query(ge=1)] = None):
        from datetime import date, datetime, time, timedelta
        from .deadlines import TZ
        try:
            day = date.fromisoformat(on)
            if day.isoformat() != on:
                raise ValueError('noncanonical date')
            start = int(datetime.combine(day, time(), TZ).timestamp())
            end = int(datetime.combine(day + timedelta(days=1), time(), TZ).timestamp())
        except (ValueError, OverflowError):
            raise Problem(422, 'invalid_date', '上课日期不正确')
        def run(db, actor):
            selected = workspace(actor, workspace_role)
            if selected not in {'admin', 'principal', 'teacher'}:
                raise Problem(403, 'permission_denied', '此身份请在审批列表查看管辖范围申请')
            where, params = ['a.org_id=?', 'a.starts_at>=?', 'a.starts_at<?'], [actor['org_id'], start, end]
            if selected == 'teacher':
                where.append('EXISTS(SELECT 1 FROM teacher_classes t WHERE t.org_id=a.org_id AND t.class_id=a.class_id AND t.user_id=?)')
                params.append(actor['id'])
            if before is not None:
                where.append('a.rowid<?')
                params.append(before)
            rows = db.execute('SELECT a.*,a.rowid AS cursor,s.name AS student_name,c.name AS class_name,b.units AS balance_units,EXISTS(SELECT 1 FROM management_requests r WHERE r.org_id=a.org_id AND r.lesson_id=a.lesson_id AND r.student_id=a.student_id AND r.status=\'pending\') AS has_pending_request FROM management_attendance a JOIN students s ON s.org_id=a.org_id AND s.id=a.student_id JOIN classes c ON c.org_id=a.org_id AND c.id=a.class_id JOIN management_balances b ON b.org_id=a.org_id AND b.student_id=a.student_id WHERE '
                              + ' AND '.join(where) + ' ORDER BY a.rowid DESC LIMIT ?', (*params, limit + 1)).fetchall()
            items = [dict(r) for r in rows[:limit]]
            return {'items': items, 'next_before': items[-1]['cursor'] if len(rows) > limit else None, 'server_time': store.clock()}
        return store.command(token, run)

    @app.post('/v1/management/requests', status_code=201)
    def submit(data: RequestInput, token: Token):
        return store.submit(token, data.workspace_role, **data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/requests/{request_id}/decision')
    def decide(request_id: str, data: DecisionInput, token: Token):
        return store.decide(token, data.workspace_role, request_id=request_id,
                            **data.model_dump(exclude={'workspace_role'}))

    @app.get('/v1/management/assessment-rules')
    def rules(token: Token, workspace_role: Annotated[Role, Query()]):
        from dataclasses import asdict
        import json
        from .deadlines import DEFAULTS
        def run(db, actor):
            require(actor, workspace_role, 'settings.manage')
            stored = {r['kind']: r for r in db.execute('SELECT * FROM assessment_rules WHERE org_id=?', (actor['org_id'],))}
            return {'items': [{'kind': kind, 'rule': json.loads(stored[kind]['rule']) if kind in stored else asdict(rule),
                               'revision': stored[kind]['revision'] if kind in stored else 0} for kind, rule in DEFAULTS.items()]}
        return store.command(token, run)

    @app.get('/v1/management/assessment-teachers')
    def assessment_teachers(token: Token, workspace_role: Annotated[Role, Query()]):
        def run(db, actor):
            require(actor, workspace_role, 'settings.manage')
            return {'items': [dict(r) for r in db.execute("SELECT u.id,u.display_name FROM users u WHERE u.org_id=? AND u.active=1 AND EXISTS(SELECT 1 FROM roles r WHERE r.org_id=u.org_id AND r.user_id=u.id AND r.role='teacher') ORDER BY u.display_name,u.id LIMIT 1000", (actor['org_id'],))]}
        return store.command(token, run)

    @app.post('/v1/management/assessment-rules')
    def configure_rule(data: RuleChangeInput, token: Token):
        return store.configure_rule(token, data.workspace_role, **data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/assessment-periods', status_code=201)
    def open_period(data: PeriodInput, token: Token):
        return store.open_period(token, data.workspace_role, **data.model_dump(exclude={'workspace_role'}))

    @app.get('/v1/management/materials')
    def materials(token: Token, workspace_role: Annotated[Role, Query()],
                  limit: Annotated[int, Query(ge=1, le=100)] = 50,
                  before: Annotated[str | None, Query(max_length=32)] = None):
        def run(db, actor):
            selected = workspace(actor, workspace_role)
            require(actor, selected, 'submissions.read', owner_id=actor['id'])
            where, params = ['m.org_id=?'], [actor['org_id']]
            if selected == 'teacher':
                where.append('m.owner_id=?')
                params.append(actor['id'])
            if before:
                where.append('m.rowid < COALESCE((SELECT rowid FROM assessment_materials WHERE org_id=? AND id=?),0)')
                params.extend([actor['org_id'], before])
            rows = db.execute('SELECT m.id,m.org_id,m.owner_id,m.state,m.revision,m.submitted_at,m.unlocked_until,m.updated_at,p.kind,p.starts_on,p.due_at,u.display_name FROM assessment_materials m JOIN assessment_periods p ON p.org_id=m.org_id AND p.id=m.period_id JOIN users u ON u.org_id=m.org_id AND u.id=m.owner_id WHERE '
                              + ' AND '.join(where) + ' ORDER BY m.rowid DESC LIMIT ?', (*params, limit + 1)).fetchall()
            items = [store.view(r, store.clock()) for r in rows[:limit]]
            return {'items': items, 'next_before': items[-1]['id'] if len(rows) > limit else None}
        return store.command(token, run)

    @app.get('/v1/management/materials/{material_id}')
    def material(material_id: str, token: Token, workspace_role: Annotated[Role, Query()]):
        return store.read_material(token, workspace_role, material_id)

    @app.post('/v1/management/materials/{material_id}')
    def change_material(material_id: str, data: MaterialChangeInput, token: Token):
        return store.change_material(token, data.workspace_role, material_id=material_id, **data.model_dump(exclude={'workspace_role'}))

    @app.post('/v1/management/materials/{material_id}/scores')
    def record_score(material_id: str, data: ScoreInput, token: Token):
        return store.record_score(token, data.workspace_role, material_id=material_id, **data.model_dump(exclude={'workspace_role'}))

    return app
