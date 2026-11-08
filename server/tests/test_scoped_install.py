import base64
import hashlib
import importlib.util
import json
from pathlib import Path,PurePosixPath
import sqlite3
import sys
import unittest
from unittest.mock import patch
import test_scoped_pilot as fixture

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scoped_install',ROOT/'ops/install-scoped-pilot.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class ScopedInstallTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ScopedPilotTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.stage=self.f.f.output;self.home=self.f.f.root/'home';self.units=self.home/'.config/systemd/user';self.units.mkdir(parents=True)
        self.standby={'format':'future-school-pilot-standby-installed','standby_ready':True,'runtime_sha256':hashlib.sha256((ROOT/'ops/pilot-standby.py').read_bytes()).hexdigest()}
        (self.stage/'standby').mkdir();(self.stage/'standby/install-receipt.json').write_text(json.dumps(self.standby))
        self.config={'stage_directory':str(self.stage),'stage_receipt':self.f.receipt,'standby_receipt':self.standby,'plan':self.f.f.plan,'actor_login':'fictional-teacher'}
        self.files={}
        for name in installer.FILES:
            raw=(ROOT/'ops'/name).read_bytes();self.files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode()}

    def scenario(self,fail=False,actor_fail=False):
        calls=[];original=installer.program
        def program(raw,name):
            ns=original(raw,name)
            if name=='pilot-standby.py':ns['verify_release']=lambda path:None
            return ns
        def run(args,**kwargs):
            calls.append(args)
            if 'is-active' in args:return 'active'
            if 'LoadState' in args:return 'not-found'
            if 'enable' in args and fail:raise ValueError('fictional startup failure')
            return 'ActiveState=active\nSubState=running\nUnitFileState=enabled\nNRestarts=0'
        def health(port):
            if port==18084:return {'mode':'pilot_standby','roster_review_only':True,'standby_code_sha256':self.standby['runtime_sha256']}
            scope=json.loads((self.stage/'teacher-pilot/scoped.private.json').read_bytes())['scope']
            return {'mode':'pilot_scoped_teacher','pilot_phase':'teacher_preparation','roster_review_only':False,'scoped_code_sha256':self.files['scoped-pilot.py']['sha256'],'lesson_id':scope['lesson_id'],'selected_students':1,'teacher_class_writes_enabled':True,**{k:False for k in ['business_ready','student_sync_enabled','wechat_sending_enabled','device_registration_enabled','parent_binding_enabled']}}
        if actor_fail:self.config['actor_login']='other-teacher'
        before=fixture.fixture.stage.checksum(self.stage/'pilot-offline.sqlite')
        with patch.object(installer,'root',return_value=self.stage),patch.object(installer,'program',side_effect=program),patch.object(installer,'unit',return_value='fictional unit'),patch.object(installer.Path,'home',return_value=self.home),patch.object(installer,'run',side_effect=run),patch.object(installer,'health',side_effect=health),patch.object(installer.socket,'socket'),patch.object(installer.time,'time',return_value=fixture.fixture.NOW),patch.object(sys,'path',list(sys.path)):
            if fail or actor_fail:
                with self.assertRaises(ValueError):installer.install(self.config,self.files)
            else:
                result=installer.install(self.config,self.files)
                self.assertTrue(result['lesson_created']);self.assertFalse(result['device_enabled']);self.assertEqual(result['expected_students'],1)
                with self.assertRaises(ValueError):installer.install(self.config,self.files)
        self.assertEqual(fixture.fixture.stage.checksum(self.stage/'pilot-offline.sqlite'),before)
        if fail:self.assertIn(['systemctl','--user','disable','--now',installer.UNIT],calls)
        if actor_fail:self.assertFalse((self.units/installer.UNIT).exists())
        self.assertTrue(all(a[-1]==installer.UNIT for a in calls if 'disable' in a or 'stop' in a))
        self.assertEqual((self.stage/'teacher-pilot/install-receipt.json').exists(),not fail and not actor_fail)

    def test_new_copy_confirmed_lesson_new_unit_and_repeat_refusal(self):self.scenario()
    def test_failed_start_stops_only_new_unit_and_preserves_standby(self):self.scenario(fail=True)
    def test_operator_without_admin_role_cannot_prepare_lesson(self):self.scenario(actor_fail=True)

    def test_fixed_unit_and_pause_are_limited_to_scoped_phase(self):
        stage='/opt/wxnotify/app/single-class-offline-20261028121655-da40e468'
        text=installer.unit(PurePosixPath(stage+'/teacher-pilot'));self.assertIn('NoNewPrivileges=yes',text);self.assertNotIn('sudo',text)
        with self.assertRaises(ValueError):installer.root('/opt/other')
        calls=[]
        def run(args,**kwargs):calls.append(args);return stage+'/teacher-pilot' if 'WorkingDirectory' in args else 'ActiveState=inactive\nUnitFileState=disabled'
        with patch.object(installer,'root',return_value=PurePosixPath(stage)),patch.object(installer,'run',side_effect=run):self.assertEqual(installer.pause({'stage_directory':stage})['unit'],installer.UNIT)
        self.assertIn(['systemctl','--user','disable','--now',installer.UNIT],calls)
