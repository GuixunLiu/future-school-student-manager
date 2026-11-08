"""Disposable browser fixture for classroom navigation. No real data."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_management_classroom import ClassroomTests


def main():
    fixture=ClassroomTests();fixture.setUp()
    try:
        app=create_app(fixture.f.path,lambda:fixture.f.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18109,access_log=False,log_level='warning'))
        @app.post('/__fictional_fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['CLASSROOM_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_CLASSROOM_READY',flush=True);server.run()
    finally:fixture.doCleanups()


if __name__=='__main__':main()
