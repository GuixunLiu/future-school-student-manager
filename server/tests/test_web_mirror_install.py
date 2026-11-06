import base64,hashlib,importlib.util,json,os,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from test_teacher_web import TeacherWebTests

SERVER=Path(__file__).resolve().parents[1]
CERT=SERVER.parents[2]/'outputs/private-web-mirror-2026-10-29'

@unittest.skipUnless((CERT/'server.key').is_file(),'Local private TLS identity is not part of Git')
class MirrorInstallTests(unittest.TestCase):
    def prepare(self):
        f=TeacherWebTests();f.setUp();self.addCleanup(f.doCleanups)
        root=f.f.f.root/'install';root.mkdir();work=root/'teacher-web-20261029101834-42da0325';(work/'teacher_web/web').mkdir(parents=True)
        home=root/'home';drop=home/'.config/systemd/user/wxnotify-http-viewer.service.d/teacher-web.conf';drop.parent.mkdir(parents=True);drop.write_bytes(b'ExecStart=/python '+str(work).encode()+b'/teacher_web/gateway.py --expires-at 1794391683')
        import shutil
        shutil.copyfile(f.db,work/'business.sqlite')
        with sqlite3.connect(work/'business.sqlite') as db:
            db.execute("UPDATE organizations SET slug='weilai'")
            count=db.execute('SELECT count(*) FROM users').fetchone()[0]
            for i in range(count,12):db.execute("INSERT INTO users SELECT ?,org_id,?,?,password_hash,active,created_at FROM users LIMIT 1",(format(i+100,'032x'),'fictional-admin-'+str(i),'虚构管理员'+str(i)))
        cfg={'source_sha256':'f'*64,'files':{}}
        for n in ['teacher_web/api.py','teacher_web/web/workspace.js']:
            (work/n).write_bytes(b'# old source\n');cfg['files'][n]=hashlib.sha256((work/n).read_bytes()).hexdigest()
        (work/'web.private.json').write_text(json.dumps(cfg))
        spec=importlib.util.spec_from_file_location('mirror_install',SERVER/'ops/install-web-mirror.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.ROOT=root
        payload={'files':{},'tls':{n:base64.b64encode((CERT/n).read_bytes()).decode() for n in ['server.crt','server.key']}}
        for n in m.NAMES:
            b=(SERVER/n).read_bytes();payload['files'][n]={'sha256':m.sha(b),'base64':base64.b64encode(b).decode()}
        plan={'work_directory':str(work),'source_sha256':'f'*64,'previous_files':cfg['files'],'device':{'id':'d'*32,'token_digest':'a'*64}}
        return m,work,home,drop,plan,payload

    def test_install_backs_up_and_preserves_accounts_requests_and_dataset(self):
        m,w,h,d,plan,payload=self.prepare();before=(w/'web.private.json').read_bytes()
        with sqlite3.connect(w/'business.sqlite') as db:accounts=m.accounts(db);dataset=db.execute('SELECT * FROM web_dataset').fetchall()
        with patch.object(Path,'home',return_value=h),patch.object(os,'geteuid',return_value=1000,create=True),patch.object(m,'run',side_effect=lambda a:str(w) if 'WorkingDirectory' in ' '.join(a) else ''),patch.object(m,'drain'),patch.object(m,'wait'):
            r=m.install(plan,payload)
        self.assertTrue(r['tls_verified']);self.assertTrue((Path(r['backup_directory'])/'before.sqlite').is_file());self.assertEqual((Path(r['backup_directory'])/'web.private.json').read_bytes(),before)
        with sqlite3.connect(w/'business.sqlite') as db:self.assertEqual(m.accounts(db),accounts);self.assertEqual(db.execute('SELECT * FROM web_dataset').fetchall(),dataset);self.assertEqual(db.execute('SELECT count(*) FROM web_mirror_devices WHERE active=1').fetchone()[0],1)
        self.assertIn(b'/teacher_web/mirror_gateway.py',d.read_bytes())

    def test_readiness_failure_restores_original_web_and_disables_enrollment(self):
        m,w,h,d,plan,payload=self.prepare();before=(w/'web.private.json').read_bytes();drop=d.read_bytes();calls=[]
        def waiting(*a,**kw):
            calls.append(a)
            if len(calls)==2:raise ValueError('Fictional readiness failure')
        with patch.object(Path,'home',return_value=h),patch.object(os,'geteuid',return_value=1000,create=True),patch.object(m,'run',side_effect=lambda a:str(w) if 'WorkingDirectory' in ' '.join(a) else ''),patch.object(m,'drain'),patch.object(m,'wait',side_effect=waiting):
            with self.assertRaisesRegex(ValueError,'original web recovered'):m.install(plan,payload)
        self.assertEqual((w/'web.private.json').read_bytes(),before);self.assertEqual(d.read_bytes(),drop)
        with sqlite3.connect(w/'business.sqlite') as db:self.assertEqual(db.execute('SELECT count(*) FROM web_mirror_devices WHERE active=1').fetchone()[0],0)

if __name__=='__main__':unittest.main()
