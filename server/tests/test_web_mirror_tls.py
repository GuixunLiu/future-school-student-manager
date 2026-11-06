import hashlib
from http.client import HTTPConnection,HTTPSConnection
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import ssl
import threading
import unittest
from unittest.mock import patch
from test_web_mirror import WebMirrorTests,TOKEN

ROOT=Path(__file__).resolve().parents[1]
CERT=ROOT.parents[2]/'outputs/private-web-mirror-2026-10-29'


@unittest.skipUnless((CERT/'server.key').is_file(),'Local private TLS identity is not part of Git')
class WebMirrorTLSTests(unittest.TestCase):
    def test_same_port_keeps_http_and_only_accepts_native_snapshots_over_tls(self):
        f=WebMirrorTests();f.setUp();self.addCleanup(f.doCleanups)
        work=f.f.f.f.root/'mixed-gateway';(work/'teacher_web').mkdir(parents=True)
        shutil.copyfile(ROOT/'ops/temporary-http-viewer.py',work/'base-viewer.py')
        for n in ['gateway.py','mirror_gateway.py']:shutil.copyfile(ROOT/'teacher_web'/n,work/'teacher_web'/n)
        spec=importlib.util.spec_from_file_location('mixed_test_gateway',work/'teacher_web/mirror_gateway.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(str(CERT/'server.crt'),str(CERT/'server.key'))
        trusted=ssl.create_default_context(cafile=str(CERT/'server.crt'))
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        viewer=module.Viewer(('127.0.0.1',port),'http://127.0.0.1:'+str(port),4102444800,str(f.db),context,clock=lambda:f.f.now)
        # Normal web forwarding stays on the existing backend. This test isolates TLS routing.
        viewer.ready=lambda:True
        thread=threading.Thread(target=viewer.serve_forever,daemon=True);thread.start()
        try:
            # More than both old limits catches slot leakage from inherited dispatch.
            for index in range(50):
                c=(HTTPSConnection('127.0.0.1',port,context=trusted,timeout=8) if index%2 else HTTPConnection('127.0.0.1',port,timeout=8));c.request('GET','/viewer/health');r=c.getresponse();r.read();self.assertEqual(r.status,200);c.close()
            c=HTTPConnection('127.0.0.1',port,timeout=8);c.request('GET','/viewer/health');r=c.getresponse();self.assertEqual(r.status,200);self.assertTrue(json.loads(r.read())['mirror_tls_enabled']);c.close()
            c=HTTPConnection('127.0.0.1',port,timeout=8);c.request('GET','/v1/tablet-mirror/plan',headers={'Authorization':'Bearer '+TOKEN});r=c.getresponse();r.read();self.assertEqual(r.status,403);c.close()
            c=HTTPSConnection('127.0.0.1',port,context=trusted,timeout=8);c.request('GET','/v1/tablet-mirror/plan',headers={'Authorization':'Bearer '+TOKEN});r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['revision'],0);c.close()
            raw=f.payload();c=HTTPSConnection('127.0.0.1',port,context=trusted,timeout=8);c.request('POST','/v1/tablet-mirror/snapshot',body=raw,headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'});r=c.getresponse();body=r.read();self.assertEqual(r.status,200,body);self.assertFalse(json.loads(body)['hours_deducted']);c.close()
            c=HTTPSConnection('127.0.0.1',port,context=trusted,timeout=8);c.request('POST','/v1/tablet-mirror/snapshot',body=raw,headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'});r=c.getresponse();self.assertTrue(json.loads(r.read())['duplicate']);c.close()
        finally:viewer.shutdown();viewer.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
