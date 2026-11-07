"""Reuse the existing bounded HTTP proxy; add only the owner-authorized makeup request route."""
import argparse
import hashlib
from http.client import HTTPConnection, HTTPException
import importlib.util
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('original_viewer',ROOT/'base-viewer.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)


def ready(data):
    return isinstance(data,dict) and data.get('status')=='ready' and data.get('mode')=='teacher_web' and data.get('makeup_mode')=='request_pending_tablet' and all(data.get(k) is False for k in ['data_realtime','student_sync_enabled','wechat_sending_enabled','parent_binding_enabled','business_ready'])
base.backend_ready=ready


class Handler(base.Handler):
    def dispatch(self):
        try:route=urlsplit(self.path)
        except ValueError:return self.reject(400,'invalid_path')
        special=(self.command=='POST' and route.path=='/v1/web/makeup-requests') or route.path=='/viewer/health'
        if not special:return super().dispatch()
        server=self.server;hosts=self.headers.get_all('Host',[]);origins=self.headers.get_all('Origin',[])
        if hosts!=[server.authority] or origins and origins!=[server.public_origin]:return self.reject(403,'portal_origin_rejected')
        if server.clock()>=server.expires_at:return self.reject(410,'temporary_viewer_expired')
        if route.scheme or route.netloc or route.fragment or route.query or '\\' in self.path:return self.reject(400,'invalid_path')
        if self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding') or self.headers.get_all('Expect'):return self.reject(400,'ambiguous_body')
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)>1 or lengths and (not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0])>5 or int(lengths[0])>4096):return self.reject(413,'body_limit')
        length=int(lengths[0]) if lengths else 0
        if route.path=='/viewer/health':
            if self.command not in {'GET','HEAD'} or length:return self.reject(405,'method_rejected')
            if not server.ready():return self.reject(503,'backend_not_ready')
            return self.reply(200,json.dumps({'status':'ready','viewer':'teacher_web','gateway_sha256':server.source_sha256,'backend_ready':True,'expires_at':server.expires_at,'data_realtime':False,'makeup_mode':'request_pending_tablet','wechat_enabled':False}).encode(),{'Content-Type':'application/json'})
        if self.headers.get_content_type()!='application/json' or not length:return self.reject(415,'json_required')
        auth=self.headers.get_all('Authorization',[])
        if len(auth)!=1 or len(auth[0])>128 or not auth[0].startswith('Bearer '):return self.reject(401,'unauthorized')
        if not server.login_allowed(self.client_address[0]):return self.reject(429,'request_rate_limit')
        if not server.ready():return self.reject(503,'backend_not_ready')
        body=self.rfile.read(length)
        if len(body)!=length:return self.reject(400,'incomplete_body')
        conn=HTTPConnection(*server.upstream,timeout=10)
        try:
            conn.request('POST',route.path,body=body,headers={'Host':server.upstream[0]+':'+str(server.upstream[1]),'Authorization':auth[0],'Content-Type':'application/json'})
            r=conn.getresponse();payload=r.read(65537)
            if len(payload)>65536:return self.reject(502,'response_limit')
            self.reply(r.status,payload,{'Content-Type':'application/json'})
        except (OSError,HTTPException):self.reject(502,'upstream_unavailable')
        finally:conn.close()


class Viewer(base.Viewer):
    def __init__(self,address,public_origin,expires_at,*,upstream=('127.0.0.1',18086),clock=time.time):
        super().__init__(address,public_origin,expires_at,upstream=upstream,clock=clock)
        self.RequestHandlerClass=Handler
        self.source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);p.add_argument('--expires-at',type=int,required=True);p.add_argument('--temporary-http-confirmed',action='store_true',required=True);a=p.parse_args()
    if urlsplit(a.origin).port!=18083 or not time.time()<a.expires_at:raise SystemExit('Existing temporary origin and expiry required')
    with Viewer(('0.0.0.0',18083),a.origin,a.expires_at) as server:
        if not server.ready():raise SystemExit('Teacher web backend not ready')
        server.serve_forever(poll_interval=.5)
