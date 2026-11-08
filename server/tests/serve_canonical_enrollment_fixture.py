"""Loopback pinned TLS fixture; fictional token, no student storage."""
import json,ssl,sys,threading,socket
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
root,tls=map(Path,sys.argv[1:3])
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def answer(self,value):
        raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        plan=json.loads((root/'public-plan.json').read_text())
        if self.headers.get('Authorization')!='Bearer wxd_'+'A'*43:return self.send_error(401)
        if (root/'revoke').exists():return self.send_error(403)
        if self.path=='/v1/management/device/activation?generation='+plan['generation']+'&profile_id='+plan['profile_id']:
            if (root/'pause').exists():return self.send_error(409)
            activation=json.loads((root/'activation-plan.json').read_text())
            if (root/'activation-active').exists():activation['phase']='active'
            return self.answer(activation)
        if self.path=='/v1/management/device/next?generation='+plan['generation']:
            (root/'next-requested').write_text('1')
            return self.answer(dict(org_id=plan['org_id'],device_id=plan['device_id'],generation=plan['generation'],item=None))
        if self.path!='/v1/management/device/plan?generation='+plan['generation']:return self.send_error(404)
        if (root/'wrong-baseline').exists():plan['baseline_sha256']='0'*64
        raw=json.dumps(plan).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        if self.headers.get('Authorization')!='Bearer wxd_'+'A'*43:return self.send_error(401)
        if self.path!='/v1/management/device/activation-commit':return self.send_error(404)
        if (root/'pause').exists() or (root/'revoke').exists():return self.send_error(409)
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<4096:return self.send_error(413)
        body=json.loads(self.rfile.read(size));plan=json.loads((root/'activation-plan.json').read_text())
        expected={k:plan[k] for k in ('generation','activation_id','profile_id','nonce','bootstrap_sha256','source_map_sha256')}
        if body!=expected:return self.send_error(409)
        duplicate=(root/'activation-active').exists();(root/'activation-active').write_text('1')
        if (root/'commit-lost').exists():
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        result={k:plan[k] for k in ('activation_id','org_id','device_id','generation','profile_id','bootstrap_sha256','source_map_sha256','active_until')}
        self.answer(result|dict(protocol=1,phase='active',old_mirror_writable=False,balances_changed=False,duplicate=duplicate))
server=ThreadingHTTPServer(('127.0.0.1',18445),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(tls/'server.crt',tls/'server.key');server.socket=context.wrap_socket(server.socket,server_side=True)
threading.Thread(target=lambda:(sys.stdin.buffer.read(1),server.shutdown()),daemon=True).start()
print('fictional canonical TLS fixture ready',flush=True)
server.serve_forever();server.server_close()
