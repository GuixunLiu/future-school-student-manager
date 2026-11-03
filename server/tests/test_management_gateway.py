from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import threading
import unittest

from management import gateway

CERT=Path(__file__).resolve().parents[4]/'outputs/private-web-mirror-2026-10-29'


class ReadinessTests(unittest.TestCase):
    def test_candidate_or_claimed_wechat_backend_is_never_ready_for_publishing(self):
        good=dict(status='ready',mode='management_staged',runtime_admitted=True,wechat_sending_enabled=False,approval_mode='web_approval')
        self.assertTrue(gateway.ready(good));self.assertTrue(gateway.ready(good|dict(mode='management_active')))
        for bad in (dict(mode='management_candidate'),dict(runtime_admitted=False),dict(wechat_sending_enabled=True),dict(approval_mode='request_pending_tablet')):self.assertFalse(gateway.ready(good|bad))


@unittest.skipUnless((CERT/'server.key').is_file(),'Local TLS identity is deliberately outside Git')
class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.requests=[];requests=self.requests
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):self.respond()
            def do_POST(self):self.respond()
            def respond(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                requests.append((self.command,self.path,body,self.headers.get('Authorization')))
                value=dict(status='ready',mode='management_staged',runtime_admitted=True,wechat_sending_enabled=False,approval_mode='web_approval') if self.path=='/health/ready' else dict(forwarded=True)
                raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream);thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(upstream.shutdown(),upstream.server_close(),thread.join(timeout=5)))
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(str(CERT/'server.crt'),str(CERT/'server.key'))
        self.trusted=ssl.create_default_context(cafile=str(CERT/'server.crt'))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.port=port;self.origin='http://127.0.0.1:'+str(port)
        self.viewer=gateway.Viewer(('127.0.0.1',port),self.origin,9999,context,upstream=upstream.server_address,clock=lambda:2000)
        viewer_thread=threading.Thread(target=self.viewer.serve_forever,daemon=True);viewer_thread.start()
        self.addCleanup(lambda:(self.viewer.shutdown(),self.viewer.server_close(),viewer_thread.join(timeout=5)))

    def call(self,path,*,tls=False,method='GET',headers=None,body=None):
        c=HTTPSConnection('127.0.0.1',self.port,context=self.trusted,timeout=5) if tls else HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            c.request(method,path,headers=headers or {},body=body);r=c.getresponse();return r.status,r.read()
        finally:c.close()

    def test_same_port_requires_tls_for_native_and_never_reopens_old_mirror(self):
        auth={'Authorization':'Bearer fictional-device-token'}
        self.assertEqual(self.call('/v1/management/device/plan?generation=fictional',headers=auth)[0],403)
        self.assertEqual(self.call('/v1/management/device/plan?generation=fictional',tls=True,headers=auth)[0],200)
        self.assertEqual(self.call('/v1/tablet-mirror/plan',tls=True,headers=auth)[0],409)
        self.assertEqual(self.call('/v1/management/device/class-next?generation=fictional',headers=auth)[0],403)
        self.assertEqual(self.call('/v1/management/device/class-next?generation=fictional',tls=True,headers=auth)[0],200)
        for route in ('class-ready','class-receipt'):
            self.assertEqual(self.call('/v1/management/device/'+route,method='POST',headers=auth|{'Content-Type':'application/json'},body='{}')[0],403)
            self.assertEqual(self.call('/v1/management/device/'+route,tls=True,method='POST',headers=auth|{'Content-Type':'application/json'},body='{}')[0],200)
        for i in range(55):self.assertEqual(self.call('/viewer/health',tls=bool(i%2))[0],200)
        health=json.loads(self.call('/viewer/health')[1]);self.assertFalse(health['legacy_mirror_writable']);self.assertTrue(health['encrypted_device_transport'])
        # An entire class can upload its new arrivals; device commits are not
        # accidentally subject to the 20-login-attempt limit.
        for i in range(25):self.assertEqual(self.call('/v1/management/device/checkin',tls=True,method='POST',headers=auth|{'Content-Type':'application/json'},body='{}')[0],200)

    def test_strict_host_origin_body_methods_and_route_whitelist(self):
        auth={'Authorization':'Bearer fictional-web-token'}
        for path,options,status in [
            ('/v1/me',dict(headers=auth|{'Host':'evil.invalid'}),403),
            ('/v1/me',dict(headers=auth|{'Origin':'http://evil.invalid'}),403),
            ('/v1/me',{},401),
            ('/v1/students',dict(method='POST',headers=auth|{'Content-Type':'application/json'},body='{}'),404),
            ('/v1/management/requests',dict(method='POST',headers=auth|{'Content-Type':'application/json','Content-Length':'16385'},body=None),413),
            ('/v1/management/requests',dict(method='POST',headers=auth,body='{}'),415),
            ('/v1/management/device/next?generation=x',dict(tls=True,headers=auth|{'Origin':self.origin}),403),
            ('/v1/management/requests',dict(method='POST',headers=auth|{'Content-Type':'application/json'},body='{}'),200),
            ('/v1/management/legacy-requests/'+'a'*32+'/decision',dict(method='POST',headers=auth|{'Content-Type':'application/json'},body='{}'),200),
            ('/v1/management/legacy-requests/not-a-valid-id/decision',dict(method='POST',headers=auth|{'Content-Type':'application/json'},body='{}'),404),
            ('/v1/management/materials/'+'a'*32+'/scores',dict(method='POST',headers=auth|{'Content-Type':'application/json'},body='{}'),200),
        ]:
            self.assertEqual(self.call(path,**options)[0],status,(path,status))
