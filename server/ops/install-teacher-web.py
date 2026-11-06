"""Create an independent web DB and backend; atomically switch only the existing temporary proxy."""
import base64
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time

PYTHON='/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python'
RELEASE='/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release'
SOURCE='/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/data/roster.sqlite'
OLD_WORK='/opt/wxnotify/app/http-viewer-20261028100749-15dcb449'
OLD_SHA='b21dfd3214fb27c102218d08da5b389a5949cb2d677856228bda8416fd80b394'
UNIT='wxnotify-teacher-web.service'
VIEWER='wxnotify-http-viewer.service'
APP_ROOT=Path('/opt/wxnotify/app')
MANIFEST_SHA='df5e8eafce1f220d2252b1768db50a8a869bf2a2b2e563cd60a6312e42f10359'
FILES={'teacher_web/__init__.py','teacher_web/api.py','teacher_web/runtime.py','teacher_web/gateway.py','teacher_web/web/index.html','teacher_web/web/workspace.js','teacher_web/web/workspace.css','base-viewer.py'}


def run(args,allowed=(0,)):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode not in allowed:raise ValueError('User-service operation failed: '+args[-1])
    return p.stdout.strip()


def health(port,host=None):
    c=HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        c.request('GET','/viewer/health' if port==18083 else '/health/ready',headers={'Host':host or '127.0.0.1:'+str(port)});r=c.getresponse();b=r.read(32769)
        if r.status!=200 or len(b)>32768:raise ValueError('Web health failed')
        return json.loads(b)
    finally:c.close()


