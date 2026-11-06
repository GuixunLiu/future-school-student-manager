"""Add the tablet mirror to the current web instance; never replace its database."""
import base64,hashlib,json,os,socket,sqlite3,ssl,subprocess,time
from pathlib import Path
from http.client import HTTPConnection,HTTPSConnection

ROOT=Path('/opt/wxnotify/app');UNIT='wxnotify-teacher-web.service';GATE='wxnotify-http-viewer.service'
NAMES={'teacher_web/api.py','teacher_web/web/workspace.js','teacher_web/mirror_store.py','teacher_web/mirror_gateway.py'}
sha=lambda b:hashlib.sha256(b).hexdigest()
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=35)
    if p.returncode:raise ValueError('Service operation failed: '+args[-1])
    return p.stdout.strip()
def replace(p,b):
    temp=p.with_name(p.name+'.mirror-new');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(b)
    os.replace(temp,p)
def get(port,path,context=None,host=None):
    c=HTTPSConnection('127.0.0.1',port,timeout=4,context=context) if context else HTTPConnection('127.0.0.1',port,timeout=4)
    try:
        c.request('GET',path,headers={'Host':host} if host else {});r=c.getresponse();b=r.read(32769)
        if r.status!=200 or len(b)>32768:raise ValueError('Readiness failed')
        return json.loads(b)
    finally:c.close()
def wait(port,path,check,context=None,host=None):
    for _ in range(40):
        try:
            h=get(port,path,context,host)
            if check(h):return h
        except (OSError,ValueError):pass
        time.sleep(.5)
    raise ValueError('Readiness timed out')
def drain():
    for _ in range(180):
        with socket.socket() as s:
            try:s.bind(('0.0.0.0',18083));return
            except OSError:pass
        time.sleep(.5)
    raise ValueError('Port still occupied')
def accounts(db):
    return sha(repr([list(db.execute('SELECT * FROM '+t+' ORDER BY 1,2')) for t in ['users','roles','teacher_classes']]).encode())
