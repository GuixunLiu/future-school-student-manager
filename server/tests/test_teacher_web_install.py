import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_teacher_web as fixture

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('web_install',ROOT/'ops/install-teacher-web.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
sha=lambda b:hashlib.sha256(b).hexdigest()


class TeacherWebInstallTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.TeacherWebTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.f.f.root/'installer';self.root.mkdir();self.old=self.root/'old';self.old.mkdir();self.home=self.root/'home';self.units=self.home/'.config/systemd/user';self.units.mkdir(parents=True)
        self.original=(ROOT/'ops/temporary-http-viewer.py').read_bytes();self.assertEqual(sha(self.original),installer.OLD_SHA);(self.old/'temporary-http-viewer.py').write_bytes(self.original)
        self.release=self.root/'frozen';self.release.mkdir();files={}
        for n in range(27):
            name='file'+str(n);(self.release/name).write_bytes(b'fictional frozen');files[name]=sha(b'fictional frozen')
        raw=json.dumps({'files':files}).encode();(self.release/'MANIFEST.json').write_bytes(raw);self.manifest_sha=sha(raw)
        self.payload={'files':{}}
        for name in installer.FILES:
            raw=self.original if name=='base-viewer.py' else (ROOT/name).read_bytes();self.payload['files'][name]={'sha256':sha(raw),'base64':base64.b64encode(raw).decode()}
        raw=json.dumps(self.f.data).encode();self.payload['dataset']={'sha256':sha(raw),'base64':base64.b64encode(raw).decode()}
        self.config={'public_origin':'http://47.97.226.219:18083','expires_at':1794391683,'makeup_mode':'request_pending_tablet'}

    def scenario(self,fail=None):
        calls=[];state={'switched':False,'failed':False}
        def run(args,allowed=(0,)):
            calls.append(args)
            if 'WorkingDirectory' in args:return str(self.old)
            if 'is-active' in args:return 'active'
            if 'LoadState' in args:return 'not-found'
            if 'enable' in args and fail=='backend':raise ValueError('fictional backend failed')
            if args[-1]==installer.VIEWER and 'start' in args:
                if fail=='viewer' and not state['failed']:state['failed']=True;raise ValueError('fictional viewer failed')
                state['switched']=(self.units/(installer.VIEWER+'.d')/'teacher-web.conf').exists()
            return 'ActiveState=active\nSubState=running\nUnitFileState=enabled\nNRestarts=0'
        def health(port,host=None):
            if port==18086:return {'mode':'teacher_web','source_sha256':self.f.data['source_sha256'],'makeup_mode':'request_pending_tablet'}
            return {'gateway_sha256':self.payload['files']['teacher_web/gateway.py']['sha256'] if state['switched'] else installer.OLD_SHA,'expires_at':1794391683,'viewer':'teacher_web' if state['switched'] else 'temporary_http_read_only'}
        before=self.f.db.read_bytes()
        with patch.object(installer,'APP_ROOT',self.root),patch.object(installer,'RELEASE',str(self.release)),patch.object(installer,'MANIFEST_SHA',self.manifest_sha),patch.object(installer,'SOURCE',str(self.f.db)),patch.object(installer,'OLD_WORK',str(self.old)),patch.object(installer.os,'geteuid',return_value=1000,create=True),patch.object(installer.Path,'home',return_value=self.home),patch.object(installer,'run',side_effect=run),patch.object(installer,'health',side_effect=health),patch.object(installer.socket,'socket'):
            if fail:
                with self.assertRaises(ValueError):installer.install(self.config,self.payload)
            else:
                result=installer.install(self.config,self.payload);self.assertFalse(result['hours_deducted']);self.assertTrue(result['accounts_preserved']);self.assertEqual(result['summary']['classes'],1)
                with self.assertRaises(ValueError):installer.install(self.config,self.payload)
        self.assertEqual(before,self.f.db.read_bytes());self.assertEqual((self.old/'temporary-http-viewer.py').read_bytes(),self.original)
        if fail:
            self.assertFalse((self.units/(installer.VIEWER+'.d')).exists())
            self.assertIn(['systemctl','--user','disable','--now',installer.UNIT],calls)
        if fail=='backend':self.assertNotIn(['systemctl','--user','stop',installer.VIEWER],calls)
        if fail=='viewer':self.assertFalse(state['switched']);self.assertGreaterEqual(calls.count(['systemctl','--user','start',installer.VIEWER]),2)

    def test_new_backend_public_switch_and_repeat_refusal(self):self.scenario()
    def test_backend_failure_does_not_stop_original_viewer(self):self.scenario('backend')
    def test_public_switch_failure_restores_and_verifies_original_viewer(self):self.scenario('viewer')


if __name__=='__main__':unittest.main()
