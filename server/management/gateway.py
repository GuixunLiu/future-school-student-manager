"""Bounded web HTTP / pinned device TLS entry for an explicitly admitted copy.

Not installed automatically. Reuses the existing temporary host/port/expiry and
TLS identity. Legacy mirror uploads stay fenced after this entry is selected.
"""
import argparse
import hashlib
from http.client import HTTPConnection, HTTPException
import importlib.util
import json
from pathlib import Path
import re
import socket
from socketserver import ThreadingMixIn
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('canonical_base_viewer',ROOT/'ops/temporary-http-viewer.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)


def ready(data):
    return (isinstance(data,dict) and data.get('status')=='ready' and data.get('runtime_admitted')is True
        and data.get('mode') in ('management_staged','management_active') and data.get('wechat_sending_enabled')is False
        and data.get('approval_mode')=='web_approval')


base.backend_ready=ready
GETS={'/','/workspace.js','/workspace.css','/future-school-logo.png','/people','/personnel.js','/v1/management/people','/class-teachers.js','/v1/management/classes','/v1/management/device/class-next','/health/ready','/v1/me','/v1/management/attendance','/v1/management/weekly-reports',
      '/v1/management/requests','/v1/management/legacy-requests','/v1/management/materials','/v1/management/assessment-rules',
      '/v1/management/assessment-teachers','/v1/management/workspace','/v1/management/migration-holds','/v1/management/device/plan','/v1/management/device/activation','/v1/management/device/next'}
POSTS={'/v1/auth/login','/v1/auth/logout','/v1/management/people','/v1/management/requests','/v1/management/assessment-rules',
       '/v1/management/assessment-periods','/v1/management/enrollment-reviews','/v1/management/enrollment-authorizations',
       '/v1/management/enrollment-batches','/v1/management/migration-holds','/v1/management/activations',
       '/v1/management/device/activation-commit','/v1/management/device/receipt','/v1/management/device/checkin','/v1/management/device/class-ready','/v1/management/device/class-receipt','/v1/management/device/history-ready'}
GET_PATTERNS=(r'/v1/management/activations/[a-f0-9]{32}',r'/v1/management/materials/[a-f0-9]{32}')
POST_PATTERNS=(r'/v1/management/requests/[a-f0-9]{32}/decision',r'/v1/management/materials/[a-f0-9]{32}',
               r'/v1/management/legacy-requests/[a-f0-9]{32}/decision',
               r'/v1/management/people/[a-f0-9]{32}',
               r'/v1/management/classes/[a-f0-9]{32}/teacher',
               r'/v1/management/materials/[a-f0-9]{32}/scores',r'/v1/management/enrollment-batches/[a-f0-9]{32}/(?:pages|finalize)',
               r'/v1/management/migration-holds/[a-f0-9]{32}/cancel',r'/v1/management/activations/[a-f0-9]{32}/state')


class Handler(base.Handler):
    def dispatch(self):
        s=self.server
        try:p=urlsplit(self.path)
        except ValueError:return self.reject(400,'invalid_path')
        if self.headers.get_all('Host',[])!=[s.authority]:return self.reject(403,'invalid_host')
        native=p.path.startswith('/v1/management/device/')
        origins=self.headers.get_all('Origin',[])
        if native and (not isinstance(self.connection,ssl.SSLSocket) or origins):return self.reject(403,'encrypted_device_transport_required')
        if origins and origins!=[s.public_origin]:return self.reject(403,'invalid_origin')
        if s.clock()>=s.expires_at:return self.reject(410,'temporary_viewer_expired')
        if p.scheme or p.netloc or p.fragment or '%' in p.path or '\\' in self.path or len(self.path)>4096:return self.reject(400,'invalid_path')
        if p.path.startswith('/v1/tablet-mirror/'):return self.reject(409,'legacy_mirror_fenced')
        get=self.command in {'GET','HEAD'};post=self.command=='POST'
        if p.path=='/viewer/health':
            if not get or p.query:return self.reject(405,'method_rejected')
        elif not ((get and (p.path in GETS or any(re.fullmatch(x,p.path) for x in GET_PATTERNS)))
                  or (post and (p.path in POSTS or any(re.fullmatch(x,p.path) for x in POST_PATTERNS)))):
            return self.reject(404,'route_not_exposed')
        if self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding') or self.headers.get_all('Expect'):return self.reject(400,'ambiguous_body')
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)>1 or lengths and (not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0])>5 or int(lengths[0])>16384):return self.reject(413,'body_limit')
        length=int(lengths[0]) if lengths else 0
        if get and length:return self.reject(400,'unexpected_body')
        if post and (p.query or self.headers.get_content_type()!='application/json' or not length):return self.reject(415,'json_required')
        auth=self.headers.get_all('Authorization',[])
        if len(auth)>1 or auth and (len(auth[0])>128 or not auth[0].startswith('Bearer ')):return self.reject(400,'invalid_authorization')
        if p.path.startswith('/v1/') and p.path!='/v1/auth/login' and not auth:return self.reject(401,'unauthorized')
        if p.path=='/v1/auth/login' and not s.login_allowed(self.client_address[0]):return self.reject(429,'request_rate_limit')
        if not s.ready():return self.reject(503,'management_not_ready')
        if p.path=='/viewer/health':return self.reply(200,json.dumps(dict(status='ready',viewer='canonical_management',expires_at=s.expires_at,gateway_sha256=s.source_sha256,encrypted_device_transport=True,legacy_mirror_writable=False,wechat_enabled=False)).encode(),{'Content-Type':'application/json'})
        body=self.rfile.read(length) if length else None
        if length and len(body)!=length:return self.reject(400,'incomplete_body')
        headers={'Host':s.upstream[0]+':'+str(s.upstream[1])}
        if auth:headers['Authorization']=auth[0]
        if post:headers['Content-Type']='application/json'
        c=HTTPConnection(*s.upstream,timeout=15)
        try:
            c.request('GET' if self.command=='HEAD' else self.command,self.path,body=body,headers=headers)
            r=c.getresponse();payload=r.read(4*1024*1024+1)
            if len(payload)>4*1024*1024:return self.reject(502,'response_limit')
            return self.reply(r.status,payload,{k:v for k,v in r.getheaders() if k.lower() in {'content-type','content-security-policy','www-authenticate','retry-after'}})
        except (OSError,ValueError,HTTPException):return self.reject(502,'upstream_unavailable')
        finally:c.close()


