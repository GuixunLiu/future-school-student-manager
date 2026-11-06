"""Disposable loopback fixture only, with fictional accounts and class names."""
import json
import os
import uvicorn
from fastapi import Header
from fastapi.responses import JSONResponse
from management.api import create_app
from test_management_reports import ReportTests


def main():
    token=os.environ['REPORT_FIXTURE_STOP']
    fixture=ReportTests();fixture.setUp()
    try:
        with fixture.store.transaction() as db:
            data=json.loads(db.execute('SELECT data_json FROM web_dataset').fetchone()[0])
            data['weeks'][0]['classes'][0]['name']='<img src=x onerror=window.fixtureXss=1>'
            db.execute('UPDATE web_dataset SET data_json=?',(json.dumps(data),))
        app=create_app(fixture.f.path,lambda:fixture.f.now)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18099,access_log=False,log_level='warning'))
        @app.post('/__fictional_fixture_stop')
        def stop(x_fixture_stop:str=Header(default='')):
            if x_fixture_stop!=token:return JSONResponse({'error':'fictional fixture'},status_code=403)
            server.should_exit=True;return {'stopped':True}
        print('FICTIONAL_REPORT_FIXTURE_READY',flush=True);server.run()
    finally:fixture.doCleanups()


if __name__=='__main__':main()
