"""Install a NEW loopback teacher phase from the immutable candidate. Standby remains available."""
import base64
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

UNIT="wxnotify-single-class-teacher.service"
RELEASE="/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release"
PYTHON="/opt/wxnotify/app/business-v0.4.0-c59dab1f/.venv/bin/python"
FILES={"scoped-pilot.py","scoped-pilot.html","scoped-pilot-ui.js","scoped-pilot.css","stage-single-class-pilot.py","pilot-standby.py"}


def run(args,allowed=(0,)):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode not in allowed:raise ValueError("Scoped user-service operation failed")
    return p.stdout.strip()


def root(value):
    if not re.fullmatch(r"/opt/wxnotify/app/single-class-offline-\d{14}-[a-f0-9]{8}",value):raise ValueError("Confirmed stage root required")
    return Path(value)


def health(port):
    c=HTTPConnection('127.0.0.1',port,timeout=4)
    try:
        c.request('GET','/health/ready');r=c.getresponse();raw=r.read(32769)
        if r.status!=200 or len(raw)>32768:raise ValueError("Scoped readiness not confirmed")
        return json.loads(raw)
    finally:c.close()


def program(raw,name):
    ns={'__name__':'scoped_install_'+name.replace('.','_')};exec(compile(raw,name,'exec'),ns);return ns


def unit(work):
    if not re.fullmatch(r"/opt/wxnotify/app/single-class-offline-\d{14}-[a-f0-9]{8}/teacher-pilot",str(work)):raise ValueError("Fixed new teacher work directory required")
    return f"""[Unit]
Description=Future School single confirmed lesson teacher preparation (device sync disabled)
StartLimitIntervalSec=60
StartLimitBurst=3
[Service]
Type=exec
WorkingDirectory={work}
ExecStart={PYTHON} -u {work}/scoped-pilot.py --config {work}/scoped.private.json
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=yes
UMask=0077
LimitCORE=0
LimitNOFILE=512
MemoryMax=256M
CPUQuota=50%
TasksMax=32
Environment=WXNOTIFY_ENABLE_WECHAT=0 WXNOTIFY_ENABLE_STUDENT_SYNC=0 WXNOTIFY_ENABLE_PARENT_BINDING=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
UnsetEnvironment=PYTHONPATH PYTHONHOME
StandardOutput=null
StandardError=journal
SyslogIdentifier=wxnotify-single-class-teacher
[Install]
WantedBy=default.target
"""


