"""Loopback pinned TLS fixture; fictional token, no student storage."""
import json,ssl,sys,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
root,tls=map(Path,sys.argv[1:3])
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        plan=json.loads((root/'public-plan.json').read_text())
        if self.headers.get('Authorization')!='Bearer wxd_'+'A'*43:return self.send_error(401)
        if self.path!='/v1/management/device/plan?generation='+plan['generation']:return self.send_error(404)
        if (root/'revoke').exists():return self.send_error(403)
        if (root/'wrong-baseline').exists():plan['baseline_sha256']='0'*64
        raw=json.dumps(plan).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',18445),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(tls/'server.crt',tls/'server.key');server.socket=context.wrap_socket(server.socket,server_side=True)
threading.Thread(target=lambda:(sys.stdin.buffer.read(1),server.shutdown()),daemon=True).start()
print('fictional canonical TLS fixture ready',flush=True)
server.serve_forever();server.server_close()
