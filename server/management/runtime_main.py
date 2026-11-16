"""ASGI entry point for the explicitly configured unified runtime."""
import os

from .runtime_api import create_app


def from_environment():
    dsn = os.environ.get('RUNTIME_DATABASE_DSN', '')
    if not dsn:
        raise RuntimeError('RUNTIME_DATABASE_DSN must be supplied through the private service environment')
    hour_scale = int(os.environ.get('RUNTIME_WEB_HOUR_UNIT_SCALE', '1'))
    if hour_scale not in (1, 100):
        raise RuntimeError('RUNTIME_WEB_HOUR_UNIT_SCALE must be 1 or 100')
    return create_app(dsn, web_face_enabled=os.environ.get('RUNTIME_WEB_FACE_ENABLED') == '1',
                      web_hour_unit_scale=hour_scale)


app = from_environment()
