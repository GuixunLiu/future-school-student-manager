"""Disposable browser fixture; cannot accept a database path or read production data."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
import test_scoped_pilot as fixture


def main():
    p=argparse.ArgumentParser();p.add_argument('--fictional',action='store_true',required=True);p.parse_args()
    f=fixture.ScopedPilotTests();f.setUp()
    try:
        db=sqlite3.connect(f.f.source)
        db.execute('UPDATE students SET name=? WHERE id=?',('虚构 <img src=x onerror=alert(1)> 同名',fixture.fixture.STUDENT))
        for n in range(15):
            sid=format(200+n,'032x');source=2000+n;ref='BROWSER'+str(n+1).zfill(3)
            db.execute('INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units) VALUES(?,?,?,?,?,?)',(sid,fixture.fixture.ORG,fixture.fixture.CLASS,'虚构同名学生'+str(n+1),ref,1000))
            db.execute('INSERT INTO roster_source_links VALUES(?,?,?,?,?)',(fixture.fixture.ORG,'student',source,sid,'a'*64))
            f.f.plan['members'].append({'source_local_id':source,'external_ref':ref,'server_student_id':sid,'expected_remaining_units':1000})
        db.commit();db.close();f.f.plan['expected_students']=16
        output=f.f.root/'browser-stage';plan_hash=hashlib.sha256(json.dumps(f.f.plan).encode()).hexdigest()
        receipt=fixture.fixture.stage.prepare(f.f.source,output,f.f.plan,plan_hash,now=fixture.fixture.NOW)
        database=f.f.root/'browser-working.sqlite'
        scope=fixture.runtime.prepare_database(output/'pilot-offline.sqlite',database,f.f.plan,receipt,fixture.fixture.stage,'fictional-teacher',clock=lambda:fixture.fixture.NOW)
        api=fixture.runtime.build_app(database,f.f.plan,scope,fixture.fixture.stage,clock=lambda:fixture.fixture.NOW)
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16)
        server=uvicorn.Server(uvicorn.Config(api,host='127.0.0.1',port=sock.getsockname()[1],access_log=False,log_level='critical',ws='none',timeout_graceful_shutdown=5))
        def stop():sys.stdin.buffer.read(1);server.should_exit=True
        threading.Thread(target=stop,daemon=True).start()
        print(json.dumps({'fixture_url':'http://127.0.0.1:'+str(sock.getsockname()[1]),'fictional':True}),flush=True)
        server.run(sockets=[sock])
    finally:f.doCleanups()


if __name__=='__main__':main()
