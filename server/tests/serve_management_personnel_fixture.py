"""Disposable personnel UI with explicitly migrated fictional database."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from management.personnel_api import install_personnel_routes
from test_management_personnel import PersonnelTests


def main():
    f=PersonnelTests();f.setUp()
    try:
        app=create_app(f.f.path,lambda:f.f.now);install_personnel_routes(app,f.f.path,lambda:f.f.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18110,access_log=False,log_level='warning'))
        @app.post('/__fictional_fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['PERSONNEL_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_PERSONNEL_READY',flush=True);server.run()
    finally:f.doCleanups()


if __name__=='__main__':main()