def install(config,files):
    if set(files)!=FILES:raise ValueError("Complete fixed scoped programs required")
    payloads={}
    for name,item in files.items():
        raw=base64.b64decode(item['base64'],validate=True)
        if len(raw)>131072 or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError("Scoped file checksum differs")
        if name.endswith('.py'):compile(raw,name,'exec')
        payloads[name]=raw
    helper=program(payloads['stage-single-class-pilot.py'],'stage-single-class-pilot.py')
    stage=root(config['stage_directory']);helper['private'](stage,True)
    stage_file=stage/'stage-receipt.json';helper['private'](stage_file)
    receipt=json.loads(stage_file.read_bytes())
    if receipt!=config['stage_receipt'] or receipt.get('stage_complete') is not True:raise ValueError("Confirmed offline receipt differs")
    installed=stage/'standby/install-receipt.json';helper['private'](installed)
    if json.loads(installed.read_bytes())!=config['standby_receipt']:raise ValueError("Confirmed standby proof differs")
    plan_path=stage/'confirmed-plan.private.json';helper['private'](plan_path)
    plan=json.loads(plan_path.read_bytes())
    if plan!=config['plan']:raise ValueError("Owner-confirmed lesson differs")
    helper['validate_plan'](plan,int(time.time()))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}",config['actor_login']):raise ValueError("Explicit operator login required")
    for name,key in [('baseline-backup.sqlite','baseline_backup_sha256'),('pilot-offline.sqlite','candidate_sha256')]:
        p=stage/name;helper['private'](p)
        if helper['checksum'](p)!=receipt[key]:raise ValueError("Immutable offline source changed")
    verifier=program(payloads['pilot-standby.py'],'pilot-standby.py');verifier['verify_release'](Path(RELEASE))
    for old in ['wxnotify-roster-review.service','wxnotify-http-viewer.service','wxnotify-single-class-standby.service']:
        if run(['systemctl','--user','is-active',old])!='active':raise ValueError("Existing service needs inspection")
    old=health(18084)
    if old.get('mode')!='pilot_standby' or old.get('roster_review_only') is not True or old.get('standby_code_sha256')!=config['standby_receipt']['runtime_sha256']:raise ValueError("Existing standby changed")
    work=stage/'teacher-pilot';units=Path.home()/'.config/systemd/user';helper['private'](units,True);target=units/UNIT
    if work.exists() or work.is_symlink() or target.exists() or target.is_symlink() or (units/(UNIT+'.d')).exists():raise ValueError("Teacher phase already prepared; inspect instead of repeating")
    if run(['systemctl','--user','show',UNIT,'-p','LoadState','--value'],allowed=(0,1))!='not-found':raise ValueError("Teacher phase unit is not new")
    with socket.socket() as probe:probe.bind(('127.0.0.1',18085))
    content=unit(work);work.mkdir(mode=0o700)
    for name,raw in payloads.items():
        with os.fdopen(os.open(work/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:stream.write(raw)
    sys.path.insert(0,RELEASE)
    # Import under a module name so the preparation code uses ordinary module attributes.
    import importlib.util
    def load(name,path):
        s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    module=load('scoped_prepare',work/'scoped-pilot.py');helper_module=load('scoped_helper',work/'stage-single-class-pilot.py')
    scope=module.prepare_database(stage/'pilot-offline.sqlite',work/'business.sqlite',plan,receipt,helper_module,config['actor_login'])
    cfg={'format':'future-school-scoped-teacher-config','plan':plan,'scope':scope,'files':{name:item['sha256'] for name,item in files.items()}}
    with os.fdopen(os.open(work/'scoped.private.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as stream:json.dump(cfg,stream,ensure_ascii=True,indent=2)
    written=False
    try:
        with os.fdopen(os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as stream:stream.write(content)
        written=True;run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','enable','--now',UNIT])
        ready=None
        for _ in range(30):
            try:
                h=health(18085)
                if h.get('mode')=='pilot_scoped_teacher' and h.get('pilot_phase')=='teacher_preparation' and h.get('roster_review_only') is False and h.get('scoped_code_sha256')==files['scoped-pilot.py']['sha256'] and h.get('lesson_id')==scope['lesson_id'] and h.get('selected_students')==len(scope['student_ids']) and h.get('teacher_class_writes_enabled') is True and all(h.get(k) is False for k in ['business_ready','student_sync_enabled','wechat_sending_enabled','device_registration_enabled','parent_binding_enabled']):ready=h;break
            except (ValueError,OSError):pass
            time.sleep(.5)
        if ready is None:raise ValueError("Teacher phase readiness failed")
        for old in ['wxnotify-roster-review.service','wxnotify-http-viewer.service','wxnotify-single-class-standby.service']:
            if run(['systemctl','--user','is-active',old])!='active':raise ValueError("Existing service state changed")
        if helper['checksum'](stage/'pilot-offline.sqlite')!=receipt['candidate_sha256']:raise ValueError("Original candidate changed")
        result={'format':'future-school-scoped-pilot-installed','schema_version':1,'unit':UNIT,'stage_directory':str(stage),
            'runtime_sha256':files['scoped-pilot.py']['sha256'],'lesson_id':scope['lesson_id'],'starts_at':plan['starts_at'],'ends_at':plan['ends_at'],
            'expected_students':len(scope['student_ids']),'opening_remaining_units':receipt['selected_remaining_units'],'port':18085,'loopback_only':True,
            'teacher_scope_ready':True,'lesson_created':True,'source_candidate_unchanged':True,'existing_services_modified':False,
            'device_enabled':False,'wechat_enabled':False,'parent_binding_enabled':False,
            'service_state':run(['systemctl','--user','show',UNIT,'--no-pager','-p','ActiveState','-p','SubState','-p','UnitFileState','-p','NRestarts'])}
        with os.fdopen(os.open(work/'install-receipt.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
        return result
    except BaseException:
        if written:run(['systemctl','--user','disable','--now',UNIT])
        raise


def pause(config):
    stage=root(config['stage_directory'])
    if run(['systemctl','--user','show',UNIT,'-p','WorkingDirectory','--value'])!=str(stage/'teacher-pilot'):raise ValueError("Pause directory differs")
    run(['systemctl','--user','disable','--now',UNIT]);state=run(['systemctl','--user','show',UNIT,'-p','ActiveState','-p','UnitFileState'])
    if 'ActiveState=inactive' not in state or 'UnitFileState=disabled' not in state:raise ValueError("Scoped pause not confirmed")
    return {'format':'future-school-scoped-pilot-paused','schema_version':1,'unit':UNIT,'stage_directory':str(stage),'service_state':state,'existing_services_modified':False}
