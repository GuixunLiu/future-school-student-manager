"""Isolated tests for the read-only target preflight and its redaction boundary."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SERVER = Path(__file__).resolve().parents[1]
MODULE_PATH = SERVER / 'ops' / 'runtime-preflight.py'
SPEC = importlib.util.spec_from_file_location('runtime_preflight', MODULE_PATH)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class RuntimePreflightTests(unittest.TestCase):
    def test_requirements_are_exactly_pinned_and_complete(self):
        pins = preflight.read_pins(SERVER / 'requirements-runtime.txt')
        self.assertEqual(preflight.REQUIRED_PACKAGES, set(pins))
        with tempfile.TemporaryDirectory(prefix='runtime-preflight-pins-') as folder:
            target = Path(folder) / 'requirements.txt'
            target.write_text('fastapi==0.162.0\nfastapi==0.162.0\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'duplicate_requirement'):
                preflight.read_pins(target)
            target.write_text('fastapi>=0.162.0\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'invalid_requirement_line'):
                preflight.read_pins(target)

    def test_loopback_port_check_never_replaces_an_existing_listener(self):
        port = preflight.check_port(0)
        self.assertEqual('fail', port['status'])
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            occupied = listener.getsockname()[1]
            self.assertEqual('fail', preflight.check_port(occupied)['status'])
            with socket.socket() as probe:
                probe.settimeout(1)
                self.assertEqual(0, probe.connect_ex(('127.0.0.1', occupied)))
        self.assertEqual('pass', preflight.check_port(occupied)['status'])

    def test_private_environment_requires_owner_and_zero_group_other_access(self):
        path = Mock()
        path.is_symlink.return_value = False
        path.is_file.return_value = True
        path.stat.return_value = types.SimpleNamespace(st_mode=0o100640, st_uid=1000)
        with patch.object(preflight.os, 'name', 'posix'), patch.object(
                preflight.os, 'geteuid', return_value=1000, create=True):
            self.assertEqual('group_and_other_access_must_be_zero',
                             preflight.check_private_environment(path)['detail'])
            path.stat.return_value.st_mode = 0o100600
            self.assertEqual('pass', preflight.check_private_environment(path)['status'])
            path.stat.return_value.st_uid = 2000
            self.assertEqual('owner_must_be_root_or_current_user',
                             preflight.check_private_environment(path)['detail'])

    def test_database_failure_never_leaks_dsn_or_driver_error_text(self):
        secret = 'host=example.invalid password=do-not-print-this dbname=private'
        driver = types.ModuleType('psycopg')
        conninfo = types.ModuleType('psycopg.conninfo')
        conninfo.make_conninfo = lambda value, **changes: value

        class DeliberateError(Exception):
            pass

        def connect(_):
            raise DeliberateError('connection failed with ' + secret)

        driver.connect = connect
        with patch.dict(sys.modules, {'psycopg': driver, 'psycopg.conninfo': conninfo}):
            result = preflight.check_database(secret)
        self.assertEqual('fail', result['status'])
        self.assertEqual('DeliberateError', result['detail'])
        serialized = json.dumps(result)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('do-not-print-this', serialized)

    def test_database_check_is_read_only_and_rolls_back(self):
        driver = types.ModuleType('psycopg')
        conninfo = types.ModuleType('psycopg.conninfo')
        captured = {}
        conninfo.make_conninfo = lambda value, **changes: captured.update(changes) or value
        connection = Mock()
        driver.connect = Mock(return_value=connection)
        with patch.dict(sys.modules, {'psycopg': driver, 'psycopg.conninfo': conninfo}):
            result = preflight.check_database('host=example.invalid password=private')
        self.assertEqual('pass', result['status'])
        self.assertEqual(3, captured['connect_timeout'])
        self.assertEqual(['BEGIN READ ONLY', 'SELECT 1'],
                         [call.args[0] for call in connection.execute.call_args_list])
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_installed_candidate_dependencies_match_and_failures_are_redacted(self):
        result = preflight.check_dependencies(
            SERVER, SERVER / '.test-venv' / 'Scripts' / 'python.exe')
        self.assertEqual('pass', result['status'], result)
        with patch.object(preflight, 'read_pins', return_value={'fastapi': '0.162.0'}), patch.object(
                preflight.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 1, stdout='', stderr='password=do-not-print-this')):
            failed = preflight.check_dependencies(SERVER, Path('/not/used'))
        self.assertEqual('dependency_inspection_failed', failed['detail'])
        self.assertNotIn('do-not-print-this', json.dumps(failed))


    def test_real_cli_returns_safe_json_and_matching_exit_status(self):
        with tempfile.TemporaryDirectory(prefix='runtime-preflight-cli-') as folder:
            environment = Path(folder) / 'private.env'
            environment.write_text('RUNTIME_DATABASE_DSN=password=do-not-print-this\n', encoding='utf-8')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            result = subprocess.run([
                sys.executable, '-B', str(MODULE_PATH), '--runtime-root', str(SERVER),
                '--python', sys.executable, '--environment-file', str(environment),
                '--port', str(port), '--min-free-gib', '0.0001',
            ], capture_output=True, text=True, timeout=30, check=False)
            report = json.loads(result.stdout)
            self.assertEqual(preflight.FORMAT, report['format'])
            self.assertEqual(0 if report['ok'] else 1, result.returncode)
            self.assertIn('skip', [item['status'] for item in report['checks']
                                   if item['name'] == 'postgresql_read_only'])
            self.assertNotIn('do-not-print-this', result.stdout)
            self.assertNotIn('do-not-print-this', result.stderr)


if __name__ == '__main__':
    unittest.main()
