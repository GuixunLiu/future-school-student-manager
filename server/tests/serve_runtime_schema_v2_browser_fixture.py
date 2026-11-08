"""Disposable v2 browser fixture; local PostgreSQL and fictional data only."""
import os

import psycopg
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse

from business.security import password_hash
from management.runtime_api import create_app
from test_runtime_service import RuntimeBusinessTests


if __name__ == '__main__':
    RuntimeBusinessTests.setUpClass()
    fixture = RuntimeBusinessTests('test_tablet_bootstrap_contains_only_authorized_classes_and_versions')
    fixture.setUp()
    try:
        with psycopg.connect(fixture.conninfo) as connection:
            connection.execute(
                'INSERT INTO user_credentials VALUES(%s,%s,%s,%s)',
                ('org-fiction', 'admin-a', password_hash('Fictional admin password 2026!'), 1),
            )
        app = create_app(fixture.conninfo, lambda: 1000)
        server = uvicorn.Server(uvicorn.Config(
            app, host='127.0.0.1', port=18128, access_log=False, log_level='warning'))

        @app.post('/__fixture_stop')
        def stop(x_fixture_stop: str = Header(default='')):
            if x_fixture_stop != os.environ['RUNTIME_V2_FIXTURE_STOP']:
                return JSONResponse({}, status_code=403)
            server.should_exit = True
            return {'stopped': True}

        @app.post('/__fixture_failed_report')
        def failed_report(x_fixture_stop: str = Header(default='')):
            if x_fixture_stop != os.environ['RUNTIME_V2_FIXTURE_STOP']:
                return JSONResponse({}, status_code=403)
            with psycopg.connect(fixture.conninfo) as connection:
                connection.execute('''INSERT INTO report_runs
                    (id,org_id,kind,period_start,version,status,payload,error,requested_by,created_at,completed_at)
                    VALUES('browser-failed-monthly','org-fiction','monthly','1970-01',1,'failed',NULL,
                           'report_generation_failed','admin-a',900,999)''')
            return {'run_id': 'browser-failed-monthly'}

        print('FICTIONAL_RUNTIME_V2_READY', flush=True)
        server.run()
    finally:
        fixture.doCleanups()
        RuntimeBusinessTests.tearDownClass()
