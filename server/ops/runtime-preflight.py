"""Read-only Linux target preflight; never prints configuration values or errors verbatim."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys


FORMAT = 'future-school-runtime-preflight-v1'
REQUIRED_PACKAGES = {'fastapi', 'uvicorn', 'psycopg', 'cryptography'}
PIN = re.compile(r'^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9_.!+-]+)$')


def outcome(name, status, detail):
    if status not in {'pass', 'fail', 'skip'}:
        raise ValueError('Invalid preflight status')
    return {'name': name, 'status': status, 'detail': detail}


def check_platform():
    if platform.system() != 'Linux':
        return outcome('platform', 'fail', 'linux_required')
    if sys.version_info < (3, 11):
        return outcome('platform', 'fail', 'python_3_11_or_newer_required')
    return outcome('platform', 'pass', platform.machine() or 'linux')


def absolute_path(path):
    return path.is_absolute() and not path.is_symlink()


def valid_venv_python_symlink(path):
    """Permit the standard Linux venv launcher, not an arbitrary Python symlink."""
    if path.parent.name != 'bin':
        return False
    config = path.parent.parent / 'pyvenv.cfg'
    if config.is_symlink() or not config.is_file():
        return False
    try:
        return path.resolve(strict=True).is_file()
    except (OSError, RuntimeError):
        return False


def check_directories(root, python, environment_file):
    if not all(path.is_absolute() for path in (root, python, environment_file)):
        return outcome('paths', 'fail', 'absolute_paths_required')
    if root.is_symlink() or not root.is_dir():
        return outcome('paths', 'fail', 'runtime_root_must_be_a_real_directory')
    if not os.access(root, os.R_OK | os.X_OK):
        return outcome('paths', 'fail', 'runtime_root_not_readable')
    if python.is_symlink() and not valid_venv_python_symlink(python):
        return outcome('paths', 'fail', 'python_symlink_requires_valid_venv')
    if not python.is_file() or not os.access(python, os.R_OK):
        return outcome('paths', 'fail', 'python_executable_not_readable')
    if os.name == 'posix' and not os.access(python, os.X_OK):
        return outcome('paths', 'fail', 'python_executable_not_executable')
    return check_private_environment(environment_file)


def check_private_environment(path):
    if path.is_symlink() or not path.is_file():
        return outcome('environment_file', 'fail', 'private_regular_file_required')
    if os.name != 'posix':
        return outcome('environment_file', 'fail', 'posix_permission_check_required')
    try:
        info = path.stat()
        if stat.S_IMODE(info.st_mode) & 0o077:
            return outcome('environment_file', 'fail', 'group_and_other_access_must_be_zero')
        if info.st_uid not in {0, os.geteuid()}:
            return outcome('environment_file', 'fail', 'owner_must_be_root_or_current_user')
    except OSError:
        return outcome('environment_file', 'fail', 'environment_file_stat_failed')
    return outcome('environment_file', 'pass', 'private_permissions_verified_without_reading_values')


def check_disk(root, minimum_gib):
    try:
        free = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
    except (OSError, AttributeError):
        return outcome('disk', 'fail', 'statvfs_unavailable')
    required = int(minimum_gib * 1024 ** 3)
    return outcome('disk', 'pass' if free >= required else 'fail',
                   {'free_gib': round(free / 1024 ** 3, 2), 'minimum_gib': minimum_gib})


def check_port(port):
    if not 1 <= port <= 65535:
        return outcome('loopback_port', 'fail', 'invalid_port')
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', port))
        return outcome('loopback_port', 'pass', 'loopback_bind_available_at_check_time')
    except OSError:
        return outcome('loopback_port', 'fail', 'loopback_port_unavailable')


def read_pins(path):
    pins = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError('invalid_requirement_line')
        name, version = match.groups()
        name = name.lower()
        if name in pins:
            raise ValueError('duplicate_requirement')
        pins[name] = version
    if set(pins) != REQUIRED_PACKAGES:
        raise ValueError('unexpected_requirement_set')
    return pins


def check_dependencies(root, python):
    try:
        pins = read_pins(root / 'requirements-runtime.txt')
    except (OSError, UnicodeError, ValueError) as error:
        return outcome('dependencies', 'fail', 'requirements_' + str(error))
    program = ('import importlib.metadata as m,json,sys;'
               'print(json.dumps({n:m.version(n) for n in json.loads(sys.argv[1])}))')
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        completed = subprocess.run(
            [str(python), '-B', '-c', program, json.dumps(pins, sort_keys=True)],
            capture_output=True, text=True, timeout=20, env=env, check=False)
        if completed.returncode != 0:
            return outcome('dependencies', 'fail', 'dependency_inspection_failed')
        installed = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return outcome('dependencies', 'fail', 'dependency_inspection_unavailable')
    mismatched = sorted(name for name, version in pins.items() if installed.get(name) != version)
    if mismatched:
        return outcome('dependencies', 'fail', {'version_mismatch': mismatched})
    return outcome('dependencies', 'pass', 'all_runtime_versions_match')


def check_systemd():
    available = all(shutil.which(name) for name in ('systemctl', 'systemd-analyze'))
    running = Path('/run/systemd/system').is_dir()
    if not available or not running:
        return outcome('systemd', 'fail', 'running_systemd_and_verification_tools_required')
    return outcome('systemd', 'pass', 'systemd_available; unit verification remains a separate step')


def check_database(dsn):
    if not dsn:
        return outcome('postgresql_read_only', 'fail', 'private_environment_variable_missing')
    try:
        import psycopg
        from psycopg.conninfo import make_conninfo
        safe_dsn = make_conninfo(dsn, connect_timeout=3,
                                 application_name='future-school-preflight-readonly')
        connection = psycopg.connect(safe_dsn)
        try:
            connection.execute('BEGIN READ ONLY')
            connection.execute('SELECT 1').fetchone()
        finally:
            connection.rollback()
            connection.close()
        return outcome('postgresql_read_only', 'pass', 'select_1_succeeded_and_transaction_rolled_back')
    except Exception as error:
        # Driver errors can contain host/user/database details; never return raw messages.
        return outcome('postgresql_read_only', 'fail', type(error).__name__)


def run(args):
    checks = [
        check_platform(),
        check_directories(args.runtime_root, args.python, args.environment_file),
        check_disk(args.runtime_root, args.min_free_gib),
        check_port(args.port),
        check_dependencies(args.runtime_root, args.python),
        check_systemd(),
    ]
    if args.database:
        checks.append(check_database(os.environ.get('RUNTIME_DATABASE_DSN', '')))
    else:
        checks.append(outcome('postgresql_read_only', 'skip', 'pass --database with the private variable already set'))
    return {
        'format': FORMAT,
        'ok': all(item['status'] != 'fail' for item in checks),
        'checks': checks,
        'notes': [
            'No files, services, packages, firewall rules or database records are modified.',
            'Port availability is only a point-in-time check; recheck immediately before isolated startup.',
            'A passing preflight is not production deployment or HTTPS approval.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description='Read-only unified runtime target preflight')
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--environment-file', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--min-free-gib', type=float, default=5.0)
    parser.add_argument('--database', action='store_true')
    args = parser.parse_args()
    if not 0 < args.min_free_gib <= 1024:
        parser.error('--min-free-gib must be greater than 0 and at most 1024')
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
