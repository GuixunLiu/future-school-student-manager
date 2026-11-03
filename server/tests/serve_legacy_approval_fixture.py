"""Independent browser fixture for reviewing preserved old requests."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_legacy_approvals import LegacyApprovalTests,KEY

if __name__=='__main__':
    f=LegacyApprovalTests();f.setUp()
    try:
        with f.store.transaction() as db:
            db.execute('INSERT INTO web_makeup_requests SELECT ?,org_id,lesson_key,student_local_id,reason,status,created_at FROM web_makeup_requests WHERE id=?',('b'*32,KEY))
            db.execute('INSERT INTO web_makeup_requests SELECT ?,org_id,lesson_key,?,reason,status,created_at FROM web_makeup_requests WHERE id=?',('c'*32,'999',KEY))
        app=create_app(f.f.path,lambda:f.f.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18114,access_log=False,log_level='warning'))
        @app.post('/__fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['LEGACY_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_LEGACY_READY',flush=True);server.run()
    finally:f.doCleanups()