def install(config,payload):
    if set(payload['files'])!=FILES or config['public_origin']!='http://47.97.226.219:18083' or config['expires_at']!=1794391683 or config.get('makeup_mode')!='request_pending_tablet':raise ValueError('Existing origin, expiry and request-only mode required')
    if os.geteuid()==0:raise ValueError('Ordinary deployment account required')
    manifest=Path(RELEASE)/'MANIFEST.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest()!=MANIFEST_SHA:raise ValueError('Frozen release manifest differs')
    frozen=json.loads(manifest.read_bytes())['files']
    if len(frozen)!=27 or any(hashlib.sha256((Path(RELEASE)/name).read_bytes()).hexdigest()!=digest for name,digest in frozen.items()):raise ValueError('Frozen release source changed')
    if payload['files']['base-viewer.py']['sha256']!=OLD_SHA:raise ValueError('Verified proxy base required')
    root=APP_ROOT
    if root.is_symlink() or not root.is_dir():raise ValueError('Existing app root required')
    old=Path(OLD_WORK)/'temporary-http-viewer.py'
    if old.is_symlink() or hashlib.sha256(old.read_bytes()).hexdigest()!=OLD_SHA:raise ValueError('Original viewer changed; inspect before switching')
    if run(['systemctl','--user','show',VIEWER,'-p','WorkingDirectory','--value'])!=OLD_WORK:raise ValueError('Original viewer directory differs')
    if run(['systemctl','--user','is-active',VIEWER])!='active':raise ValueError('Original viewer not active')
    units=Path.home()/'.config/systemd/user';dropdir=units/(VIEWER+'.d');target=units/UNIT
    if target.exists() or (units/(UNIT+'.d')).exists() or dropdir.exists():raise ValueError('Web backend or viewer override already exists; do not reinstall')
    if run(['systemctl','--user','show',UNIT,'-p','LoadState','--value'],(0,1))!='not-found':raise ValueError('New backend unit is not new')
    original=health(18083,'47.97.226.219:18083')
    if original.get('gateway_sha256')!=OLD_SHA or original.get('expires_at')!=config['expires_at']:raise ValueError('Original public viewer health differs')
    with socket.socket() as probe:probe.bind(('127.0.0.1',18086))
    work=root/('teacher-web-'+time.strftime('%Y%m%d%H%M%S')+'-'+os.urandom(4).hex());work.mkdir(mode=0o700)
    for name,entry in payload['files'].items():
        raw=base64.b64decode(entry['base64'],validate=True)
        if len(raw)>131072 or hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Web source checksum differs')
        p=work/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with os.fdopen(os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as f:f.write(raw)
    raw=base64.b64decode(payload['dataset']['base64'],validate=True)
    if len(raw)>12*1024*1024 or hashlib.sha256(raw).hexdigest()!=payload['dataset']['sha256']:raise ValueError('Dataset checksum differs')
    data=json.loads(raw)
    src=sqlite3.connect(Path(SOURCE).as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(work/'business.sqlite')
    try:src.execute('PRAGMA query_only=ON');src.backup(dst)
    finally:src.close();dst.close()
    os.chmod(work/'business.sqlite',0o600)
    sys.path.insert(0,RELEASE);sys.path.insert(0,str(work))
    from teacher_web.api import install_data
    summary=install_data(work/'business.sqlite',data)
    with sqlite3.connect(work/'business.sqlite') as db:db.execute('DELETE FROM sessions')
    (work/'web.private.json').write_text(json.dumps({'files':{k:v['sha256'] for k,v in payload['files'].items()},'source_sha256':data['source_sha256']}),encoding='utf-8');os.chmod(work/'web.private.json',0o600)
    content=f'''[Unit]\nDescription=Future School teacher web and pending makeup requests\nStartLimitIntervalSec=60\nStartLimitBurst=3\n[Service]\nType=exec\nWorkingDirectory={work}\nExecStart={PYTHON} -u {work}/teacher_web/runtime.py\nRestart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\nUMask=0077\nMemoryMax=384M\nCPUQuota=75%\nTasksMax=64\nEnvironment=PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1\nUnsetEnvironment=PYTHONPATH PYTHONHOME\nStandardOutput=null\nStandardError=journal\n[Install]\nWantedBy=default.target\n'''
    switched=False;written=False
    try:
        with os.fdopen(os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:f.write(content)
        written=True;run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','enable','--now',UNIT])
        for attempt in range(30):
            try:
                h=health(18086)
                if h.get('mode')=='teacher_web' and h.get('source_sha256')==data['source_sha256'] and h.get('makeup_mode')=='request_pending_tablet':break
            except (OSError,ValueError):pass
            time.sleep(.5)
        else:raise ValueError('New web backend not ready')
        dropdir.mkdir(mode=0o700);override=dropdir/'teacher-web.conf'
        override.write_text(f'[Service]\nWorkingDirectory={work}\nExecStart=\nExecStart={PYTHON} -u {work}/teacher_web/gateway.py --origin {config["public_origin"]} --expires-at {config["expires_at"]} --temporary-http-confirmed\n',encoding='utf-8');switched=True
        run(['systemctl','--user','stop',VIEWER]);run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','reset-failed',VIEWER])
        for _ in range(90):
            try:
                with socket.socket() as probe:probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);probe.bind(('0.0.0.0',18083))
                break
            except OSError:time.sleep(1)
        else:raise ValueError('Existing viewer port did not release')
        run(['systemctl','--user','start',VIEWER])
        for _ in range(30):
            try:
                h=health(18083,'47.97.226.219:18083')
                if h.get('viewer')=='teacher_web' and h.get('gateway_sha256')==payload['files']['teacher_web/gateway.py']['sha256'] and h.get('expires_at')==config['expires_at']:break
            except (OSError,ValueError):pass
            time.sleep(.5)
        else:raise ValueError('Updated public viewer not ready')
        result={'format':'future-school-teacher-web-installed','schema_version':1,'public_url':config['public_origin'],'expires_at':config['expires_at'],'work_directory':str(work),'gateway_sha256':payload['files']['teacher_web/gateway.py']['sha256'],'dataset_sha256':payload['dataset']['sha256'],'source_sha256':data['source_sha256'],'source_captured_at':data['source_captured_at'],'summary':summary,'makeup_mode':'request_pending_tablet','accounts_preserved':True,'old_roster_database_modified':False,'tablet_modified':False,'hours_deducted':False,'wechat_enabled':False,'device_sync_enabled':False,'backend_unit':UNIT,'service_state':run(['systemctl','--user','show',UNIT,'-p','ActiveState','-p','SubState','-p','UnitFileState','-p','NRestarts'])}
        (work/'install-receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8');return result
    except BaseException as failure:
        if switched:
            run(['systemctl','--user','stop',VIEWER],(0,1,5));(dropdir/'teacher-web.conf').unlink();dropdir.rmdir();run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','reset-failed',VIEWER]);run(['systemctl','--user','start',VIEWER])
        if written:run(['systemctl','--user','disable','--now',UNIT],(0,1,5))
        if switched:
            restored=False
            for _ in range(30):
                try:
                    if health(18083,'47.97.226.219:18083').get('gateway_sha256')==OLD_SHA:restored=True;break
                except (ValueError,OSError):pass
                time.sleep(.5)
            if not restored:raise ValueError('Update failed; original viewer recovery needs inspection') from failure
            raise ValueError('Update failed; original viewer restored and verified') from failure
        raise
