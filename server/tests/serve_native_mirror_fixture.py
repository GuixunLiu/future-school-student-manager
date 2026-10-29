"""Loopback TLS receipt fixture, exclusively for the independent synthetic App."""
import hashlib,json,socket,ssl,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

root=Path(sys.argv[1]);token='wxd_'+'A'*43;device='d'*32
received={};attempts=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        if self.path!='/v1/tablet-mirror/plan':return self.send_error(404)
        self.reply({'protocol':1,'enabled':True,'device_id':device,'revision':max(received,default=0)})
    def reply(self,value):
        if self.headers.get('Authorization')!='Bearer '+token:return self.send_error(401)
        b=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_POST(self):
        if self.path!='/v1/tablet-mirror/snapshot':return self.send_error(404)
        raw=self.rfile.read(int(self.headers['Content-Length']));p=json.loads(raw);assert p['device_id']==device
        digest=hashlib.sha256(raw).hexdigest();revision=p['revision'];duplicate=revision in received
        if duplicate:assert received[revision]['source_sha256']==digest
        r={'device_id':device,'revision':revision,'request_ref':p['request_ref'],'source_sha256':digest,'hours_deducted':False,'received_at':int(time.time()),'duplicate':duplicate}
        received[revision]=r;attempts.append({'revision':revision,'sha256':digest,'duplicate':duplicate})
        (root/'native-receipts.json').write_text(json.dumps({'attempts':attempts,'receipts':received}))
        marker=root/'drop-next'
        if marker.exists():marker.unlink();self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        self.reply(r)
server=ThreadingHTTPServer(('127.0.0.1',18443),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(root/'server.crt',root/'server.key');server.socket=ctx.wrap_socket(server.socket,server_side=True)
threading.Thread(target=lambda:(sys.stdin.buffer.read(1),server.shutdown()),daemon=True).start()
print('fictional TLS fixture ready',flush=True);server.serve_forever();server.server_close()
