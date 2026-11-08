"""HTTP entry point for the implemented PostgreSQL runtime transactions."""
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field

from business.api import LocalGuard
from business.models import Input, Login
from business.store import Problem
from .runtime_service import RuntimeStore


class TabletCheckinInput(Input):
    event_ref: str = Field(min_length=1, max_length=80)
    lesson_id: str = Field(min_length=1, max_length=200)
    student_id: str = Field(min_length=1, max_length=80)
    occurred_at: int = Field(ge=0, le=253402185599)
    attendance_revision: int = Field(ge=0)
    balance_revision: int = Field(ge=0)
    previous_balance_units: int = Field(ge=-1_000_000, le=10_000_000)
    consumed_units: int = Field(gt=0, le=11_000_000)


class AttendanceCorrectionInput(Input):
    student_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)
    event_ref: str = Field(min_length=1, max_length=80)
    expected_revision: int = Field(ge=0)


class BalanceAdjustmentInput(Input):
    units: int = Field(ge=-1_000_000, le=10_000_000)
    reason: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)
    command_ref: str = Field(min_length=1, max_length=80)


class StudentEnrollmentInput(Input):
    class_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    external_ref: str = Field(min_length=1, max_length=80)
    opening_units: int = Field(ge=-1_000_000, le=10_000_000)
    occurred_at: int = Field(ge=0, le=253402185599)
    source_ref: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class StudentUpdateInput(Input):
    class_ids: list[str] = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    external_ref: str = Field(min_length=1, max_length=80)
    active: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class CourseCreateInput(Input):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=2000)
    unit_price_cents: int = Field(ge=0, le=100_000_000)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class CourseUpdateInput(CourseCreateInput):
    active: bool


class ClassCreateInput(Input):
    name: str = Field(min_length=1, max_length=80)
    course_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class ClassUpdateInput(ClassCreateInput):
    expected_version: int = Field(ge=0)


class ClassTeacherInput(Input):
    teacher_ids: list[str] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class ScheduleTemplateInput(Input):
    weekday: int = Field(ge=1, le=7)
    interval_weeks: int = Field(ge=1, le=2)
    anchor_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    start_time: str = Field(pattern=r'^\d{2}:\d{2}$')
    end_time: str = Field(pattern=r'^\d{2}:\d{2}$')
    duration_minutes: int = Field(ge=1, le=720)
    end_day_offset: int = Field(ge=0, le=1)
    effective_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    teacher_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class ScheduleTemplateCreateInput(ScheduleTemplateInput):
    class_id: str = Field(min_length=1, max_length=80)


class ScheduleTemplateUpdateInput(ScheduleTemplateInput):
    pass


class ScheduleTemplateDeleteInput(Input):
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class AccountCreateInput(Input):
    login: str = Field(min_length=3, max_length=64, pattern=r'^[a-z0-9][a-z0-9_.-]{2,63}$')
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=15, max_length=128, repr=False, json_schema_extra={'writeOnly': True})
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class AccountStatusInput(Input):
    active: bool
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class AccountPasswordInput(Input):
    password: str = Field(min_length=15, max_length=128, repr=False, json_schema_extra={'writeOnly': True})
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class RenewalInput(Input):
    student_id: str = Field(min_length=1, max_length=80)
    course_id: str = Field(min_length=1, max_length=80)
    units_added: int = Field(gt=0, le=11_000_000)
    amount_cents: int = Field(ge=0, le=100_000_000)
    unit_price_cents: int = Field(ge=0, le=100_000_000)
    occurred_at: int = Field(ge=0, le=253402185599)
    source_ref: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class RosterSyncInput(Input):
    command_ref: str = Field(min_length=1, max_length=80)


class LessonChargeRuleInput(Input):
    charge_units: int = Field(gt=0, le=10_000_000)
    expected_revision: int = Field(ge=0)
    command_ref: str = Field(min_length=1, max_length=80)


class LessonCreateInput(Input):
    class_id: str = Field(min_length=1, max_length=80)
    teacher_id: str = Field(min_length=1, max_length=80)
    schedule_id: str | None = Field(default=None, min_length=1, max_length=80)
    starts_at: int = Field(ge=0, le=253402185599)
    ends_at: int = Field(ge=0, le=253402185599)
    charge_units: int = Field(gt=0, le=10_000_000)
    command_ref: str = Field(min_length=1, max_length=80)


