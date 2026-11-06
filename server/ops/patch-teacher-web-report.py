"""Update explicitly scoped web files; preserve the database and existing gateway."""
import base64
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

ROOT=Path('/opt/wxnotify/app')
UNIT='wxnotify-teacher-web.service'
FILES={'teacher_web/api.py','teacher_web/web/workspace.js'}
ROLE_FILES=FILES|{'teacher_web/web/workspace.css'}
sha=lambda b:hashlib.sha256(b).hexdigest()


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode:raise ValueError('Teacher web service operation failed')
    return p.stdout.strip()


def health():
    c=HTTPConnection('127.0.0.1',18086,timeout=5)
    try:
        c.request('GET','/health/ready');r=c.getresponse();b=r.read(32769)
        if r.status!=200 or len(b)>32768:raise ValueError('Teacher web is not ready')
        return json.loads(b)
    finally:c.close()


def wait_ready(source,revision=None,ui_revision=None):
    for _ in range(30):
        try:
            h=health()
            if h.get('source_sha256')==source and h.get('mode')=='teacher_web' and h.get('makeup_mode')=='request_pending_tablet' and h.get('student_sync_enabled') is False and h.get('wechat_sending_enabled') is False and (revision is None or h.get('report_display_version')==revision) and (ui_revision is None or h.get('workspace_ui_version')==ui_revision and h.get('workspace_role_selection') is True):return
        except (OSError,ValueError):pass
        time.sleep(.5)
    raise ValueError('Teacher web readiness failed')


def replace(p,raw):
    temp=p.with_name(p.name+'.report-patch-'+os.urandom(4).hex())
    with os.fdopen(os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as f:f.write(raw)
    os.replace(temp,p)


def install(config,payload):
    kind=config.get('kind','report_display')
    if kind not in {'report_display','role_interface'}:raise ValueError('Unknown patch kind')
    names=ROLE_FILES if kind=='role_interface' else FILES
    if os.geteuid()==0 or set(payload['files'])!=names:raise ValueError('Ordinary account and bounded patch required')
    work=Path(config['work_directory'])
    if work.is_symlink() or work.resolve().parent!=ROOT.resolve() or not work.name.startswith('teacher-web-'):raise ValueError('Pinned teacher web directory required')
    if run(['systemctl','--user','show',UNIT,'-p','WorkingDirectory','--value'])!=str(work):raise ValueError('Teacher web directory differs')
    if run(['systemctl','--user','is-active',UNIT])!='active':raise ValueError('Teacher web is not active')
    cfg_path=work/'web.private.json'
    if cfg_path.is_symlink():raise ValueError('Unexpected configuration link')
    cfg_raw=cfg_path.read_bytes();cfg=json.loads(cfg_raw)
    if cfg!={'files':config['previous_files'],'source_sha256':config['source_sha256']}:raise ValueError('Runtime pins changed; do not repeat patch')
    for name,digest in cfg['files'].items():
        p=work/name
        if p.is_symlink() or sha(p.read_bytes())!=digest:raise ValueError('Existing pinned source changed')
    wait_ready(config['source_sha256'])
    old={name:(work/name).read_bytes() for name in names};new={}
    for name,entry in payload['files'].items():
        raw=base64.b64decode(entry['base64'],validate=True)
        if len(raw)>131072 or sha(raw)!=entry['sha256']:raise ValueError('New source checksum differs')
        new[name]=raw
    prefix='role-ui-backup-' if kind=='role_interface' else 'report-display-backup-'
    backup=work/(prefix+time.strftime('%Y%m%d%H%M%S')+'-'+os.urandom(4).hex());backup.mkdir(mode=0o700)
    for name,raw in {**old,'web.private.json':cfg_raw}.items():
        p=backup/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(raw);os.chmod(p,0o600)
    stopped=False
    try:
        run(['systemctl','--user','stop',UNIT]);stopped=True
        src=sqlite3.connect((work/'business.sqlite').as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(backup/'before.sqlite')
        try:
            src.execute('PRAGMA query_only=ON');src.backup(dst)
            pending=src.execute('SELECT count(*) FROM web_makeup_requests').fetchone()[0]
        finally:src.close();dst.close()
        os.chmod(backup/'before.sqlite',0o600)
        for name,raw in new.items():replace(work/name,raw);cfg['files'][name]=sha(raw)
        replace(cfg_path,json.dumps(cfg).encode())
        run(['systemctl','--user','reset-failed',UNIT]);run(['systemctl','--user','start',UNIT]);wait_ready(config['source_sha256'],2,3 if kind=='role_interface' else None)
        result={'format':'future-school-teacher-web-role-ui-patched' if kind=='role_interface' else 'future-school-teacher-web-report-patched','work_directory':str(work),'backup_directory':str(backup),'source_sha256':config['source_sha256'],'new_files':{n:sha(v) for n,v in new.items()},'report_display_version':2,'requests_before':pending,'database_written':False,'gateway_changed':False,'tablet_changed':False,'service_state':run(['systemctl','--user','show',UNIT,'-p','ActiveState','-p','SubState','-p','UnitFileState'])}
        if kind=='role_interface':result.update(workspace_ui_version=3,workspace_role_selection=True)
        (backup/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8');return result
    except BaseException as failure:
        if stopped:
            run(['systemctl','--user','stop',UNIT])
            for name,raw in old.items():replace(work/name,raw)
            replace(cfg_path,cfg_raw);run(['systemctl','--user','reset-failed',UNIT]);run(['systemctl','--user','start',UNIT])
            try:wait_ready(config['source_sha256'])
            except (OSError,ValueError) as recovery:raise ValueError('Report patch failed; original web recovery needs inspection') from recovery
            raise ValueError('Report patch failed; original teacher web restored and verified') from failure
        raise
