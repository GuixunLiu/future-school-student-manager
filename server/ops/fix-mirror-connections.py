"""Fix the bounded gateway worker only; retain device, TLS identity and database."""
import base64,hashlib,json,os,socket,subprocess,time
from pathlib import Path
from http.client import HTTPConnection,HTTPSConnection
import ssl
def install(plan,payload):
    work=Path(plan['work_directory']);name='teacher_web/mirror_gateway.py';cfg_path=work/'web.private.json';cfg_raw=cfg_path.read_bytes();cfg=json.loads(cfg_raw);old=(work/name).read_bytes()
    sha=lambda b:hashlib.sha256(b).hexdigest()
    if str(work)!='/opt/wxnotify/app/teacher-web-20261029101834-42da0325' or set(payload['files'])!={name} or sha(old)!='d99480a91b6225be52f1cc36b67b81cf8798305d8a93f374c01af9f61e3653d2' or cfg['files'][name]!=sha(old):raise ValueError('Current gateway differs; inspect before repeating')
    for n,h in cfg['files'].items():
        if (work/n).is_symlink() or sha((work/n).read_bytes())!=h:raise ValueError('Current source changed')
    entry=payload['files'][name];new=base64.b64decode(entry['base64'],validate=True)
    if sha(new)!=entry['sha256'] or len(new)>20000:raise ValueError('Patch checksum differs')
    compile(new,name,'exec');backup=work/('mirror-connection-fix-'+os.urandom(8).hex());backup.mkdir(mode=0o700);(backup/'gateway.py').write_bytes(old);(backup/'web.private.json').write_bytes(cfg_raw)
    def run(action):
        r=subprocess.run(['systemctl','--user',action,'wxnotify-http-viewer.service'],capture_output=True,timeout=35)
        if r.returncode:raise ValueError('Gateway service operation failed')
    def replace(p,b):
        t=p.with_name(p.name+'.connections-new')
        with open(t,'xb') as f:f.write(b)
        t.chmod(0o600);os.replace(t,p)
    def stop():
        run('stop')
        for _ in range(180):
            with socket.socket() as s:
                try:s.bind(('0.0.0.0',18083));return
                except OSError:pass
            time.sleep(.5)
        raise ValueError('Gateway port still busy')
    def health(context=None):
        c=HTTPSConnection('127.0.0.1',18083,context=context,timeout=5) if context else HTTPConnection('127.0.0.1',18083,timeout=5)
        try:
            c.request('GET','/viewer/health',headers={'Host':'47.97.226.219:18083'});r=c.getresponse();b=r.read(32768)
            if r.status!=200:raise ValueError('Gateway readiness failed')
            return json.loads(b)
        finally:c.close()
    try:
        stop();replace(work/name,new);cfg['files'][name]=sha(new);replace(cfg_path,json.dumps(cfg).encode());run('reset-failed');run('start')
        for _ in range(40):
            try:
                if health().get('gateway_sha256')==sha(new):break
            except (OSError,ValueError):pass
            time.sleep(.5)
        else:raise ValueError('Gateway startup failed')
        context=ssl.create_default_context(cafile=str(work/'mirror-tls/server.crt'))
        for i in range(60):
            if health(context if i%2 else None).get('gateway_sha256')!=sha(new):raise ValueError('Repeated connection check failed')
        result={'format':'future-school-mirror-connections-fixed','gateway_sha256':sha(new),'backup_directory':str(backup),'consecutive_connections':60,'tls_verified':True,'database_written':False,'device_credentials_changed':False,'expiry_changed':False};(backup/'receipt.json').write_text(json.dumps(result));return result
    except BaseException:
        stop();replace(work/name,old);replace(cfg_path,cfg_raw);run('reset-failed');run('start')
        raise ValueError('Connection fix failed; original source restored; inspect before retrying')