def install(plan,payload):
    if os.geteuid()==0 or set(payload['files'])!=NAMES:raise ValueError('Bounded ordinary-account install required')
    work=Path(plan['work_directory']);drop=Path.home()/'.config/systemd/user/wxnotify-http-viewer.service.d/teacher-web.conf'
    if work.is_symlink() or work.resolve().parent!=ROOT or work.name!='teacher-web-20261029101834-42da0325':raise ValueError('Current web directory differs')
    if run(['systemctl','--user','show',UNIT,'-p','WorkingDirectory','--value'])!=str(work):raise ValueError('Service differs')
    cfg_path=work/'web.private.json';cfg_raw=cfg_path.read_bytes();cfg=json.loads(cfg_raw)
    if cfg['files']!=plan['previous_files'] or cfg['source_sha256']!=plan['source_sha256'] or (work/'mirror.private.json').exists():raise ValueError('Source changed or mirror already installed; inspect before repeating')
    for name,digest in cfg['files'].items():
        p=work/name
        if p.is_symlink() or sha(p.read_bytes())!=digest:raise ValueError('Source checksum differs')
    old_drop=drop.read_bytes()
    if b'/teacher_web/gateway.py' not in old_drop or b'1794391683' not in old_drop:raise ValueError('Gateway configuration changed')
    new={}
    for name,entry in payload['files'].items():
        b=base64.b64decode(entry['base64'],validate=True)
        if len(b)>150000 or sha(b)!=entry['sha256']:raise ValueError('Payload checksum differs')
        compile(b,name,'exec') if name.endswith('.py') else None
        new[name]=b
    tls={n:base64.b64decode(payload['tls'][n],validate=True) for n in ['server.crt','server.key']}
    if any(len(b)>8192 for b in tls.values()):raise ValueError('TLS limit')
    backup=work/('mirror-backup-'+os.urandom(8).hex());backup.mkdir(mode=0o700)
    old={n:(work/n).read_bytes() for n in NAMES if (work/n).exists()}
    for name,b in {**old,'web.private.json':cfg_raw,'gateway-dropin.conf':old_drop}.items():
        p=backup/name;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);p.write_bytes(b);p.chmod(0o600)
    stopped=False;database=work/'business.sqlite'
    try:
        stopped=True;run(['systemctl','--user','stop',GATE]);run(['systemctl','--user','stop',UNIT]);drain()
        with sqlite3.connect(database) as db,sqlite3.connect(backup/'before.sqlite') as dst:
            db.backup(dst);before=accounts(db);pending=db.execute('SELECT count(*) FROM web_makeup_requests').fetchone()[0]
            facts=repr(db.execute('SELECT * FROM web_dataset').fetchall());requests=repr(db.execute('SELECT * FROM web_makeup_requests ORDER BY id').fetchall())
            if db.execute('SELECT count(*) FROM users').fetchone()[0]!=12:raise ValueError('Current account set differs')
        (backup/'before.sqlite').chmod(0o600)
        for name,b in new.items():replace(work/name,b);cfg['files'][name]=sha(b)
        replace(cfg_path,json.dumps(cfg).encode())
        folder=work/'mirror-tls';folder.mkdir(mode=0o700)
        for name,b in tls.items():replace(folder/name,b)
        replace(work/'mirror.private.json',json.dumps({'tls_files':{n:sha(b) for n,b in tls.items()}}).encode())
        ns={};exec(compile(new['teacher_web/mirror_store.py'],'mirror-store','exec'),ns);ns['initialize'](database)
        device=plan['device']
        if not __import__('re').fullmatch('[a-f0-9]{32}',device['id']) or not __import__('re').fullmatch('[a-f0-9]{64}',device['token_digest']):raise ValueError('Invalid device enrollment')
        with sqlite3.connect(database) as db:
            org=db.execute("SELECT id FROM organizations WHERE slug='weilai'").fetchone()
            if not org:raise ValueError('Organization missing')
            db.execute('INSERT INTO web_mirror_devices(id,org_id,token_digest,active,expires_at) VALUES(?,?,?,1,?)',(device['id'],org[0],device['token_digest'],1794391683))
            if accounts(db)!=before or repr(db.execute('SELECT * FROM web_dataset').fetchall())!=facts or repr(db.execute('SELECT * FROM web_makeup_requests ORDER BY id').fetchall())!=requests:raise ValueError('Existing accounts or web facts changed')
        replace(drop,old_drop.replace(b'/teacher_web/gateway.py',b'/teacher_web/mirror_gateway.py'))
        run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','reset-failed',UNIT]);run(['systemctl','--user','start',UNIT])
        wait(18086,'/health/ready',lambda h:h.get('workspace_ui_version')==3 and h.get('source_sha256')==cfg['source_sha256'])
        run(['systemctl','--user','reset-failed',GATE]);run(['systemctl','--user','start',GATE])
        check=lambda h:h.get('gateway_sha256')==sha(new['teacher_web/mirror_gateway.py']) and h.get('mirror_tls_enabled') is True
        wait(18083,'/viewer/health',check,host='47.97.226.219:18083')
        context=ssl.create_default_context(cafile=str(folder/'server.crt'))
        wait(18083,'/viewer/health',check,context,'47.97.226.219:18083')
        result={'format':'future-school-web-mirror-installed','backup_directory':str(backup),'device_id':device['id'],'tls_verified':True,'accounts_preserved':True,'requests_before':pending,'dataset_unchanged':True,'hours_deducted':False,'expires_at':1794391683,'new_files':{n:sha(b) for n,b in new.items()}}
        (backup/'receipt.json').write_text(json.dumps(result),encoding='utf8');return result
    except BaseException:
        if stopped:
            run(['systemctl','--user','stop',GATE]);run(['systemctl','--user','stop',UNIT]);drain()
            for name,b in old.items():replace(work/name,b)
            replace(cfg_path,cfg_raw);replace(drop,old_drop)
            with sqlite3.connect(database) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='web_mirror_devices'").fetchone():db.execute('UPDATE web_mirror_devices SET active=0')
            run(['systemctl','--user','daemon-reload']);run(['systemctl','--user','reset-failed',UNIT]);run(['systemctl','--user','start',UNIT]);wait(18086,'/health/ready',lambda h:h.get('workspace_ui_version')==3)
            run(['systemctl','--user','reset-failed',GATE]);run(['systemctl','--user','start',GATE]);wait(18083,'/viewer/health',lambda h:h.get('backend_ready') is True,host='47.97.226.219:18083')
        raise ValueError('Mirror update failed; original web recovered; inspect receipt before retrying')
