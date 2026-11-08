"""Same-port HTTP web / pinned TLS tablet transport; no plaintext tablet upload."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import sys
import threading
import time
from socketserver import ThreadingMixIn

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('web_gateway',ROOT/'teacher_web/gateway.py');web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
from teacher_web.mirror_store import authenticate,connect,receive,MirrorError
from contextlib import closing
from urllib.parse import urlsplit


class Handler(web.Handler):
    def dispatch(self):
        try:route=urlsplit(self.path)
        except ValueError:return self.reject(400,'invalid_path')
        if route.path=='/viewer/health':
            if self.headers.get_all('Host',[])!=[self.server.authority] or self.command not in {'GET','HEAD'} or route.query or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Length',[]) not in ([],['0']):return self.reject(400,'invalid_health_request')
            if self.server.clock()>=self.server.expires_at:return self.reject(410,'temporary_viewer_expired')
            if not self.server.ready():return self.reject(503,'backend_not_ready')
            return self.reply(200,json.dumps({'status':'ready','viewer':'teacher_web','gateway_sha256':self.server.source_sha256,'backend_ready':True,'expires_at':self.server.expires_at,'mirror_tls_enabled':True,'makeup_mode':'request_pending_tablet','wechat_enabled':False}).encode(),{'Content-Type':'application/json'})
        if not route.path.startswith('/v1/tablet-mirror/'):return super().dispatch()
        if not isinstance(self.connection,ssl.SSLSocket):return self.reject(403,'encrypted_tablet_transport_required')
        if self.headers.get_all('Host',[])!=[self.server.authority] or self.headers.get_all('Origin') or route.scheme or route.netloc or route.query or route.fragment or '%' in route.path or '\\' in self.path:return self.reject(403,'tablet_origin_rejected')
        if self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding') or self.headers.get_all('Expect'):return self.reject(400,'ambiguous_body')
        auth=self.headers.get_all('Authorization',[])
        if len(auth)!=1 or not auth[0].startswith('Bearer ') or len(auth[0])>80:return self.reject(401,'device_required')
        token=auth[0][7:];now=int(self.server.clock())
        try:
            with closing(connect(self.server.database)) as db:device=dict(authenticate(db,token,now))
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)>1 or lengths and (not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0])>7):return self.reject(400,'invalid_length')
            length=int(lengths[0]) if lengths else 0
            if route.path=='/v1/tablet-mirror/plan':
                if self.command!='GET' or length:return self.reject(405,'method_rejected')
                result={'protocol':1,'mode':'web_mirror','device_id':device['id'],'revision':device['revision'],'enabled':True,'server_time':now,'expires_at':device['expires_at'],'hours_deducted':False}
            elif route.path=='/v1/tablet-mirror/snapshot':
                if self.command!='POST':return self.reject(405,'method_rejected')
                if self.headers.get_content_type()!='application/json' or not 0<length<=4*1024*1024:return self.reject(413,'snapshot_limit')
                if not self.server.snapshot_slot.acquire(False):return self.reject(429,'another_snapshot_in_progress')
                try:
                    body=self.rfile.read(length)
                    if len(body)!=length:return self.reject(400,'incomplete_body')
                    result=receive(self.server.database,token,body,now)
                finally:self.server.snapshot_slot.release()
            else:return self.reject(404,'not_found')
            return self.reply(200,json.dumps(result,separators=(',',':')).encode(),{'Content-Type':'application/json'})
        except MirrorError as error:return self.reject(error.status,error.code)
        except (OSError,ValueError):return self.reject(503,'tablet_temporarily_unavailable')


class Viewer(web.Viewer):
    def __init__(self,address,origin,expires_at,database,context,**kwargs):
        super().__init__(address,origin,expires_at,**kwargs);self.database=database;self.tls_context=context;self.snapshot_slot=threading.BoundedSemaphore(1);self.connection_slots=threading.BoundedSemaphore(24);self.RequestHandlerClass=Handler;self.source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    def process_request(self,request,address):
        if not self.connection_slots.acquire(False):request.close();return
        # Own the bounded slot for this override. The inherited viewer also owns a
        # slot, but its release lives in the process_request_thread we replace.
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
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);p.add_argument('--expires-at',type=int,required=True);p.add_argument('--temporary-http-confirmed',action='store_true',required=True);args=p.parse_args()
    pins=json.loads((ROOT/'web.private.json').read_bytes())
    for name,digest in pins['files'].items():
        target=ROOT/name
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError('Pinned mirror gateway source changed')
    cfg=json.loads((ROOT/'mirror.private.json').read_bytes())
    for name in ['server.crt','server.key']:
        p=ROOT/'mirror-tls'/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=cfg['tls_files'][name]:raise ValueError('TLS identity changed')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(str(ROOT/'mirror-tls/server.crt'),str(ROOT/'mirror-tls/server.key'))
    with Viewer(('0.0.0.0',18083),args.origin,args.expires_at,str(ROOT/'business.sqlite'),context) as server:server.serve_forever(poll_interval=.3)


if __name__=='__main__':main()