class Viewer(base.Viewer):
    def __init__(self,address,origin,expires_at,context,**kwargs):
        super().__init__(address,origin,expires_at,**kwargs);self.tls_context=context
        self.connection_slots=threading.BoundedSemaphore(24);self.RequestHandlerClass=Handler
        self.source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    def process_request(self,request,address):
        if not self.connection_slots.acquire(False):request.close();return
        try:ThreadingMixIn.process_request(self,request,address)
        except BaseException:self.connection_slots.release();raise
    def process_request_thread(self,request,address):
        channel=request
        try:
            request.settimeout(8)
            if request.recv(1,socket.MSG_PEEK)==b'\x16':channel=self.tls_context.wrap_socket(request,server_side=True)
            channel.settimeout(15);self.finish_request(channel,address)
        except (OSError,ValueError):pass
        finally:self.shutdown_request(channel);self.connection_slots.release()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    from management.initialization import export_helper
    helper=export_helper();helper.private_path(args.config.parent,True);helper.private_path(args.config)
    config=json.loads(args.config.read_bytes())
    if set(config)!={'origin','expires_at','certificate','private_key','tls_sha256','gateway_sha256'}:raise ValueError('Explicit gateway configuration required')
    if config['origin']!='http://47.97.226.219:18083' or config['expires_at']!=1794391683 or not time.time()<config['expires_at']:raise ValueError('Original temporary origin and expiry required')
    if config['tls_sha256'].get('certificate')!='320750ba1113dafa0410bcdbeffd7b04a68f42f64b74744adf01731bad92e8fe':raise ValueError('Original pinned TLS certificate required')
    if config['gateway_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Gateway code changed')
    for key in ('certificate','private_key'):
        file=Path(config[key]);helper.private_path(file.parent,True);helper.private_path(file)
        if config['tls_sha256'].get(key)!=hashlib.sha256(file.read_bytes()).hexdigest():raise ValueError('TLS identity changed')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['certificate'],config['private_key'])
    with Viewer(('0.0.0.0',18083),config['origin'],config['expires_at'],context,upstream=('127.0.0.1',18098)) as server:server.serve_forever(poll_interval=.3)


if __name__=='__main__':main()
