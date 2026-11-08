"""The one explicitly confirmed source adds only an audit review, no raw edits."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('confirmed_makeup',Path(__file__).parents[1]/'ops/confirm-known-temporary-makeup.py');review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)

class ConfirmMakeupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name);self.path=self.work/'business.sqlite'
        self.lesson=dict(key='11:2026-11-01',date='2026-11-01',class_name='Python周六下午1',teacher='尹海勇',expected=0,arrived=1,leave_count=0,needs_review=True,members=[dict(student_local_id='1',status='arrived',expected=False)])
        with closing(sqlite3.connect(self.path)) as db,db:
            db.executescript("CREATE TABLE organizations(id TEXT,slug TEXT);INSERT INTO organizations VALUES('org','weilai');CREATE TABLE users(id TEXT,org_id TEXT,login TEXT,active INTEGER);INSERT INTO users VALUES('owner','org','liuguixun',1);CREATE TABLE roles(org_id TEXT,user_id TEXT,role TEXT);INSERT INTO roles VALUES('org','owner','admin');CREATE TABLE web_dataset(id INTEGER,data_json TEXT);CREATE TABLE management_initializations(org_id TEXT,archive_json TEXT);CREATE TABLE management_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,org_id TEXT,actor_id TEXT,workspace_role TEXT,action TEXT,entity_id TEXT,detail TEXT,created_at INTEGER);")
            db.execute('INSERT INTO web_dataset VALUES(1,?)',(json.dumps(dict(lessons=[self.lesson])),));db.execute('INSERT INTO management_initializations VALUES(?,?)',('org',json.dumps(dict(sections=dict(attendance=[dict(studentId=1,date='2026-11-01')],transactions=[dict(amount=-1)])))))
        self.payload=dict(format='future-school-confirm-known-temporary-makeup-v1',expected_lesson_sha256=review.sha(review.encoded(self.lesson).encode()))
        for p in [patch.object(review,'WORK',self.work),patch.object(review,'private')]:p.start();self.addCleanup(p.stop)
    def test_confirm_then_retry_preserves_raw_data_and_only_one_audit(self):
        with closing(sqlite3.connect(self.path)) as db:before=[db.execute('SELECT * FROM '+t).fetchall() for t in ['web_dataset','management_initializations','users','roles']]
        result=review.confirm(self.payload);self.assertEqual((result['expected_added'],result['attendance_added'],result['charges_added']),(1,0,0));self.assertTrue(review.confirm(self.payload)['duplicate'])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM management_audit').fetchone()[0],1);self.assertEqual([db.execute('SELECT * FROM '+t).fetchall() for t in ['web_dataset','management_initializations','users','roles']],before)
    def test_changed_or_non_unique_source_refuses_without_audit(self):
        with self.assertRaises(ValueError):review.confirm(dict(self.payload,expected_lesson_sha256='0'*64))
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(dict(lessons=[self.lesson,self.lesson])),))
        with self.assertRaises(ValueError):review.confirm(self.payload)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM management_audit').fetchone()[0],0)

if __name__=='__main__':unittest.main()
