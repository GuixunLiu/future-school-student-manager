"""Optional real PostgreSQL rehearsal using pgserver and fictional rows only."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from management import fresh_start
from management import fresh_start_postgres as pg
from test_fresh_start_postgres import fictional_bundle


@unittest.skipUnless(importlib.util.find_spec("pgserver"), "optional pgserver is not installed")
class RealPostgresRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pgserver
        cls.tmp = tempfile.TemporaryDirectory(prefix="fresh-start-pg-")
        cls.root = Path(cls.tmp.name)
        cls.server = pgserver.get_server(str(cls.root / "data"), cleanup_mode="stop")
        parsed = urlsplit(cls.server.get_uri())
        cls.base = make_conninfo(host="127.0.0.1", port=parsed.port,
                                 user=parsed.username, password=parsed.password)
        cls.sequence = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.tmp.cleanup()

    def create_database(self):
        type(self).sequence += 1
        name = "fresh_start_rehearsal_" + str(self.sequence)
        with psycopg.connect(self.base, dbname="postgres", autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        self.addCleanup(self.drop_database, name)
        return name

    @classmethod
    def drop_database(cls, name):
        with psycopg.connect(cls.base, dbname="postgres", autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def bundle_file(self, bundle):
        path = self.root / ("bundle-" + str(self.sequence) + ".json")
        path.write_bytes(fresh_start.bundle_bytes(bundle))
        return path

    def test_commit_counts_receipts_and_duplicate_refusal(self):
        database = self.create_database()
        bundle = fictional_bundle()
        receipt = pg.apply_local(self.bundle_file(bundle), make_conninfo(self.base, dbname=database), True, 123456)
        self.assertEqual(receipt["counts"], bundle["counts"])
        with psycopg.connect(self.base, dbname=database) as connection:
            self.assertEqual(connection.execute("SELECT sum(units) FROM opening_balances").fetchone()[0], 1750)
            self.assertEqual(connection.execute("SELECT count(*) FROM migration_receipts").fetchone()[0], 1)
            self.assertFalse(connection.execute("SELECT history_migrated OR credentials_migrated FROM migration_receipts").fetchone()[0])
            columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'")}
            self.assertNotIn("password_hash", columns)
        with self.assertRaisesRegex(pg.FreshStartError, "nonempty"):
            pg.apply_local(self.bundle_file(bundle), make_conninfo(self.base, dbname=database), True, 123456)

    def test_foreign_key_failure_rolls_back_schema_and_all_rows(self):
        database = self.create_database()
        bundle = fictional_bundle()
        bundle["teacher_classes"][0]["class_id"] = "missing-class"
        with self.assertRaises(Exception):
            pg.apply_local(self.bundle_file(bundle), make_conninfo(self.base, dbname=database), True, 123456)
        with psycopg.connect(self.base, dbname=database) as connection:
            count = connection.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchone()[0]
            self.assertEqual(count, 0)

    def test_external_failure_after_population_rolls_back_everything(self):
        database = self.create_database()
        bundle = fictional_bundle()
        raw = fresh_start.bundle_bytes(bundle)
        class DeliberateFailure(Exception):
            pass
        with self.assertRaises(DeliberateFailure):
            with psycopg.connect(self.base, dbname=database) as connection:
                pg.populate(connection, json_canonical(raw), raw, 123456)
                raise DeliberateFailure()
        with psycopg.connect(self.base, dbname=database) as connection:
            count = connection.execute("SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname='public'").fetchone()[0]
            self.assertEqual(count, 0)


def json_canonical(raw):
    import json
    return json.loads(raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
