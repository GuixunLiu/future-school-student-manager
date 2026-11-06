"""Synthetic SQLite only: minimal scoped exports, no changes or transmissions."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from business.store import Store, uid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("roster_review_export", ROOT / "ops/export-roster-review.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReviewExportTests(unittest.TestCase):
    def fixture(self, folder):
        database = Path(folder) / "pilot.sqlite"
        store = Store.bootstrap(database, "test-school", "test-owner", "Fictional owner", "Only fictional review test password!")
        with store.transaction() as db:
            org = db.execute("SELECT id FROM organizations").fetchone()[0]
            other, first, second, teacher = uid(), uid(), uid(), uid()
            db.execute("INSERT INTO organizations VALUES(?,?,?)", (other, "other-school", 0))
            db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (first, org, "Fictional class", "Fictional course", 0))
            db.execute("INSERT INTO classes VALUES(?,?,?,?,?)", (second, other, "PRIVATE_OTHER_CLASS", "OTHER", 0))
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?)", (teacher, org, "fictional-teacher", "Fictional teacher", "PRIVATE_HASH_MUST_NOT_EXPORT", 1, 0))
            db.execute("INSERT INTO roles VALUES(?,?,?)", (org, teacher, "teacher"))
            db.execute("INSERT INTO teacher_classes VALUES(?,?,?)", (org, teacher, first))
            for tenant, school_class, number, name in [(org, first, "000101", "Fictional student"), (other, second, "SECRET-OTHER", "PRIVATE_OTHER_STUDENT")]:
                db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active,source_device_id,source_revision) VALUES(?,?,?,?,?,?,?,?,?)", (uid(), tenant, school_class, name, number, -250, 1, None, 0))
        return database

    def test_export_is_minimal_scoped_consistent_and_read_only(self):
        with tempfile.TemporaryDirectory(prefix="wxnotify-roster-export-test-") as folder:
            database = self.fixture(folder)
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            with mock.patch.object(module, "private_path"):
                result = module.snapshot(database, "test-school")
                self.assertTrue(result["complete"])
                self.assertEqual(len(result["classes"]), 1)
                self.assertEqual(len(result["students"]), 1)
                self.assertEqual(result["students"][0]["external_ref"], "000101")
                self.assertEqual(result["students"][0]["remaining_units"], -250)
                self.assertIs(result["students"][0]["active"], True)
                self.assertEqual(result["classes"][0]["teachers"][0]["name"], "Fictional teacher")
                encoded = json.dumps(result)
                self.assertNotIn("PRIVATE_", encoded)
                self.assertNotIn("password", encoded)
                output = Path(folder) / "review.json"
                metadata = module.export(database, "test-school", output)
                self.assertEqual(metadata["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
                self.assertFalse(metadata["mutation_performed"])
                saved = output.read_bytes()
                with self.assertRaises(FileExistsError):
                    module.export(database, "test-school", output)
                self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_unknown_org_limits_and_invalid_scope_never_write_partial_output(self):
        with tempfile.TemporaryDirectory(prefix="wxnotify-roster-export-test-") as folder:
            database, output = self.fixture(folder), Path(folder) / "review.json"
            with mock.patch.object(module, "private_path"):
                with self.assertRaisesRegex(ValueError, "does not exist"):
                    module.export(database, "missing-school", output)
                with self.assertRaisesRegex(ValueError, "exact organization"):
                    module.export(database, "test-school' OR 1=1", output)
                with mock.patch.object(module, "LIMIT", 0):
                    with self.assertRaisesRegex(ValueError, "limit exceeded"):
                        module.export(database, "test-school", output)
            self.assertFalse(output.exists())

    def test_unknown_database_identity_is_rejected_read_only(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory(prefix="wxnotify-roster-export-test-") as folder:
            database = self.fixture(folder)
            with closing(sqlite3.connect(database)) as db:
                db.execute("PRAGMA application_id=0")
            before = database.read_bytes()
            with mock.patch.object(module, "private_path"):
                with self.assertRaisesRegex(ValueError, "identity"):
                    module.snapshot(database, "test-school")
            self.assertEqual(database.read_bytes(), before)

    def test_broken_relationships_do_not_produce_complete_snapshot(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory(prefix="wxnotify-roster-export-test-") as folder:
            database, output = self.fixture(folder), Path(folder) / "review.json"
            with closing(sqlite3.connect(database)) as db:
                db.execute("UPDATE students SET class_id=?", (uid(),))
                db.commit()
            before = database.read_bytes()
            with mock.patch.object(module, "private_path"):
                with self.assertRaisesRegex(ValueError, "relationships"):
                    module.export(database, "test-school", output)
            self.assertFalse(output.exists())
            self.assertEqual(database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
