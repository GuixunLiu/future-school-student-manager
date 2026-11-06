import base64
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('report_patch',Path(__file__).resolve().parents[1]/'ops/patch-teacher-web-report.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
sha=lambda b:hashlib.sha256(b).hexdigest()


class ReportPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'teacher-web-fictional';self.work.mkdir()
        self.old={name:b'# fictional previous source\n' for name in installer.FILES|{'teacher_web/gateway.py'}}
        for name,raw in self.old.items():
            p=self.work/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        self.pins={n:sha(v) for n,v in self.old.items()}
        self.cfg=json.dumps({'files':self.pins,'source_sha256':'a'*64}).encode();(self.work/'web.private.json').write_bytes(self.cfg)
        with closing(sqlite3.connect(self.work/'business.sqlite')) as db,db:db.execute('CREATE TABLE web_makeup_requests(id TEXT)');db.execute("INSERT INTO web_makeup_requests VALUES('fictional pending request')")
        self.db=(self.work/'business.sqlite').read_bytes()
        self.config={'work_directory':str(self.work),'previous_files':self.pins,'source_sha256':'a'*64}
        self.payload={'files':{n:{'base64':base64.b64encode(b'# fictional patched source\n').decode(),'sha256':sha(b'# fictional patched source\n')} for n in installer.FILES}}

    def scenario(self,fail=False):
        state={'new':False,'failed':False};calls=[]
        def run(args):
            calls.append(args)
            if 'WorkingDirectory' in args:return str(self.work)
            if 'is-active' in args:return 'active'
            if 'start' in args:
                if fail and not state['failed']:state['failed']=True;raise ValueError('fictional failed start')
                state['new']=json.loads((self.work/'web.private.json').read_bytes())['files']!=self.pins
            return 'ActiveState=active\nSubState=running\nUnitFileState=enabled'
        def health():return {'mode':'teacher_web','source_sha256':'a'*64,'makeup_mode':'request_pending_tablet','student_sync_enabled':False,'wechat_sending_enabled':False,'report_display_version':2 if state['new'] else 1,'workspace_ui_version':3 if state['new'] else None,'workspace_role_selection':state['new']}
        with patch.object(installer,'ROOT',self.root),patch.object(installer.os,'geteuid',return_value=1000,create=True),patch.object(installer,'run',side_effect=run),patch.object(installer,'health',side_effect=health):
            if fail:
                with self.assertRaisesRegex(ValueError,'restored and verified'):installer.install(self.config,self.payload)
                for name,raw in self.old.items():self.assertEqual((self.work/name).read_bytes(),raw)
                self.assertEqual((self.work/'web.private.json').read_bytes(),self.cfg)
            else:
                result=installer.install(self.config,self.payload);self.assertFalse(result['database_written']);self.assertEqual(result['requests_before'],1)
                self.assertEqual((self.work/'teacher_web/gateway.py').read_bytes(),self.old['teacher_web/gateway.py'])
                with self.assertRaises(ValueError):installer.install(self.config,self.payload)
        self.assertEqual((self.work/'business.sqlite').read_bytes(),self.db)
        self.assertTrue(all('wxnotify-http-viewer.service' not in c for c in calls))

    def test_patch_keeps_database_requests_gateway_and_refuses_repeat(self):self.scenario()
    def test_failed_new_start_restores_code_and_pins_without_database_restore(self):self.scenario(True)

    def role_case(self,fail=False):
        name='teacher_web/web/workspace.css';raw=b'/* fictional previous CSS */'
        self.old[name]=raw;(self.work/name).write_bytes(raw);self.pins[name]=sha(raw)
        self.cfg=json.dumps({'files':self.pins,'source_sha256':'a'*64}).encode();(self.work/'web.private.json').write_bytes(self.cfg)
        self.config['kind']='role_interface'
        self.payload['files'][name]={'base64':base64.b64encode(b'/* fictional new CSS */').decode(),'sha256':sha(b'/* fictional new CSS */')}
        self.scenario(fail)

    def test_role_ui_patch_keeps_database_requests_and_gateway(self):self.role_case()
    def test_role_ui_failure_restores_all_three_files(self):self.role_case(True)


if __name__=='__main__':unittest.main()
