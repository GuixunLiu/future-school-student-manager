"""Bounded single-SSH cutover: no shell/SQL RPC, fixed services and source.

Private inputs/results travel through SSH stdin/stdout. A fresh full copy is
used; failure before an activation offer restores the original services.
After any committed offer, uncertainty MUST stay fenced for reconciliation.
"""
import base64
from contextlib import closing
import hashlib
from http.client import HTTPConnection, HTTPSConnection
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

APP=Path('/opt/wxnotify/app')
SOURCE=APP/'teacher-web-20261029101834-42da0325/business.sqlite'
PYTHON=APP/'business-v0.4.0-c59dab1f/.venv/bin/python'
OLD=('wxnotify-http-viewer.service','wxnotify-teacher-web.service')
NEW=('wxnotify-canonical-gateway.service','wxnotify-canonical-management.service')
EXPIRY=1794391683
PIN='0bd32cee02e66ae18e98f708bd88cc5a8f889afe0305921ec16be8b223d220a0'
MODULES=('__init__','api','runtime','gateway','initialization','approvals','assessments','deadlines','delivery','uplink','enrollment','segmented_enrollment','migration','migration_guard','history_boundary','activation','staging','reports','policy','personnel','personnel_api','class_teachers','class_teachers_api','historical','legacy_approvals')
FILES={*(f'management/{n}.py' for n in MODULES),*(f'business/{n}.py' for n in ('__init__','api','models','store','security')),
       'ops/export-management-review.py','ops/temporary-http-viewer.py',*(f'management/web/{n}' for n in ('index.html','workspace.js','workspace.css','future-school-logo.png','personnel.html','personnel.js','class-teachers.js'))}
sha=lambda b:hashlib.sha256(b).hexdigest()


def run(*args,allowed=(0,)):
    p=subprocess.run(['systemctl','--user',*args],capture_output=True,text=True,timeout=45)
    if p.returncode not in allowed:raise ValueError('User service action failed')
    return p.stdout.strip()


def write(path,value):
    raw=value if isinstance(value,bytes) else json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    with path.open('xb') as f:f.write(raw)
    path.chmod(0o600)


