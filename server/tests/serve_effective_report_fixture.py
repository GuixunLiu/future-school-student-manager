"""Disposable historical approval browser fixture; never real records."""
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_effective_weekly_reports import EffectiveWeeklyTests

if __name__=='__main__':
    f=EffectiveWeeklyTests();f.setUp();lesson=f.temporary();f.confirm(lesson);r=f.h.submit();f.h.decide(r['id'])
    try:
        app=create_app(f.f.path,lambda:f.f.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18115,access_log=False,log_level='warning'))
        @app.post('/__fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=os.environ['REPORT_FIXTURE_STOP']:return JSONResponse({},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_REPORT_READY',flush=True);server.run()
    finally:f.doCleanups()
