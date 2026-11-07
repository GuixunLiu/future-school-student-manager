"""Isolated rendering tests; no target paths are accessed and no service is installed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SERVER = Path(__file__).resolve().parents[1]
MODULE_PATH = SERVER / 'ops' / 'render-runtime-service.py'
SPEC = importlib.util.spec_from_file_location('render_runtime_service', MODULE_PATH)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)

ROOT = '/opt/future-school/releases/fiction'
PYTHON = '/opt/future-school/venv/bin/python'
ENVIRONMENT = '/etc/future-school/fiction.env'


class RuntimeLaunchConfigTests(unittest.TestCase):
    def test_real_cli_renders_loopback_unit_without_installing_or_starting(self):
        with tempfile.TemporaryDirectory(prefix='runtime-render-cli-') as folder:
            output = Path(folder) / 'fiction.service'
            result = subprocess.run([
                sys.executable, '-B', str(MODULE_PATH), '--runtime-root', ROOT,
                '--python', PYTHON, '--environment-file', ENVIRONMENT,
                '--port', '18090', '--output', str(output),
            ], capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            receipt = json.loads(result.stdout)
            raw = output.read_bytes()
            text = raw.decode('utf-8')
            self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt['sha256'])
            self.assertEqual(len(raw), receipt['bytes'])
            self.assertTrue(receipt['local_output_only'])
            self.assertFalse(receipt['installed'])
            self.assertFalse(receipt['started'])
            self.assertIn('WorkingDirectory=' + ROOT, text)
            self.assertIn('EnvironmentFile=' + ENVIRONMENT, text)
            self.assertIn('ExecStart=' + PYTHON + ' -m uvicorn', text)
            self.assertIn('--host 127.0.0.1 --port 18090 --workers 1', text)
            self.assertIsNone(re.search(r'@[A-Z][A-Z0-9_]*@', text))
            self.assertNotIn('RUNTIME_DATABASE_DSN=', text)
            self.assertNotIn('FACE_PROFILE_KEY=', text)

    def test_output_is_exclusive_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix='runtime-render-output-') as folder:
            output = Path(folder) / 'candidate.service'
            raw = renderer.render(ROOT, PYTHON, ENVIRONMENT, 18090)
            receipt = renderer.write(raw, output)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt['sha256'])
            with self.assertRaises(FileExistsError):
                renderer.write(b'changed', output)
            self.assertEqual(raw, output.read_bytes())
            with self.assertRaises(ValueError):
                renderer.write(raw, Path(folder) / 'wrong.txt')
            self.assertFalse((Path(folder) / 'wrong.txt').exists())

    def test_unsafe_target_paths_and_ports_are_rejected(self):
        invalid_roots = (
            'relative/path', '/opt/../etc', '/opt/./release',
            '/opt/future school', '/opt/future-school\nExecStart=/bad',
            '/opt/%i/release', '/',
        )
        for value in invalid_roots:
            with self.subTest(root=value), self.assertRaises(ValueError):
                renderer.render(value, PYTHON, ENVIRONMENT, 18090)
        for value in (0, 65536, True, '18090'):
            with self.subTest(port=value), self.assertRaises(ValueError):
                renderer.render(ROOT, PYTHON, ENVIRONMENT, value)


if __name__ == '__main__':
    unittest.main()