def unpack(raw,checksum,target):
    if len(raw)>2*1024*1024 or sha(raw)!=checksum:raise ValueError('Code archive differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or set(names)!=FILES|{'MANIFEST.json'} or sum(f.file_size for f in archive.infolist())>2*1024*1024:raise ValueError('Unexpected release paths')
        manifest=json.loads(archive.read('MANIFEST.json'))
        if manifest.get('format')!='future-school-management-release-v1' or manifest.get('contains_data')is not False or set(manifest['files'])!=FILES:raise ValueError('Code-only manifest required')
        for name in FILES:
            data=archive.read(name)
            if sha(data)!=manifest['files'][name]:raise ValueError('Release source checksum differs')
            file=target/name;file.parent.mkdir(parents=True,mode=0o700,exist_ok=True);write(file,data)
        return manifest


class Cutover:
    def __init__(self):self.phase='new';self.work=None;self.frozen=False;self.created_units=[];self.offered=False

    def require(self,phase):
        if self.phase!=phase:raise ValueError('Cutover phase differs; do not repeat')

    def copy(self,body):
        self.require('new')
        if body.get('accept_service_cutover')is not True or body.get('organization')!='weilai':raise ValueError('Explicit management cutover required')
        captured=body.get('source_captured_at')
        if type(captured)is not int or not -120<=int(time.time())-captured<900:raise ValueError('Fresh native capture required before pausing services')
        if not SOURCE.is_file() or SOURCE.is_symlink() or not PYTHON.is_file() or not APP.is_dir():raise ValueError('Current source runtime differs')
        unit_dir=Path.home()/'.config/systemd/user'
        if any((unit_dir/name).exists() or (unit_dir/name).is_symlink() for name in NEW):raise ValueError('A canonical service already exists; reconcile it before retrying')
        if any(run('is-active',name)!='active' or run('is-enabled',name)!='enabled' for name in OLD):raise ValueError('Current service state differs')
        if SOURCE.stat().st_size>512*1024*1024 or shutil.disk_usage(APP).free<max(200*1024*1024,SOURCE.stat().st_size*4):raise ValueError('Copy size or available disk differs')
        self.work=Path(tempfile.mkdtemp(prefix='.management-cutover-',dir=APP));self.work.chmod(0o700)
        self.release=self.work/'release';self.release.mkdir(mode=0o700)
        code=base64.b64decode(body['code_base64'],validate=True);self.manifest=unpack(code,body['code_sha256'],self.release)
        sys.path.insert(0,str(self.release))
        from management.initialization import export_helper
        self.helper=export_helper();self.helper.private_path(SOURCE.parent,True);self.helper.private_path(SOURCE)
        for name in OLD:write(self.work/(name+'.before.txt'),run('cat',name).encode())
        write(self.work/'source.private.json',dict(source=str(SOURCE),services=list(OLD),phase='before_pause'))
        self.frozen=True
        for name in OLD:run('stop',name)
        if any(run('is-active',name,allowed=(0,3))=='active' for name in OLD):raise ValueError('Original writers did not stop')
        self.database=self.work/'business.sqlite';write(self.database,b'')
        with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(self.database)) as dst:
            src.execute('PRAGMA query_only=ON');src.backup(dst,pages=256)
        self.review=self.helper.snapshot(self.database,'weilai');self.review_raw=json.dumps(self.review,separators=(',',':')).encode()
        write(self.work/'source-review.private.json',self.review_raw)
        self.phase='copied'
        return dict(phase=self.phase,review_base64=base64.b64encode(self.review_raw).decode(),review_sha256=sha(self.review_raw),source_written=False)

    def initialize(self,body):
        self.require('copied')
        from management.initialization import checked,populate
        from management.history_boundary import load
        from management.delivery import DeliveryStore
        from management.policy import require,workspace
        plan_raw=base64.b64decode(body['plan_base64'],validate=True);archive=base64.b64decode(body['archive_base64'],validate=True)
        plan=checked(plan_raw,body['plan_sha256'],8*1024*1024)
        if plan['baseline']['server_review_sha256']!=sha(self.review_raw):raise ValueError('Current full source differs')
        now=int(time.time());captured=body['source_captured_at']
        if type(captured)is not int or not -120<=now-captured<900:raise ValueError('Fresh native capture required')
        with closing(sqlite3.connect(self.database,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            self.receipt=populate(db,plan_raw,body['plan_sha256'],archive,self.review_raw,now)
            self.boundary=load(db,plan['org_id'])
        write(self.work/'initialization.private.json',self.receipt);write(self.work/'boundary.private.json',self.boundary)
        self.store=DeliveryStore(self.database)
        # Authentication is checked against the preserved account hashes locally.
        self.token=self.store.login('weilai',body['web_login'],body['web_password'],'canonical-cutover-local')['access_token']
        self.store.command(self.token,lambda db,actor:require(actor,workspace(actor,'admin'),'settings.manage'))
        self.captured=captured;self.phase='initialized'
        return dict(phase=self.phase,boundary=self.boundary,scope=self.receipt['scope'],accounts_preserved=True,legacy_applications_preserved=True,events_replayed=0)

    def stage(self,body):
        self.require('initialized')
        from management.approvals import digest
        from business.store import uid
        nonce=body['nonce']
        if not isinstance(nonce,str) or len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):raise ValueError('Actual device nonce required')
        schedule=body['schedule'];self.store.stage_runtime(self.token,'admin',schedule=schedule,source_captured_at=self.captured,reason='已核对全校当前资料并明确切换网页主管理',command_ref='production-staging')
        source=schedule['source'];org=self.boundary['org_id'];self.generation=uid();self.profile_id=uid()
        self.device=self.store.command(self.token,lambda db,actor:self.store.create_device(db,actor,dict(name='未来学校签到平板',class_ids=[c['server_id'] for c in source['classes']])))
        with self.store.transaction() as db:
            db.execute('UPDATE devices SET expires_at=? WHERE org_id=? AND id=?',(EXPIRY,org,self.device['id']))
            db.execute('INSERT INTO management_device_access VALUES(?,?,?,?,?)',(org,self.device['id'],self.generation,1,EXPIRY))
            for s in source['students']:db.execute('INSERT INTO management_device_students VALUES(?,?,?,?)',(org,s['server_id'],self.device['id'],s['class_id']))
        review=self.store.review_enrollment(self.token,'admin',device_id=self.device['id'],generation=self.generation,baseline_sha256=self.boundary['baseline_sha256'],source_captured_at=self.captured,source=source)
        proof=self.store.authorize_enrollment(self.token,'admin',review_id=review['review_id'],profile_id=self.profile_id,nonce=nonce)
        self.hold=self.store.prepare_migration(self.token,'admin',review_id=review['review_id'],profile_id=self.profile_id,reason='切换前暂停全部旧镜像写入，等待平板确认',command_ref='production-hold',accept_database_mirror_pause=True)
        with self.store.transaction() as db:recorded=json.loads(db.execute('SELECT source_map FROM management_enrollment_reviews WHERE id=?',(review['review_id'],)).fetchone()[0])
        self.profile=dict(e='https://47.97.226.219:18083',t=self.device['device_token'],d=self.device['id'],p=PIN,o=org,g=self.generation,id=self.profile_id,b=self.boundary['baseline_sha256'],n=nonce,x=proof['expires_at'])
        # Necessary short-lived provisioning recovery material; private owned
        # file only, never sent to a log or returned by the public web API.
        write(self.work/'device-provisioning.private.json',self.profile)
        self.source_hash=digest(recorded);self.phase='staged'
        return dict(phase=self.phase,profile=self.profile,source=recorded,hold_id=self.hold['hold_id'],expires_at=proof['expires_at'])

    def unit(self,name,module,config):
        path=Path.home()/'.config/systemd/user'/name
        value=f'''[Unit]\nDescription=Future School canonical management\nAfter=network.target\n[Service]\nType=simple\nWorkingDirectory={self.release}\nExecStart={PYTHON} -m {module} --config {config}\nRestart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\nUMask=0077\nCPUQuota=50%\nMemoryMax=512M\nTasksMax=64\n[Install]\nWantedBy=default.target\n'''.encode()
        write(path,value);self.created_units.append(path)

    def health(self,port,path='/health/ready',host=None,tls=None):
        c=HTTPSConnection('127.0.0.1',port,context=tls,timeout=5) if tls else HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            headers={'Host':host or '127.0.0.1:'+str(port)}
            c.request('GET',path,headers=headers);r=c.getresponse();raw=r.read(32769)
            if r.status!=200 or len(raw)>32768:raise ValueError('Service health not ready')
            return json.loads(raw)
        finally:c.close()

    def publish(self,body):
        self.require('staged')
        if body.get('source_map_sha256')!=self.source_hash:raise ValueError('Bootstrap source differs')
        runtime=self.work/'runtime.private.json';gateway_cfg=self.work/'gateway.private.json'
        write(runtime,dict(database=str(self.database),files=self.manifest['files']))
        tls_source=SOURCE.parent/'mirror-tls';tls=self.work/'tls';tls.mkdir(mode=0o700)
        hashes={}
        for key,name in [('certificate','server.crt'),('private_key','server.key')]:
            src=tls_source/name;self.helper.private_path(src);raw=src.read_bytes();write(tls/name,raw);hashes[key]=sha(raw)
        if hashes['certificate']!='320750ba1113dafa0410bcdbeffd7b04a68f42f64b74744adf01731bad92e8fe':raise ValueError('Original TLS identity differs')
        write(gateway_cfg,dict(origin='http://47.97.226.219:18083',expires_at=EXPIRY,certificate=str(tls/'server.crt'),private_key=str(tls/'server.key'),tls_sha256=hashes,gateway_sha256=self.manifest['files']['management/gateway.py']))
        self.unit(NEW[1],'management.runtime',runtime);self.unit(NEW[0],'management.gateway',gateway_cfg);run('daemon-reload');run('start',NEW[1])
        for attempt in range(20):
            try:
                h=self.health(18098)
                if h.get('mode')!='management_staged' or h.get('runtime_admitted')is not True:raise ValueError('Wrong runtime')
                break
            except (OSError,ValueError):
                if attempt==19:raise
                time.sleep(.5)
        # Preflight the actual gateway on loopback BEFORE creating an offer.
        from management.gateway import Viewer
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(tls/'server.crt',tls/'server.key')
        trusted=ssl.create_default_context(cafile=str(tls/'server.crt'))
        viewer=Viewer(('127.0.0.1',0),'http://47.97.226.219:18083',EXPIRY,context,upstream=('127.0.0.1',18098));thread=threading.Thread(target=viewer.serve_forever,daemon=True);thread.start()
        try:
            test=self.health(viewer.server_address[1],'/viewer/health','47.97.226.219:18083',trusted)
            if test.get('legacy_mirror_writable')is not False:raise ValueError('Gateway preflight differs')
        finally:viewer.shutdown();viewer.server_close();thread.join(timeout=5)
        offer=self.store.offer_activation(self.token,'admin',hold_id=self.hold['hold_id'],bootstrap_sha256=body['bootstrap_sha256'],source_map_sha256=self.source_hash,reason='已通过同轮全校基线及加密入口核验，等待平板持久化确认',command_ref='production-offer')
        self.offered=True;write(self.work/'activation-offer.private.json',offer)
        run('start',NEW[0])
        for attempt in range(20):
            try:live=self.health(18083,'/viewer/health','47.97.226.219:18083',trusted);break
            except (OSError,ValueError):
                if attempt==19:raise
                time.sleep(.5)
        if live.get('gateway_sha256')!=self.manifest['files']['management/gateway.py']:raise ValueError('Published gateway differs')
        for name in NEW:run('enable',name)
        for name in OLD:run('disable',name)
        self.phase='published'
        receipt=dict(status='awaiting_device_commit',phase=self.phase,remote_directory=str(self.work),database=str(self.database),scope=self.receipt['scope'],activation_id=offer['activation_id'],expires_at=offer['offer_expires_at'],original_source_written=False,accounts_preserved=True,legacy_applications_preserved=True,old_mirror_writable=False,new_units=list(NEW))
        write(self.work/'publish-receipt.private.json',receipt);return receipt

    def cleanup_failure(self):
        if not self.frozen:return 'original_not_paused'
        # Checking the DB covers failure after offer COMMIT but before the Python
        # caller returned. An unreadable DB is uncertainty, never permission to revert.
        try:
            if hasattr(self,'database') and self.database.is_file():
                with closing(sqlite3.connect(self.database)) as db:
                    table=db.execute("SELECT 1 FROM sqlite_schema WHERE name='management_activations'").fetchone()
                    if table and db.execute('SELECT 1 FROM management_activations LIMIT 1').fetchone():self.offered=True
        except BaseException:return 'reconciliation_required'
        if self.offered:return 'reconciliation_required'
        for file in self.created_units:run('stop',file.name,allowed=(0,5))
        for file in self.created_units:
            if file.parent==Path.home()/'.config/systemd/user' and file.name in NEW:file.unlink()
        run('daemon-reload')
        for name in reversed(OLD):run('enable',name);run('start',name)
        self.frozen=False;self.created_units=[]
        return 'original_services_restored'


def main():
    cut=Cutover();sequence=0;reset_allowed=False;resets=0;started=time.monotonic()
    def interrupted(*_):raise InterruptedError('SSH session interrupted')
    for name in ('SIGTERM','SIGHUP'):
        if hasattr(signal,name):signal.signal(getattr(signal,name),interrupted)
    try:
        while True:
            line=sys.stdin.readline(24*1024*1024+1)
            if not line:break
            if len(line)>24*1024*1024:raise ValueError('RPC size limit')
            message=json.loads(line);sequence+=1
            if time.monotonic()-started>1800:raise ValueError('Bounded cutover session expired')
            if set(message)!={'sequence','action','body'} or message['sequence']!=sequence or message['action'] not in {'copy','initialize','stage','publish','finish','diagnose','reset'}:raise ValueError('Fixed ordered cutover action required')
            action=message['action']
            if action=='diagnose':
                diagnostics={}
                for name in NEW:
                    result=subprocess.run(['journalctl','--user','-u',name,'-n','45','--no-pager','--output=cat'],capture_output=True,text=True,timeout=10)
                    text=result.stdout[-16000:]
                    text=re.sub(r'wxd_[A-Za-z0-9_-]{43}|Bearer\s+\S+','[REDACTED]',text)
                    diagnostics[name]=text
                print(json.dumps(dict(sequence=sequence,ok=True,data=dict(diagnostics=diagnostics,phase=cut.phase,reset_allowed=reset_allowed)),separators=(',',':')),flush=True);continue
            if action=='reset':
                if not reset_allowed or resets>=2:raise ValueError('Only a proven preoffer restoration can be retried')
                for name in list(sys.modules):
                    if name in {'management','business'} or name.startswith(('management.','business.')):del sys.modules[name]
                if hasattr(cut,'release'):sys.path[:]=[p for p in sys.path if p!=str(cut.release)]
                cut=Cutover();reset_allowed=False;resets+=1
                print(json.dumps(dict(sequence=sequence,ok=True,data=dict(reset=True))),flush=True);continue
            if action=='finish':
                cut.require('published');print(json.dumps(dict(sequence=sequence,ok=True,data=dict(finished=True))),flush=True);return
            try:
                result=getattr(cut,action)(message['body']);print(json.dumps(dict(sequence=sequence,ok=True,data=result),separators=(',',':')),flush=True)
            except BaseException as error:
                try:recovery=cut.cleanup_failure()
                except BaseException:recovery='reconciliation_required'
                code=getattr(error,'code','input_or_service_check_failed')
                if not isinstance(code,str) or len(code)>80 or any(c not in 'abcdefghijklmnopqrstuvwxyz_' for c in code):code='input_or_service_check_failed'
                detail=str(error) if isinstance(error,ValueError) else type(error).__name__
                detail=re.sub(r'wxd_[A-Za-z0-9_-]{43}|Bearer\s+\S+','[REDACTED]',detail)[:300]
                print(json.dumps(dict(sequence=sequence,ok=False,error='controlled_cutover_stopped',error_code=code,error_phase=cut.phase,error_detail=detail,recovery=recovery,remote_directory=str(cut.work) if cut.work else None)),flush=True)
                reset_allowed=recovery in {'original_services_restored','original_not_paused'}
                cut.phase='failed'
        raise ValueError('SSH input ended before publication finished')
    except BaseException as error:
        try:recovery=cut.cleanup_failure()
        except BaseException:recovery='reconciliation_required'
        code=getattr(error,'code','input_or_service_check_failed')
        if not isinstance(code,str) or len(code)>80 or any(c not in 'abcdefghijklmnopqrstuvwxyz_' for c in code):code='input_or_service_check_failed'
        print(json.dumps(dict(sequence=sequence,ok=False,error='controlled_cutover_stopped',error_code=code,error_phase=cut.phase,recovery=recovery,remote_directory=str(cut.work) if cut.work else None)),flush=True)
        raise SystemExit(1)


if __name__=='__main__':main()
