"""Infrastructure probe only. No student data, bindings or WeChat sender yet."""

import os

from fastapi import FastAPI

VERSION = "0.1.0"
MODE = "infrastructure_only"


def create_app() -> FastAPI:
    # Fail closed: an environment typo must not imply business features are live.
    if os.environ.get("WXNOTIFY_MODE", MODE) != MODE:
        raise RuntimeError("This release supports infrastructure_only mode only")
    for setting in ("WXNOTIFY_ENABLE_WECHAT", "WXNOTIFY_ENABLE_STUDENT_SYNC"):
        if os.environ.get(setting, "0") != "0":
            raise RuntimeError("Business features are not implemented in this release")

    api = FastAPI(
        title="wxnotify infrastructure probe",
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )

    @api.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @api.api_route("/", methods=["GET", "HEAD"])
    async def index():
        return {"service": "wxnotify", "version": VERSION, "mode": MODE}

    @api.api_route("/health/live", methods=["GET", "HEAD"])
    async def liveness():
        return {"status": "ok"}

    @api.api_route("/health/ready", methods=["GET", "HEAD"])
    async def readiness():
        # 'ready' means the probe is ready, NOT that parent messaging is ready.
        return {
            "status": "ready",
            "version": VERSION,
            "mode": MODE,
            "business_ready": False,
            "wechat_sending_enabled": False,
            "student_sync_enabled": False,
            "parent_binding_enabled": False,
        }

    return api


app = create_app()
