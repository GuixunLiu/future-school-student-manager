"""Strictly local PostgreSQL runtime migration, backup and restore rehearsal.

There is deliberately no production apply mode. Every operation requires an
explicit loopback DSN whose database name starts with runtime_rehearsal_.
"""
import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

import psycopg
from psycopg.rows import dict_row
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from . import fresh_start, fresh_start_postgres
from .runtime_schema import POSTGRES_RUNTIME_SCHEMA, full_schema, upgrade_staff_schema
from .runtime_permissions import provision_first_owner


SCHEMA_VERSION = 'fresh-start-runtime-v2'
RECEIPT_FORMAT = 'future-school-runtime-local-backup-v1'
DATABASE_PREFIX = 'runtime_rehearsal_'


class RehearsalError(ValueError):
    pass


def local_dsn(dsn, enabled):
    if enabled is not True:
        raise RehearsalError('Explicit local rehearsal confirmation is required')
    if not dsn:
        raise RehearsalError('RUNTIME_REHEARSAL_PG_DSN is required')
    try:
        info = conninfo_to_dict(dsn)
    except Exception as error:
        raise RehearsalError('A valid PostgreSQL DSN is required') from error
    if info.get('host') not in ('localhost', '127.0.0.1', '::1'):
        raise RehearsalError('Only an explicit loopback PostgreSQL host is allowed')
    if not info.get('dbname', '').startswith(DATABASE_PREFIX):
        raise RehearsalError('Database name must begin with ' + DATABASE_PREFIX)
    return info


