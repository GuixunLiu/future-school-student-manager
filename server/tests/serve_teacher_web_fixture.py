import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from test_teacher_web import TeacherWebTests


def main():
    if sys.argv[1:]!=['--fictional']:raise SystemExit('Fictional fixture only')
    f=TeacherWebTests();f.setUp()
    try:
        work=f.f.f.root/'gateway';(work/'teacher_web').mkdir(parents=True)
        root=Path(__file__).resolve().parents[1]
        shutil.copyfile(root/'ops/temporary-http-viewer.py',work/'base-viewer.py')
        shutil.copyfile(root/'teacher_web/gateway.py',work/'teacher_web/gateway.py')
        spec=importlib.util.spec_from_file_location('fixture_gateway',work/'teacher_web/gateway.py');gateway=importlib.util.module_from_spec(spec);spec.loader.exec_module(gateway)
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16);backend=sock.getsockname()[1]
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        url='http://127.0.0.1:'+str(port)
        proxy=gateway.Viewer(('127.0.0.1',port),url,4102444800,upstream=('127.0.0.1',backend))
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        server=uvicorn.Server(uvicorn.Config(f.app,access_log=False,log_level='critical',ws='none',timeout_graceful_shutdown=5))
        def stop():sys.stdin.buffer.read(1);proxy.shutdown();proxy.server_close();server.should_exit=True
        threading.Thread(target=stop,daemon=True).start()
        print(json.dumps({'url':url,'fictional':True}),flush=True);server.run(sockets=[sock])
    finally:f.doCleanups()


if __name__=='__main__':main()
