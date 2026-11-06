"""Disposable fictional class handoff workspace, loopback only."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_class_teachers import ClassTeacherTests


if __name__=='__main__':
    f=ClassTeacherTests();f.setUp()
    try:
        f.ready();app=create_app(f.base.path,lambda:f.base.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18111,access_log=False,log_level='warning'))
        @app.post('/__fictional_fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['CLASS_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return dict(stopped=True)
        print('FICTIONAL_CLASSES_READY',flush=True);server.run()
    finally:f.doCleanups()
