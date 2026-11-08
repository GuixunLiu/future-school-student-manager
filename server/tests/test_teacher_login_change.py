"""Fictional account rename and explicitly requested short-password compatibility."""

from contextlib import closing
import copy
import hashlib
import importlib.util
from pathlib import Path
import secrets
import sqlite3
import unittest

import test_teacher_access as fixtures
from business.store import Problem
from business_next.migration import digest

spec=importlib.util.spec_from_file_location("login_change",Path(__file__).resolve().parents[1]/"ops/change-teacher-logins.py")
change=importlib.util.module_from_spec(spec);spec.loader.exec_module(change)


def requested_hash():
    salt=secrets.token_hex(16)
    return "pbkdf2_sha256$600000$"+salt+"$"+hashlib.pbkdf2_hmac("sha256",b"123456",bytes.fromhex(salt),600000).hex()


class LoginChangeTests(unittest.TestCase):
    def setUp(self):
        fixtures.TeacherAccessTests.setUp(self)
        access_digest=digest(self.plan)
        fixtures.access.apply_access(self.path,self.plan,access_digest,self.backup)
        self.plan={"format":"future-school-confirmed-login-change","schema_version":1,"confirmed_by_owner":True,
                   "organization":"school-fictional","previous_access_plan_sha256":access_digest,
                   "owner_old_login":"owner","owner_new_login":"fictionalowner","expected":{"accounts":2,"classes":2},
                   "people":[{"name":self.owner_label,"old_login":"owner","new_login":"fictionalowner","roles":["admin","teacher"],"class_source_ids":[1],"password_hash":requested_hash()},
                             {"name":self.teacher_label,"old_login":"teacher-two","new_login":"fictionalteacher","roles":["teacher"],"class_source_ids":[2],"password_hash":requested_hash()}]}
        self.backup=self.root/"before-logins.sqlite"

    def apply(self):return change.change(self.path,self.plan,digest(self.plan),self.backup)

    def test_new_logins_short_password_roles_and_scope_preserved_old_sessions_revoked(self):
        token=self.store.login("school-fictional","owner",fixtures.PASSWORD,"fixture")["access_token"]
        with closing(self.store.connect()) as db:
            before=[tuple(r) for r in db.execute("SELECT * FROM teacher_classes ORDER BY user_id,class_id")]
            ids=[tuple(r) for r in db.execute("SELECT id,display_name FROM users ORDER BY id")]
        result=self.apply();self.assertEqual(result["accounts_changed"],2);self.assertEqual(result["new_accounts"],0)
        with self.assertRaises(Problem):self.store.command(token,self.store.me)
        for login,roles in (("fictionalowner",["admin","teacher"]),("fictionalteacher",["teacher"])):
            t=self.store.login("school-fictional",login,"123456","fixture-new")["access_token"]
            self.assertEqual(self.store.command(t,self.store.me)["roles"],roles)
        with self.assertRaises(Problem):self.store.login("school-fictional","owner",fixtures.PASSWORD,"fixture-old")
        with closing(self.store.connect()) as db:
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM teacher_classes ORDER BY user_id,class_id")],before)
            self.assertEqual([tuple(r) for r in db.execute("SELECT id,display_name FROM users ORDER BY id")],ids)
        again=self.apply();self.assertTrue(again["replayed"])
        with closing(fixtures.Store(self.backup).connect()) as db:self.assertEqual(db.execute("SELECT login FROM users WHERE id=?",(self.owner["id"],)).fetchone()[0],"owner")

    def test_collision_refuses_before_backup(self):
        with self.store.transaction() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,1,0)",(fixtures.uid(),self.org,"fictionalteacher","另一个虚构人",self.hash))
        with self.assertRaisesRegex(ValueError,"already used"):self.apply()
        self.assertFalse(self.backup.exists())

    def test_update_failure_rolls_back_owner_and_teacher_credentials(self):
        with self.store.transaction() as db:
            db.execute("CREATE TRIGGER fail_rename BEFORE UPDATE ON users WHEN NEW.login='fictionalteacher' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.apply()
        token=self.store.login("school-fictional","owner",fixtures.PASSWORD,"fixture-rollback")["access_token"]
        self.assertEqual(self.store.command(token,self.store.me)["roles"],["admin","teacher"])
        with closing(self.store.connect()) as db:self.assertEqual(db.execute("SELECT count(*) FROM users WHERE login IN ('owner','teacher-two')").fetchone()[0],2)
        self.assertTrue(self.backup.exists())

    def test_unconfirmed_duplicate_and_changed_permission_refused(self):
        for mutation in (lambda p:p.update(confirmed_by_owner=False),lambda p:p["people"][1].update(new_login="fictionalowner"),lambda p:p["people"][1].update(roles=["parent"])):
            p=copy.deepcopy(self.plan);mutation(p)
            with self.assertRaises(ValueError):change.validate(p)
        with self.store.transaction() as db:db.execute("DELETE FROM teacher_classes WHERE class_id=?",(self.ids[1],))
        with self.assertRaisesRegex(ValueError,"assignments changed"):self.apply()
        self.assertFalse(self.backup.exists())


if __name__=="__main__":unittest.main()
