"""Code-only release archive and isolated ASGI entry verification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / 'ops' / 'package-runtime-release.py'
SPEC = importlib.util.spec_from_file_location('package_runtime_release', MODULE_PATH)
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


class RuntimeReleasePackageTests(unittest.TestCase):
    def test_archive_is_deterministic_complete_and_code_only(self):
        first, manifest = packager.build()
        second, _ = packager.build()
        self.assertEqual(first, second)
        self.assertEqual(manifest, packager.verify(first))
        self.assertEqual('management.runtime_main:app', manifest['entrypoint'])
        self.assertFalse(manifest['contains_business_data'])
        self.assertFalse(manifest['contains_credentials'])
        self.assertFalse(manifest['production_ready'])
        self.assertFalse(manifest['wechat_sending_enabled'])
        self.assertIn('management/web/admin-schedule.js', manifest['files'])
        self.assertIn('management/web/report-history.js', manifest['files'])
        self.assertIn('management/web/face-models/sface_int8bq.onnx', manifest['files'])
        self.assertIn('management/runtime_release.py', manifest['files'])
        self.assertIn('management/runtime_reports.py', manifest['files'])
        self.assertIn('ops/future-school-runtime.service.template', manifest['files'])
        self.assertIn('ops/runtime-preflight.py', manifest['files'])
        self.assertIn('ops/render-runtime-service.py', manifest['files'])
        self.assertIn('ISOLATED-LAUNCH-RUNBOOK.md', manifest['files'])
        self.assertIn('ACCEPTANCE-REPORT.md', manifest['files'])
        self.assertIn('GO-LIVE-SIGNOFF.md', manifest['files'])
        self.assertNotIn('business/schema.sql', manifest['files'])
        with zipfile.ZipFile(__import__('io').BytesIO(first)) as archive:
            self.assertFalse(any(name.startswith(('.', '/')) or '..' in name for name in archive.namelist()))
            self.assertNotIn('server/.test-venv', '\n'.join(archive.namelist()))

    def test_output_and_checksum_never_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='runtime-package-output-') as folder:
            target = Path(folder) / 'candidate.zip'
            result = packager.write(target)
            self.assertTrue(target.is_file())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), result['sha256'])
            self.assertIn(result['sha256'], target.with_suffix('.zip.sha256').read_text('ascii'))
            original = target.read_bytes()
            with self.assertRaises(FileExistsError):
                packager.write(target)
            self.assertEqual(original, target.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
    def test_isolated_archive_initializes_postgresql_and_serves_real_asgi_entry(self):
        import pgserver
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from urllib.parse import urlsplit

        with tempfile.TemporaryDirectory(prefix='runtime-package-isolated-') as folder:
            root = Path(folder)
            raw, _ = packager.build()
            with zipfile.ZipFile(__import__('io').BytesIO(raw)) as archive:
                archive.extractall(root / 'release')
            server = pgserver.get_server(str(root / 'pg'), cleanup_mode='stop')
            try:
                parsed = urlsplit(server.get_uri())
                base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                     user=parsed.username, password=parsed.password)
                with psycopg.connect(base, dbname='postgres', autocommit=True) as connection:
                    connection.execute(sql.SQL('CREATE DATABASE {}').format(
                        sql.Identifier('runtime_rehearsal_package')))
                dsn = make_conninfo(base, dbname='runtime_rehearsal_package')
                program = r'''
import json, os
from fastapi.testclient import TestClient
from management.runtime_release import initialize_local
initialize_local(os.environ['RUNTIME_DATABASE_DSN'], True)
from management.runtime_main import app
# The packaged entry uses the production guard; emulate only its loopback HTTP client.
client = TestClient(app, client=('127.0.0.1', 12345))
with client:
    health = client.get('/health/ready', headers={'host': '127.0.0.1'})
    page = client.get('/runtime', headers={'host': '127.0.0.1'})
    script = client.get('/admin-schedule.js', headers={'host': '127.0.0.1'})
    assert health.status_code == 200, health.text
    assert health.json()['status'] == 'ready'
    assert health.json()['production_ready'] is False
    assert health.json()['wechat_sending_enabled'] is False
    assert page.status_code == 200 and '统一工作台' in page.text
    assert script.status_code == 200 and 'review-cancelled' in script.text
print(json.dumps({'isolated_entry': True, 'health': 'ready', 'runtime_page': True}))
'''
                env = os.environ.copy()
                env['RUNTIME_DATABASE_DSN'] = dsn
                env['PYTHONPATH'] = str(root / 'release')
                result = subprocess.run([sys.executable, '-c', program], cwd=root / 'release', env=env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(json.loads(result.stdout)['isolated_entry'])
            finally:
                server.cleanup()


if __name__ == '__main__':
    unittest.main()