class LessonRescheduleInput(Input):
    starts_at: int = Field(ge=0, le=253402185599)
    ends_at: int = Field(ge=0, le=253402185599)
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class LessonCancelInput(Input):
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class ReportGenerateInput(Input):
    kind: str = Field(pattern=r'^(weekly|monthly)$')
    period_start: str = Field(min_length=7, max_length=10)
    command_ref: str = Field(min_length=1, max_length=80)


class ReportRetryInput(Input):
    command_ref: str = Field(min_length=1, max_length=80)


class FaceProfileInput(Input):
    model_version: str = Field(min_length=1, max_length=40)
    descriptors: list[list[float]] = Field(min_length=3, max_length=3)
    consent_ref: str = Field(min_length=1, max_length=100)
    command_ref: str = Field(min_length=1, max_length=80)


class FaceCheckinInput(Input):
    event_ref: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=40)
    descriptors: list[list[float]] = Field(min_length=3, max_length=3)


class DeviceEnrollmentInput(Input):
    name: str = Field(min_length=1, max_length=80)
    class_ids: list[str] = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class DeviceCommandInput(Input):
    command_ref: str = Field(min_length=1, max_length=80)


class TabletActivationInput(Input):
    activation_code: str = Field(min_length=47, max_length=47, pattern=r'^wxa_[A-Za-z0-9_-]{43}$')
    expected_org_id: str | None = Field(default=None, min_length=1, max_length=80)
    expected_device_id: str | None = Field(default=None, min_length=1, max_length=80)


