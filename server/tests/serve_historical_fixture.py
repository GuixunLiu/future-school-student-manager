"""Disposable historical approval browser fixture; never real records."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_historical_requests import HistoricalTests

if __name__=='__main__':
    f=HistoricalTests();f.setUp()
    try:
        app=create_app(f.base.path,lambda:f.base.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18113,access_log=False,log_level='warning'))
        @app.post('/__fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['HISTORY_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_HISTORY_READY',flush=True);server.run()
    finally:f.doCleanups()
