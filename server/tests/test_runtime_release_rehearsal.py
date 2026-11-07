"""Real local-only release rehearsal with fictional PostgreSQL data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from business.security import token_digest
from management import fresh_start, runtime_release
from management.runtime_service import RuntimeStore
from tests.test_fresh_start_postgres import fictional_bundle


DEVICE_TOKEN = 'wxd_' + 'r' * 43


@unittest.skipUnless(importlib.util.find_spec('pgserver'), 'optional pgserver is not installed')
class RuntimeReleaseRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pgserver
        cls.tmp = tempfile.TemporaryDirectory(prefix='runtime-release-pg-')
        cls.root = Path(cls.tmp.name)
        cls.server = pgserver.get_server(str(cls.root / 'data'), cleanup_mode='stop')
        parsed = urlsplit(cls.server.get_uri())
        cls.base = make_conninfo(host='127.0.0.1', port=parsed.port,
                                 user=parsed.username, password=parsed.password)
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.tmp.cleanup()

    def create_database(self):
        type(self).sequence += 1
        name = runtime_release.DATABASE_PREFIX + str(type(self).sequence)
        with psycopg.connect(self.base, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.addCleanup(self.drop_database, name)
        return make_conninfo(self.base, dbname=name)

    @classmethod
    def drop_database(cls, name):
        with psycopg.connect(cls.base, dbname='postgres', autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))

    def bundle_file(self):
        path = self.root / ('bundle-' + str(self.sequence) + '.json')
        path.write_bytes(fresh_start.bundle_bytes(fictional_bundle()))
        return path

    @staticmethod
    def prepare_tablet(dsn):
        with psycopg.connect(dsn) as connection:
            connection.execute("""
                UPDATE devices SET token_reissue_required=FALSE WHERE id='device';
                INSERT INTO device_classes VALUES('org','device','class');
                INSERT INTO lessons VALUES('lesson','org','class',100,200,'scheduled',1);
                INSERT INTO lesson_students VALUES('org','class','lesson','student-a',TRUE,1);
                INSERT INTO attendance_records VALUES('org','lesson','student-a','missing',0,NULL,1);
            """)
            connection.execute(
                "INSERT INTO device_credentials VALUES('org','device','generation',%s,1,100000,NULL)",
                (token_digest(DEVICE_TOKEN),))

    @staticmethod
    def checkin():
        return dict(event_ref='release-checkin', lesson_id='lesson', student_id='student-a',
                    occurred_at=100, attendance_revision=0, balance_revision=1,
                    previous_balance_units=900, consumed_units=1)

    def test_migration_backup_restore_and_tablet_recheck_are_isolated(self):
        source = self.create_database()
        restored = self.create_database()
        migrated = runtime_release.migrate_local(self.bundle_file(), source, True, 1000)
        self.assertEqual((2, 2, False, False), tuple(migrated[key] for key in (
            'student_balances_initialized', 'opening_ledger_initialized',
            'history_migrated', 'credentials_migrated')))
        self.prepare_tablet(source)
        before = runtime_release.snapshot(source)
        backup_path = self.root / 'fictional-before-release.pgdump'
        backup = runtime_release.backup_local(source, backup_path, True)
        self.assertEqual(backup['snapshot'], before)
        self.assertTrue(backup_path.read_bytes().startswith(b'PGDMP'))

        changed = RuntimeStore(source, lambda: 1000).tablet_checkin(DEVICE_TOKEN, self.checkin())
        self.assertEqual((899, 2), (changed['balance_units'], changed['balance_revision']))
        self.assertNotEqual(runtime_release.snapshot(source)['sha256'], before['sha256'])

        restored_result = runtime_release.restore_local(backup_path, restored, True, backup['snapshot'])
        self.assertEqual(restored_result['snapshot'], before)
        self.assertFalse(restored_result['source_database_overwritten'])
        with psycopg.connect(restored) as connection:
            self.assertEqual(900, connection.execute(
                "SELECT units FROM student_balances WHERE student_id='student-a'").fetchone()[0])
            self.assertEqual(0, connection.execute('SELECT count(*) FROM attendance_events').fetchone()[0])
        bootstrap = RuntimeStore(restored, lambda: 1000).tablet_bootstrap(DEVICE_TOKEN)
        self.assertEqual(900, bootstrap['lessons'][0]['students'][0]['balance_units'])
        with psycopg.connect(source) as connection:
            self.assertEqual(899, connection.execute(
                "SELECT units FROM student_balances WHERE student_id='student-a'").fetchone()[0])
            self.assertEqual(1, connection.execute('SELECT count(*) FROM attendance_events').fetchone()[0])

        from fastapi.testclient import TestClient
        from management.runtime_api import create_app
        health = TestClient(create_app(restored, lambda: 1000, allow_test_client=True)).get('/health/ready')
        self.assertEqual(200, health.status_code)
        self.assertEqual(('ready', SCHEMA_VERSION := 'fresh-start-runtime-v1', False),
                         (health.json()['status'], health.json()['schema'], health.json()['production_ready']))
        self.assertEqual(SCHEMA_VERSION, migrated['schema'])

    def test_source_change_during_backup_invalidates_new_receipt_and_partial_file(self):
        source = self.create_database()
        runtime_release.migrate_local(self.bundle_file(), source, True, 1000)
        self.prepare_tablet(source)
        original_run = runtime_release._run
        changed = False

        def write_during_backup(args, env):
            nonlocal changed
            if not changed:
                changed = True
                RuntimeStore(source, lambda: 1000).tablet_checkin(DEVICE_TOKEN, self.checkin())
            return original_run(args, env)

        target = self.root / 'changed-during-backup.pgdump'
        with patch.object(runtime_release, '_run', write_during_backup):
            with self.assertRaisesRegex(runtime_release.RehearsalError, 'Source changed'):
                runtime_release.backup_local(source, target, True)
        self.assertTrue(changed)
        self.assertFalse(target.exists())
        with psycopg.connect(source) as connection:
            self.assertEqual(899, connection.execute(
                "SELECT units FROM student_balances WHERE student_id='student-a'").fetchone()[0])

    def test_failed_runtime_migration_rolls_back_schema_rows_and_projection(self):
        database = self.create_database()
        with patch.object(runtime_release, 'POSTGRES_RUNTIME_SCHEMA',
                          runtime_release.POSTGRES_RUNTIME_SCHEMA + '\nINSERT INTO missing_table VALUES(1);'):
            with self.assertRaises(Exception):
                runtime_release.migrate_local(self.bundle_file(), database, True, 1000)
        with psycopg.connect(database) as connection:
            self.assertEqual(0, connection.execute(
                "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchone()[0])

    def test_local_only_nonempty_and_no_overwrite_guards(self):
        valid = 'host=127.0.0.1 dbname=runtime_rehearsal_contract user=fictional'
        self.assertEqual('runtime_rehearsal_contract', runtime_release.local_dsn(valid, True)['dbname'])
        for dsn in ('host=203.0.113.1 dbname=runtime_rehearsal_contract',
                    'host=127.0.0.1 dbname=production', 'dbname=runtime_rehearsal_contract'):
            with self.subTest(dsn=dsn), self.assertRaises(runtime_release.RehearsalError):
                runtime_release.local_dsn(dsn, True)
        with self.assertRaises(runtime_release.RehearsalError):
            runtime_release.local_dsn(valid, False)

        database = self.create_database()
        runtime_release.initialize_local(database, True)
        digest = runtime_release.snapshot(database)['sha256']
        with self.assertRaisesRegex(runtime_release.RehearsalError, 'nonempty'):
            runtime_release.initialize_local(database, True)
        self.assertEqual(digest, runtime_release.snapshot(database)['sha256'])
        existing = self.root / 'existing.pgdump'
        existing.write_bytes(b'do-not-overwrite')
        with self.assertRaisesRegex(runtime_release.RehearsalError, 'new file'):
            runtime_release.backup_local(database, existing, True)
        self.assertEqual(b'do-not-overwrite', existing.read_bytes())
        with self.assertRaisesRegex(runtime_release.RehearsalError, 'nonempty'):
            runtime_release.restore_local(existing, database, True)


if __name__ == '__main__':
    unittest.main()