def create_app(conninfo, clock=None, *, allow_test_client=False, face_key=None):
    store = RuntimeStore(conninfo, clock, face_key=face_key)
    app = FastAPI(title='未来学校统一运行 API', docs_url=None, redoc_url=None,
                  openapi_url=None, redirect_slashes=False)
    app.state.runtime_store = store
    app.add_middleware(LocalGuard, allow_test_client=allow_test_client)

    @app.exception_handler(Problem)
    async def problem(request, error):
        headers = {'WWW-Authenticate': 'Bearer'} if error.status == 401 else {}
        return JSONResponse({'error': {'code': error.code, 'message': error.message}},
                            status_code=error.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({'error': {'code': 'invalid_request',
                                       'message': '字段格式不正确或包含未支持的字段'}}, status_code=422)

    @app.exception_handler(psycopg.IntegrityError)
    async def integrity(request, error):
        return JSONResponse({'error': {'code': 'conflict', 'message': '记录冲突，未保存任何更改'}}, status_code=409)

    @app.exception_handler(psycopg.OperationalError)
    async def unavailable(request, error):
        return JSONResponse({'error': {'code': 'storage_unavailable',
                                       'message': '暂时无法保存，请稍后重试'}}, status_code=503)

    def authorization(value: Annotated[str | None, Header(alias='Authorization')] = None):
        if not value or not value.startswith('Bearer ') or len(value) > 128:
            raise Problem(401, 'unauthorized', '请先登录')
        return value[7:]

    Token = Annotated[str, Depends(authorization)]

    from pathlib import Path
    from fastapi.responses import FileResponse
    assets = Path(__file__).parent / 'web'

    @app.get('/runtime')
    def runtime_page():
        return FileResponse(assets / 'runtime.html', media_type='text/html')

    @app.get('/runtime.js')
    def runtime_script():
        return FileResponse(assets / 'runtime.js', media_type='application/javascript')

    @app.get('/runtime.css')
    def runtime_styles():
        return FileResponse(assets / 'runtime.css', media_type='text/css')

    @app.get('/teacher-runtime.js')
    def teacher_runtime_script():
        return FileResponse(assets / 'teacher-runtime.js', media_type='application/javascript')

    @app.get('/teacher-report.js')
    def teacher_report_script():
        return FileResponse(assets / 'teacher-report.js', media_type='application/javascript')

    @app.get('/report-history.js')
    def report_history_script():
        return FileResponse(assets / 'report-history.js', media_type='application/javascript')

    @app.get('/admin-report.js')
    def admin_report_script():
        return FileResponse(assets / 'admin-report.js', media_type='application/javascript')

    @app.get('/admin-monthly.js')
    def admin_monthly_script():
        return FileResponse(assets / 'admin-monthly.js', media_type='application/javascript')

    @app.get('/admin-renewal.js')
    def admin_renewal_script():
        return FileResponse(assets / 'admin-renewal.js', media_type='application/javascript')

    @app.get('/admin-enrollment.js')
    def admin_enrollment_script():
        return FileResponse(assets / 'admin-enrollment.js', media_type='application/javascript')

    @app.get('/admin-roster-sync.js')
    def admin_roster_sync_script():
        return FileResponse(assets / 'admin-roster-sync.js', media_type='application/javascript')

    @app.get('/admin-accounts.js')
    def admin_accounts_script():
        return FileResponse(assets / 'admin-accounts.js', media_type='application/javascript')

    @app.get('/admin-schedule.js')
    def admin_schedule_script():
        return FileResponse(assets / 'admin-schedule.js', media_type='application/javascript')

    @app.get('/admin-directory.js')
    def admin_directory_script():
        return FileResponse(assets / 'admin-directory.js', media_type='application/javascript')

    @app.get('/face-engine.js')
    def face_engine_script():
        return FileResponse(assets / 'face-engine.js', media_type='application/javascript')

    @app.get('/teacher-face.js')
    def teacher_face_script():
        return FileResponse(assets / 'teacher-face.js', media_type='application/javascript')

    @app.get('/admin-face.js')
    def admin_face_script():
        return FileResponse(assets / 'admin-face.js', media_type='application/javascript')

    face_runtime_assets = {
        'face-api.min.js': 'application/javascript',
        'ort.wasm.min.js': 'application/javascript',
        'ort-wasm-simd-threaded.mjs': 'application/javascript',
        'ort-wasm-simd-threaded.wasm': 'application/wasm'}
    face_model_assets = {
        'tiny_face_detector_model-weights_manifest.json', 'tiny_face_detector_model-shard1',
        'face_landmark_68_tiny_model-weights_manifest.json', 'face_landmark_68_tiny_model-shard1',
        'sface_int8bq.onnx'}

    @app.get('/face-runtime/{filename}')
    def face_runtime_asset(filename: str):
        if filename not in face_runtime_assets:
            raise Problem(404, 'asset_not_found', '人脸组件不存在')
        return FileResponse(assets / filename, media_type=face_runtime_assets[filename])

    @app.get('/face-models/{filename}')
    def face_model_asset(filename: str):
        if filename not in face_model_assets:
            raise Problem(404, 'asset_not_found', '人脸模型不存在')
        media_type = 'application/json' if filename.endswith('.json') else 'application/octet-stream'
        return FileResponse(assets / 'face-models' / filename, media_type=media_type)

    @app.get('/workspace.css')
    def workspace_styles():
        return FileResponse(assets / 'workspace.css', media_type='text/css')

    @app.get('/future-school-logo.png')
    def runtime_logo():
        return FileResponse(assets / 'future-school-logo.png', media_type='image/png')

    @app.get('/health/ready')
    def health():
        with store.connect() as db:
            row = db.execute("SELECT applied_at FROM schema_migrations WHERE version='fresh-start-runtime-v2'").fetchone()
        return {'status': 'ready' if row else 'not_initialized',
                'schema': 'fresh-start-runtime-v2', 'initialized': bool(row),
                'production_ready': False, 'wechat_sending_enabled': False}

    @app.get('/v1/me')
    def me(token: Token):
        return store.me(token)

    @app.post('/v1/auth/login')
    def login(data: Login, request: Request):
        return store.login(data.organization, data.login, data.password, request.client.host if request.client else 'unknown')

    @app.post('/v1/auth/logout')
    def logout(token: Token):
        return store.logout(token)

    @app.get('/v1/admin/students')
    def admin_students(token: Token):
        return store.admin_students(token)

    @app.post('/v1/admin/students', status_code=201)
    def admin_create_student(data: StudentEnrollmentInput, response: Response, token: Token):
        result = store.admin_create_student(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/students/{student_id}')
    def admin_update_student(student_id: str, data: StudentUpdateInput, token: Token):
        return store.admin_update_student(token, student_id, data.model_dump())

    @app.put('/v1/admin/students/{student_id}/face-profile', status_code=201)
    def admin_face_profile(student_id: str, data: FaceProfileInput, response: Response, token: Token):
        result = store.admin_face_profile(token, student_id, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/admin/lessons/{lesson_id}/charge-rule', status_code=201)
    def admin_set_lesson_charge_rule(lesson_id: str, data: LessonChargeRuleInput, response: Response, token: Token):
        result = store.admin_set_lesson_charge_rule(token, lesson_id, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/admin/students/{student_id}/future-lessons')
    def admin_future_lessons(student_id: str, token: Token):
        return store.admin_future_lessons(token, student_id)

    @app.post('/v1/admin/students/{student_id}/future-lessons', status_code=201)
    def admin_sync_future_lessons(student_id: str, data: RosterSyncInput, response: Response, token: Token):
        result = store.admin_sync_future_lessons(token, student_id, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/admin/renewals', status_code=201)
    def admin_renewal(data: RenewalInput, response: Response, token: Token):
        result = store.admin_renewal(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/admin/courses')
    def admin_courses(token: Token):
        return store.admin_courses(token)

    @app.post('/v1/admin/courses', status_code=201)
    def admin_create_course(data: CourseCreateInput, response: Response, token: Token):
        result = store.admin_create_course(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/courses/{course_id}')
    def admin_update_course(course_id: str, data: CourseUpdateInput, token: Token):
        return store.admin_update_course(token, course_id, data.model_dump())

    @app.get('/v1/admin/classes')
    def admin_classes(token: Token):
        return store.admin_classes(token)

    @app.post('/v1/admin/classes', status_code=201)
    def admin_create_class(data: ClassCreateInput, response: Response, token: Token):
        result = store.admin_create_class(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/classes/{class_id}')
    def admin_update_class(class_id: str, data: ClassUpdateInput, token: Token):
        return store.admin_update_class(token, class_id, data.model_dump())

    @app.put('/v1/admin/classes/{class_id}/teacher')
    def admin_set_class_teacher(class_id: str, data: ClassTeacherInput, token: Token):
        return store.admin_set_class_teacher(token, class_id, data.model_dump())

    @app.get('/v1/admin/teachers')
    def admin_teachers(token: Token):
        return store.admin_teachers(token)

    @app.get('/v1/admin/accounts')
    def admin_accounts(token: Token):
        return store.admin_accounts(token)

    @app.post('/v1/admin/accounts', status_code=201)
    def admin_create_account(data: AccountCreateInput, response: Response, token: Token):
        result = store.admin_create_account(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/accounts/{user_id}/status')
    def admin_set_account_status(user_id: str, data: AccountStatusInput, token: Token):
        return store.admin_set_account_status(token, user_id, data.model_dump())

    @app.put('/v1/admin/accounts/{user_id}/password')
    def admin_reset_account_password(user_id: str, data: AccountPasswordInput, token: Token):
        return store.admin_reset_account_password(token, user_id, data.model_dump())

    @app.get('/v1/admin/schedule-templates')
    def admin_schedule_templates(token: Token):
        return store.admin_schedule_templates(token)

    @app.post('/v1/admin/schedule-templates', status_code=201)
    def admin_create_schedule_template(data: ScheduleTemplateCreateInput, response: Response, token: Token):
        result = store.admin_create_schedule_template(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/schedule-templates/{schedule_id}')
    def admin_update_schedule_template(schedule_id: str, data: ScheduleTemplateUpdateInput, token: Token):
        return store.admin_update_schedule_template(token, schedule_id, data.model_dump())

    @app.post('/v1/admin/schedule-templates/{schedule_id}/delete')
    def admin_delete_schedule_template(schedule_id: str, data: ScheduleTemplateDeleteInput, token: Token):
        return store.admin_delete_schedule_template(token, schedule_id, data.model_dump())

    @app.get('/v1/admin/lessons')
    def admin_lessons(token: Token, date: str | None = None):
        return store.admin_lessons(token, date)

    @app.post('/v1/admin/lessons', status_code=201)
    def admin_create_lesson(data: LessonCreateInput, response: Response, token: Token):
        result = store.admin_create_lesson(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/admin/lessons/{lesson_id}')
    def admin_lesson(lesson_id: str, token: Token):
        return store.admin_lesson(token, lesson_id)

    @app.post('/v1/admin/lessons/{lesson_id}/makeup', status_code=201)
    def admin_cancelled_makeup(lesson_id: str, data: AttendanceCorrectionInput, response: Response, token: Token):
        result = store.admin_cancelled_correction(token, lesson_id, 'makeup', data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/admin/lessons/{lesson_id}/leave', status_code=201)
    def admin_cancelled_leave(lesson_id: str, data: AttendanceCorrectionInput, response: Response, token: Token):
        result = store.admin_cancelled_correction(token, lesson_id, 'leave', data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.put('/v1/admin/lessons/{lesson_id}')
    def admin_reschedule_lesson(lesson_id: str, data: LessonRescheduleInput, token: Token):
        return store.admin_reschedule_lesson(token, lesson_id, data.model_dump())

    @app.post('/v1/admin/lessons/{lesson_id}/cancel')
    def admin_cancel_lesson(lesson_id: str, data: LessonCancelInput, token: Token):
        return store.admin_cancel_lesson(token, lesson_id, data.model_dump())

    @app.get('/v1/admin/reports/weekly')
    def admin_weekly_report(token: Token, week_start: str | None = None):
        return store.admin_weekly_report(token, week_start)

    @app.get('/v1/admin/reports/monthly')
    def admin_monthly_report(token: Token, month: str | None = None):
        return store.admin_monthly_report(token, month)

    @app.get('/v1/admin/report-runs')
    def admin_report_runs(token: Token, kind: str, period_start: str):
        return store.admin_report_runs(token, kind, period_start)

    @app.post('/v1/admin/report-runs', status_code=201)
    def admin_generate_report(data: ReportGenerateInput, response: Response, token: Token):
        result = store.admin_generate_report(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/admin/report-runs/{run_id}')
    def admin_report_run(run_id: str, token: Token):
        return store.admin_report_run(token, run_id)

    @app.post('/v1/admin/report-runs/{run_id}/retry', status_code=201)
    def admin_retry_report(run_id: str, data: ReportRetryInput, response: Response, token: Token):
        result = store.admin_retry_report(token, run_id, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/admin/devices')
    def admin_devices(token: Token):
        return store.admin_devices(token)

    @app.post('/v1/admin/devices', status_code=201)
    def admin_create_device(data: DeviceEnrollmentInput, response: Response, token: Token):
        result = store.admin_create_device(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/admin/devices/{device_id}/activation')
    def admin_reissue_device(device_id: str, data: DeviceCommandInput, token: Token):
        return store.admin_reissue_device(token, device_id, data.model_dump())

    @app.post('/v1/admin/devices/{device_id}/disable')
    def admin_disable_device(device_id: str, data: DeviceCommandInput, token: Token):
        return store.admin_disable_device(token, device_id, data.model_dump())

    @app.post('/v1/tablet/activate')
    def tablet_activate(data: TabletActivationInput):
        return store.activate_tablet(data.activation_code, expected_org_id=data.expected_org_id,
                                     expected_device_id=data.expected_device_id)

    @app.get('/v1/tablet/bootstrap')
    def tablet_bootstrap(token: Token):
        return store.tablet_bootstrap(token)

    @app.get('/v1/tablet/face-profiles')
    def tablet_face_profiles(token: Token):
        return store.tablet_face_profiles(token)

    @app.post('/v1/tablet/checkins', status_code=201)
    def tablet_checkin(data: TabletCheckinInput, response: Response, token: Token):
        result = store.tablet_checkin(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/tablet/checkins/resolve')
    def tablet_resolve_checkin(data: TabletCheckinInput, token: Token):
        return store.tablet_resolve_checkin(token, data.model_dump())

    @app.get('/v1/teacher/lessons')
    def teacher_lessons(token: Token, date: str | None = None):
        return store.teacher_lessons(token, date)

    @app.get('/v1/teacher/lessons/{lesson_id}')
    def teacher_lesson(lesson_id: str, token: Token):
        return store.teacher_lesson(token, lesson_id)

    @app.post('/v1/teacher/lessons/{lesson_id}/face-checkin', status_code=201)
    def teacher_face_checkin(lesson_id: str, data: FaceCheckinInput, response: Response, token: Token):
        result = store.teacher_face_checkin(token, lesson_id, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/teacher/lessons/{lesson_id}/makeup', status_code=201)
    def teacher_makeup(lesson_id: str, data: AttendanceCorrectionInput, response: Response, token: Token):
        result = store.teacher_correction(token, lesson_id, 'makeup', data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.post('/v1/teacher/lessons/{lesson_id}/leave', status_code=201)
    def teacher_leave(lesson_id: str, data: AttendanceCorrectionInput, response: Response, token: Token):
        result = store.teacher_correction(token, lesson_id, 'leave', data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/teacher/reports/weekly')
    def teacher_weekly_report(token: Token, week_start: str | None = None):
        return store.teacher_weekly_report(token, week_start)

    @app.put('/v1/teacher/students/{student_id}/balance')
    def teacher_balance(student_id: str, data: BalanceAdjustmentInput, token: Token):
        return store.teacher_adjust_balance(token, student_id, data.model_dump())

    return app
