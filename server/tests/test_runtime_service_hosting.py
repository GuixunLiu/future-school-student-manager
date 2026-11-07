"""Static service-unit checks and a real same-port process restart rehearsal."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from management import runtime_schema
from tests import test_runtime_service as fixtures


ADMIN_TOKEN = fixtures.ADMIN_TOKEN
DEVICE_TOKEN = fixtures.DEVICE_TOKEN
checkin = fixtures.checkin
SERVER = Path(__file__).resolve().parents[1]
TEMPLATE = SERVER / 'ops' / 'future-school-runtime.service.template'


def request(port, method, path, token=None, payload=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        headers = {'Host': f'127.0.0.1:{port}', 'Accept': 'application/json'}
        body = None
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(payload, separators=(',', ':'))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        connection.close()


class RuntimeServiceHostingTests(unittest.TestCase):
    def test_systemd_template_is_loopback_private_and_hardened(self):
        text = TEMPLATE.read_text(encoding='utf-8')
        for placeholder in ('@RUNTIME_ROOT@', '@PYTHON@', '@ENVIRONMENT_FILE@', '@PORT@'):
            self.assertIn(placeholder, text)
        for setting in (
            'EnvironmentFile=@ENVIRONMENT_FILE@',
            '--host 127.0.0.1', '--workers 1', '--no-proxy-headers',
            '--no-server-header', '--no-access-log', '--ws none',
            'Restart=on-failure', 'DynamicUser=true', 'NoNewPrivileges=true',
            'PrivateTmp=true', 'ProtectSystem=strict', 'MemoryMax=512M',
            'CPUQuota=50%', 'TasksMax=64',
        ):
            self.assertIn(setting, text)
        self.assertNotIn('RUNTIME_DATABASE_DSN=', text)
        self.assertNotIn('FACE_PROFILE_KEY=', text)
        self.assertNotIn('47.97.226.219', text)
        self.assertNotIn('Wx@', text)

    @unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
    def test_real_uvicorn_restart_preserves_session_and_checkin_idempotency(self):
        import pgserver

        with tempfile.TemporaryDirectory(prefix='runtime-service-hosting-') as folder:
            root = Path(folder)
            database = pgserver.get_server(str(root / 'pg'), cleanup_mode='stop')
            process = None
            log = None
            log_path = root / 'uvicorn.log'
            try:
                parsed = urlsplit(database.get_uri())
                base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                     user=parsed.username, password=parsed.password)
                with psycopg.connect(base, dbname='postgres', autocommit=True) as connection:
                    connection.execute(sql.SQL('CREATE DATABASE {}').format(
                        sql.Identifier('runtime_rehearsal_service_hosting')))
                dsn = make_conninfo(base, dbname='runtime_rehearsal_service_hosting')
                with psycopg.connect(dsn) as connection:
                    connection.execute(runtime_schema.full_schema())
                    fixtures.RuntimeBusinessTests.seed(connection)
                    # The shared fixture uses a fixed 1970 clock; the real ASGI entry uses now.
                    connection.execute('UPDATE auth_sessions SET expires_at=4102444800')
                    connection.execute('UPDATE device_credentials SET expires_at=4102444800')

                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    port = listener.getsockname()[1]
                env = os.environ.copy()
                env['RUNTIME_DATABASE_DSN'] = dsn
                env['PYTHONPATH'] = str(SERVER)
                command = [
                    sys.executable, '-m', 'uvicorn', 'management.runtime_main:app',
                    '--host', '127.0.0.1', '--port', str(port), '--workers', '1',
                    '--no-proxy-headers', '--no-server-header', '--no-access-log',
                    '--no-use-colors', '--ws', 'none', '--limit-concurrency', '32',
                    '--timeout-keep-alive', '5', '--timeout-graceful-shutdown', '10',
                ]

                def start():
                    stream = log_path.open('ab')
                    child = subprocess.Popen(command, cwd=SERVER, env=env,
                                             stdout=stream, stderr=subprocess.STDOUT)
                    return child, stream

                def stop(child, stream):
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=10)
                    else:
                        child.wait(timeout=5)
                    stream.close()

                def wait_ready(child):
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if child.poll() is not None:
                            break
                        try:
                            status, body = request(port, 'GET', '/health/ready')
                            if status == 200 and body['status'] == 'ready':
                                return body
                        except (OSError, http.client.HTTPException):
                            time.sleep(0.1)
                    raise AssertionError('Uvicorn did not become ready:\n' +
                                         log_path.read_text(encoding='utf-8', errors='replace'))

                process, log = start()
                health = wait_ready(process)
                self.assertTrue(health['initialized'])
                self.assertFalse(health['production_ready'])
                self.assertEqual(200, request(port, 'GET', '/v1/me', ADMIN_TOKEN)[0])
                first_status, first = request(port, 'POST', '/v1/tablet/checkins',
                                              DEVICE_TOKEN, checkin('service-restart-checkin'))
                self.assertEqual(201, first_status, first)
                self.assertFalse(first['duplicate'])
                self.assertEqual(9, first['balance_units'])
                stop(process, log)
                process, log = None, None

                process, log = start()
                wait_ready(process)
                self.assertEqual(200, request(port, 'GET', '/v1/me', ADMIN_TOKEN)[0])
                second_status, second = request(port, 'POST', '/v1/tablet/checkins',
                                                DEVICE_TOKEN, checkin('service-restart-checkin'))
                self.assertEqual(200, second_status, second)
                self.assertTrue(second['duplicate'])
                self.assertEqual(first['event_id'], second['event_id'])
                self.assertEqual(9, second['balance_units'])
                with psycopg.connect(dsn) as connection:
                    counts = connection.execute('''SELECT
                        (SELECT count(*) FROM attendance_events),
                        (SELECT count(*) FROM balance_ledger),
                        (SELECT units FROM student_balances WHERE student_id='student-a')''').fetchone()
                self.assertEqual((1, 3, 9), counts)
            finally:
                if process is not None:
                    stop(process, log)
                database.cleanup()


class RuntimeServiceFailureTests(unittest.TestCase):
    @staticmethod
    def free_port():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            return listener.getsockname()[1]

    @staticmethod
    def start(port, dsn, log_path):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(SERVER)
        if dsn is None:
            env.pop('RUNTIME_DATABASE_DSN', None)
        else:
            env['RUNTIME_DATABASE_DSN'] = dsn
        command = [
            sys.executable, '-m', 'uvicorn', 'management.runtime_main:app',
            '--host', '127.0.0.1', '--port', str(port), '--workers', '1',
            '--no-proxy-headers', '--no-server-header', '--no-access-log',
            '--no-use-colors', '--ws', 'none', '--limit-concurrency', '32',
            '--timeout-keep-alive', '5', '--timeout-graceful-shutdown', '10',
        ]
        stream = log_path.open('ab')
        child = subprocess.Popen(command, cwd=SERVER, env=env,
                                 stdout=stream, stderr=subprocess.STDOUT)
        return child, stream

    @staticmethod
    def stop(child, stream):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        else:
            child.wait(timeout=5)
        stream.close()

    @staticmethod
    def wait_ready(child, port, log_path):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            try:
                status, body = request(port, 'GET', '/health/ready')
                if status == 200 and body['status'] == 'ready':
                    return body
            except (OSError, http.client.HTTPException):
                time.sleep(0.1)
        raise AssertionError('Uvicorn did not become ready:\n' +
                             log_path.read_text(encoding='utf-8', errors='replace'))

    def test_missing_private_dsn_refuses_to_start_or_open_port(self):
        with tempfile.TemporaryDirectory(prefix='runtime-missing-dsn-') as folder:
            port = self.free_port()
            log_path = Path(folder) / 'missing-dsn.log'
            process, log = self.start(port, None, log_path)
            try:
                self.assertNotEqual(0, process.wait(timeout=30))
                self.stop(process, log)
                process = None
                text = log_path.read_text(encoding='utf-8', errors='replace')
                self.assertIn('RUNTIME_DATABASE_DSN must be supplied', text)
                with socket.socket() as probe:
                    probe.settimeout(1)
                    self.assertNotEqual(0, probe.connect_ex(('127.0.0.1', port)))
            finally:
                if process is not None:
                    self.stop(process, log)

    def test_occupied_port_fails_without_replacing_the_listener(self):
        with tempfile.TemporaryDirectory(prefix='runtime-occupied-port-') as folder:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                listener.listen(2)
                port = listener.getsockname()[1]
                log_path = Path(folder) / 'occupied-port.log'
                process, log = self.start(
                    port, 'host=127.0.0.1 port=1 dbname=runtime_rehearsal_unused user=fiction', log_path)
                try:
                    self.assertNotEqual(0, process.wait(timeout=30))
                    self.stop(process, log)
                    process = None
                    text = log_path.read_text(encoding='utf-8', errors='replace').lower()
                    self.assertIn('attempting to bind', text)
                    with socket.socket() as probe:
                        probe.settimeout(1)
                        self.assertEqual(0, probe.connect_ex(('127.0.0.1', port)))
                finally:
                    if process is not None:
                        self.stop(process, log)

    @unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
    def test_database_outage_returns_503_then_reconfigured_restart_recovers_once(self):
        import pgserver

        with tempfile.TemporaryDirectory(prefix='runtime-database-outage-') as folder:
            root = Path(folder)
            data = root / 'pg'
            database = pgserver.get_server(str(data), cleanup_mode='stop')
            process = None
            log = None
            log_path = root / 'uvicorn.log'
            try:
                parsed = urlsplit(database.get_uri())
                base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                     user=parsed.username, password=parsed.password)
                with psycopg.connect(base, dbname='postgres', autocommit=True) as connection:
                    connection.execute(sql.SQL('CREATE DATABASE {}').format(
                        sql.Identifier('runtime_rehearsal_outage')))
                dsn = make_conninfo(base, dbname='runtime_rehearsal_outage', connect_timeout=1)
                with psycopg.connect(dsn) as connection:
                    connection.execute(runtime_schema.full_schema())
                    fixtures.RuntimeBusinessTests.seed(connection)
                    connection.execute('UPDATE auth_sessions SET expires_at=4102444800')
                    connection.execute('UPDATE device_credentials SET expires_at=4102444800')

                port = self.free_port()
                process, log = self.start(port, dsn, log_path)
                self.wait_ready(process, port, log_path)
                database.cleanup()
                failed_status, failed = request(
                    port, 'POST', '/v1/tablet/checkins', DEVICE_TOKEN,
                    checkin('database-outage-checkin'))
                self.assertEqual(503, failed_status, failed)
                self.assertEqual('storage_unavailable', failed['error']['code'])
                self.assertIsNone(process.poll())
                self.stop(process, log)
                process, log = None, None

                database = pgserver.get_server(str(data), cleanup_mode='stop')
                parsed = urlsplit(database.get_uri())
                base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                     user=parsed.username, password=parsed.password)
                dsn = make_conninfo(base, dbname='runtime_rehearsal_outage', connect_timeout=1)
                process, log = self.start(port, dsn, log_path)
                self.wait_ready(process, port, log_path)
                first_status, first = request(
                    port, 'POST', '/v1/tablet/checkins', DEVICE_TOKEN,
                    checkin('database-outage-checkin'))
                self.assertEqual(201, first_status, first)
                self.assertEqual(9, first['balance_units'])
                repeated_status, repeated = request(
                    port, 'POST', '/v1/tablet/checkins', DEVICE_TOKEN,
                    checkin('database-outage-checkin'))
                self.assertEqual(200, repeated_status, repeated)
                self.assertTrue(repeated['duplicate'])
                self.assertEqual(first['event_id'], repeated['event_id'])
                with psycopg.connect(dsn) as connection:
                    counts = connection.execute('''SELECT
                        (SELECT count(*) FROM attendance_events),
                        (SELECT count(*) FROM balance_ledger),
                        (SELECT count(*) FROM api_commands),
                        (SELECT units FROM student_balances WHERE student_id='student-a')''').fetchone()
                self.assertEqual((1, 3, 1, 9), counts)
            finally:
                if process is not None:
                    self.stop(process, log)
                database.cleanup()


if __name__ == '__main__':
    unittest.main()
