"""ASGI entry point for the explicitly configured unified runtime."""
import os

from .runtime_api import create_app


def from_environment():
    dsn = os.environ.get('RUNTIME_DATABASE_DSN', '')
    if not dsn:
        raise RuntimeError('RUNTIME_DATABASE_DSN must be supplied through the private service environment')
    return create_app(dsn)


app = from_environment()