def _public_table_count(connection):
    return connection.execute(
        "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchone()[0]


def _require_empty(connection):
    if _public_table_count(connection) != 0:
        raise RehearsalError('Refusing a nonempty public schema')


def _check_ready(connection):
    row = connection.execute(
        'SELECT applied_at FROM schema_migrations WHERE version=%s', (SCHEMA_VERSION,)).fetchone()
    if not row:
        raise RehearsalError('Runtime schema is not initialized')


def initialize_local(dsn, enabled):
    local_dsn(dsn, enabled)
    with psycopg.connect(dsn) as connection:
        _require_empty(connection)
        connection.execute(full_schema())
        _check_ready(connection)
    return {'format': 'future-school-runtime-local-init-v1', 'rehearsal_only': True,
            'live_apply_authorized': False, 'schema': SCHEMA_VERSION}


def migrate_local(bundle_path, dsn, enabled, now):
    """Migrate reviewed master data and opening balances in one transaction."""
    raw = Path(bundle_path).read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise RehearsalError('Bundle exceeds the size limit')
    bundle = fresh_start.read_json(bundle_path)
    local_dsn(dsn, enabled)
    if type(now) is not int or now < 0:
        raise RehearsalError('A nonnegative integer migration timestamp is required')
    with psycopg.connect(dsn) as connection:
        # The base writer checks an empty public schema and never commits itself.
        base = fresh_start_postgres.populate(connection, bundle, raw, now)
        connection.execute(POSTGRES_RUNTIME_SCHEMA)
        upgrade_staff_schema(connection)
        connection.execute('''INSERT INTO student_balances(org_id,student_id,units,revision,updated_at)
            SELECT org_id,student_id,units,1,%s FROM opening_balances''', (now,))
        rows = connection.execute('''SELECT org_id,student_id,units FROM opening_balances''').fetchall()
        for org_id, student_id, units in rows:
            ledger_id = hashlib.sha256((org_id + '\0' + student_id).encode('utf-8')).hexdigest()
            connection.execute('''INSERT INTO balance_ledger
                (id,org_id,student_id,kind,source_event_id,previous_units,units_delta,units,
                 previous_revision,revision,actor_type,actor_id,reason,created_at)
                VALUES(%s,%s,%s,'opening',%s,0,%s,%s,0,1,'system',NULL,'迁移期初课时',%s)''',
                (ledger_id, org_id, student_id, 'opening:' + student_id, units, units, now))
        balances = connection.execute('SELECT count(*) FROM student_balances').fetchone()[0]
        ledger = connection.execute("SELECT count(*) FROM balance_ledger WHERE kind='opening'").fetchone()[0]
        if balances != len(bundle['students']) or ledger != balances:
            raise RehearsalError('Opening balance projection is incomplete')
        _check_ready(connection)
    return {'format': 'future-school-runtime-local-migration-v1', 'rehearsal_only': True,
            'live_apply_authorized': False, 'schema': SCHEMA_VERSION,
            'source_sha256': base['source_sha256'], 'bundle_sha256': base['bundle_sha256'],
            'counts': base['counts'], 'student_balances_initialized': balances,
            'opening_ledger_initialized': ledger, 'history_migrated': False,
            'credentials_migrated': False}


def _normalized(value):
    if isinstance(value, bytes):
        return {'bytea_hex': value.hex()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return value


def snapshot(dsn, enabled=True):
    """Return only table counts and a canonical digest, never raw business rows."""
    local_dsn(dsn, enabled)
    with psycopg.connect(dsn) as connection:
        _check_ready(connection)
        names = [row[0] for row in connection.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename")]
        tables = {}
        for name in names:
            cursor = connection.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(name)))
            rows = cursor.fetchall()
            columns = [column.name for column in cursor.description]
            normalized = [_normalized(dict(zip(columns, row))) for row in rows]
            normalized.sort(key=lambda row: json.dumps(row, ensure_ascii=True, sort_keys=True,
                                                       separators=(',', ':')))
            tables[name] = normalized
        raw = json.dumps(tables, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return {'sha256': hashlib.sha256(raw).hexdigest(),
                'table_counts': {name: len(rows) for name, rows in tables.items()}}


def _tool(name, explicit=None):
    if explicit:
        path = Path(explicit)
    else:
        try:
            import pgserver
        except ImportError as error:
            raise RehearsalError('pgserver or an explicit PostgreSQL tool path is required') from error
        path = Path(pgserver.__file__).parent / 'pginstall' / 'bin' / name
    if os.name == 'nt' and path.suffix.lower() != '.exe':
        path = path.with_suffix('.exe')
    if not path.is_file():
        raise RehearsalError('PostgreSQL tool is unavailable: ' + name)
    return path


def _command(info, tool):
    args = [str(tool), '--no-password', '--host', info['host'],
            '--port', str(info.get('port', 5432)), '--username', info.get('user', ''),
            '--dbname', info['dbname']]
    env = os.environ.copy()
    if info.get('password'):
        env['PGPASSWORD'] = info['password']
    return args, env


def _run(args, env):
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RehearsalError('PostgreSQL backup tool did not complete') from error
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()[-2000:]
        raise RehearsalError('PostgreSQL backup tool failed; inspect the local database without overwriting it'
                             + (': ' + detail if detail else ''))


def backup_local(dsn, output, enabled, *, pg_dump=None):
    info = local_dsn(dsn, enabled)
    target = Path(output).resolve()
    if target.exists() or target.is_symlink():
        raise RehearsalError('Backup output must be a new file')
    if not target.parent.is_dir():
        raise RehearsalError('Backup parent directory must already exist')
    with psycopg.connect(dsn) as connection:
        _check_ready(connection)
    before = snapshot(dsn, True)
    args, env = _command(info, _tool('pg_dump', pg_dump))
    args[1:1] = ['--format=custom', '--no-owner', '--no-acl', '--file', str(target)]
    try:
        _run(args, env)
        raw = target.read_bytes()
        if not raw.startswith(b'PGDMP'):
            raise RehearsalError('Backup format verification failed')
        if snapshot(dsn, True) != before:
            raise RehearsalError('Source changed during backup; repeat with a quiet local rehearsal database')
    except Exception:
        if target.is_file() and not target.is_symlink():
            target.unlink()
        raise
    return {'format': RECEIPT_FORMAT, 'rehearsal_only': True, 'live_apply_authorized': False,
            'schema': SCHEMA_VERSION, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'snapshot': before, 'source_overwrite': False}


def restore_local(backup_path, dsn, enabled, expected_snapshot=None, *, pg_restore=None):
    info = local_dsn(dsn, enabled)
    with psycopg.connect(dsn) as connection:
        _require_empty(connection)
    source = Path(backup_path).resolve()
    if not source.is_file() or source.is_symlink() or not source.read_bytes().startswith(b'PGDMP'):
        raise RehearsalError('A valid custom-format PostgreSQL backup is required')
    args, env = _command(info, _tool('pg_restore', pg_restore))
    args[1:1] = ['--exit-on-error', '--single-transaction', '--no-owner', '--no-acl']
    args.append(str(source))
    _run(args, env)
    restored = snapshot(dsn, True)
    if expected_snapshot is not None and restored != expected_snapshot:
        raise RehearsalError('Restored database differs from the backup snapshot')
    return {'format': 'future-school-runtime-local-restore-v1', 'rehearsal_only': True,
            'live_apply_authorized': False, 'schema': SCHEMA_VERSION,
            'backup_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'snapshot': restored, 'source_database_overwritten': False}


def _write_receipt(path, receipt):
    target = Path(path).resolve()
    with target.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Loopback-only runtime release rehearsal')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-empty')
    owner = commands.add_parser('provision-owner')
    owner.add_argument('--organization', required=True)
    owner.add_argument('--login', required=True)
    migrate = commands.add_parser('migrate')
    migrate.add_argument('--bundle', required=True)
    migrate.add_argument('--now', type=int, required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--output', required=True)
    backup.add_argument('--receipt', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--input', required=True)
    restore.add_argument('--backup-receipt', required=True)
    parser.add_argument('--local-rehearsal', action='store_true')
    args = parser.parse_args(argv)
    dsn = os.environ.get('RUNTIME_REHEARSAL_PG_DSN', '')
    if args.command == 'init-empty':
        result = initialize_local(dsn, args.local_rehearsal)
    elif args.command == 'migrate':
        result = migrate_local(args.bundle, dsn, args.local_rehearsal, args.now)
    elif args.command == 'provision-owner':
        local_dsn(dsn, args.local_rehearsal)
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            provision_first_owner(connection, args.organization, args.login)
        result = {'owner_provisioned': True}
    elif args.command == 'backup':
        result = backup_local(dsn, args.output, args.local_rehearsal)
        _write_receipt(args.receipt, result)
    else:
        with Path(args.backup_receipt).open(encoding='utf-8') as stream:
            receipt = json.load(stream)
        if receipt.get('format') != RECEIPT_FORMAT or receipt.get('rehearsal_only') is not True:
            raise RehearsalError('Unexpected backup receipt')
        result = restore_local(args.input, dsn, args.local_rehearsal, receipt.get('snapshot'))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
