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


class DeviceEnrollmentInput(Input):
    name: str = Field(min_length=1, max_length=80)
    class_ids: list[str] = Field(min_length=1, max_length=200)
    command_ref: str = Field(min_length=1, max_length=80)


class DeviceCommandInput(Input):
    command_ref: str = Field(min_length=1, max_length=80)


class TabletActivationInput(Input):
    activation_code: str = Field(min_length=47, max_length=47, pattern=r'^wxa_[A-Za-z0-9_-]{43}$')


def create_app(conninfo, clock=None, *, allow_test_client=False):
    store = RuntimeStore(conninfo, clock)
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

    @app.get('/workspace.css')
    def workspace_styles():
        return FileResponse(assets / 'workspace.css', media_type='text/css')

    @app.get('/future-school-logo.png')
    def runtime_logo():
        return FileResponse(assets / 'future-school-logo.png', media_type='image/png')

    @app.get('/health/ready')
    def health():
        with store.connect() as db:
            row = db.execute("SELECT applied_at FROM schema_migrations WHERE version='fresh-start-runtime-v1'").fetchone()
        return {'status': 'ready' if row else 'not_initialized',
                'schema': 'fresh-start-runtime-v1', 'initialized': bool(row),
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

    @app.get('/v1/admin/classes')
    def admin_classes(token: Token):
        return store.admin_classes(token)

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
        return store.activate_tablet(data.activation_code)

    @app.get('/v1/tablet/bootstrap')
    def tablet_bootstrap(token: Token):
        return store.tablet_bootstrap(token)

    @app.post('/v1/tablet/checkins', status_code=201)
    def tablet_checkin(data: TabletCheckinInput, response: Response, token: Token):
        result = store.tablet_checkin(token, data.model_dump())
        if result['duplicate']:
            response.status_code = 200
        return result

    @app.get('/v1/teacher/lessons')
    def teacher_lessons(token: Token, date: str | None = None):
        return store.teacher_lessons(token, date)

    @app.get('/v1/teacher/lessons/{lesson_id}')
    def teacher_lesson(lesson_id: str, token: Token):
        return store.teacher_lesson(token, lesson_id)

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

    @app.put('/v1/teacher/students/{student_id}/balance')
    def teacher_balance(student_id: str, data: BalanceAdjustmentInput, token: Token):
        return store.teacher_adjust_balance(token, student_id, data.model_dump())

    return app
