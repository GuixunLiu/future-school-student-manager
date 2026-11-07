"""Disposable cancelled-lesson browser fixture; fictional data only."""
import os

import psycopg
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from business.security import password_hash
from management.runtime_api import create_app
from test_runtime_service import ADMIN_TOKEN, RuntimeBusinessTests


if __name__ == '__main__':
    RuntimeBusinessTests.setUpClass()
    fixture = RuntimeBusinessTests('test_http_admin_cancelled_lesson_detail_corrections_and_tablet_receipt')
    fixture.setUp()
    try:
        with psycopg.connect(fixture.conninfo) as connection:
            connection.execute(
                'INSERT INTO user_credentials VALUES(%s,%s,%s,%s)',
                ('org-fiction', 'admin-a', password_hash('Fictional admin password 2026!'), 1),
            )
        setup_client = TestClient(create_app(fixture.conninfo, lambda: 1000, allow_test_client=True))
        admin = {'Authorization': 'Bearer ' + ADMIN_TOKEN}
        for index, (start, end) in enumerate(((1100, 1200), (1300, 1400))):
            created = setup_client.post('/v1/admin/lessons', headers=admin, json=fixture.lesson_data(
                'browser-cancel-create-' + str(index), starts_at=start, ends_at=end, charge_units=1))
            assert created.status_code == 201, created.text
            cancelled = setup_client.post(
                '/v1/admin/lessons/' + created.json()['lesson_id'] + '/cancel', headers=admin,
                json=fixture.cancel_data('browser-cancel-' + str(index)))
            assert cancelled.status_code == 200, cancelled.text
        setup_client.close()

        app = create_app(fixture.conninfo, lambda: 1000)
        server = uvicorn.Server(uvicorn.Config(
            app, host='127.0.0.1', port=18127, access_log=False, log_level='warning'))

        @app.post('/__fixture_stop')
        def stop(x_fixture_stop: str = Header(default='')):
            if x_fixture_stop != os.environ['CANCELLED_FIXTURE_STOP']:
                return JSONResponse({}, status_code=403)
            server.should_exit = True
            return {'stopped': True}

        print('FICTIONAL_CANCELLED_READY', flush=True)
        server.run()
    finally:
        fixture.doCleanups()
        RuntimeBusinessTests.tearDownClass()
