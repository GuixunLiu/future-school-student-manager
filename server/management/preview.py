"""Disposable fictional preview. Never accepts a database path or public host."""
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import time as clock_time

import uvicorn

from business.store import Store
from .api import create_app
from .deadlines import TZ
from .delivery import DeliveryStore


def main():
    clock = lambda: int(clock_time.time())
    today = datetime.now(TZ).date()
    starts = int(datetime.combine(today, time(9), TZ).timestamp())
    with TemporaryDirectory(prefix='fictional-management-') as folder:
        path = Path(folder) / 'fictional.sqlite'
        Store.bootstrap(path, 'demo', 'demo', '演示负责人', 'demo-management-2026', clock)
        store = DeliveryStore(path, clock)
        store.initialize()
        with store.transaction() as db:
            owner = db.execute('SELECT * FROM users').fetchone()
            org, who = owner['org_id'], owner['id']
            db.execute('INSERT INTO roles VALUES(?,?,?)', (org, who, 'teacher'))
            for role in ('principal', 'assessor'):
                db.execute('INSERT INTO management_roles VALUES(?,?,?)', (org, who, role))
            db.execute('INSERT INTO classes VALUES(?,?,?,?,?)', ('demo-class', org, '演示班级', '演示课程', clock()))
            db.execute('INSERT INTO teacher_classes VALUES(?,?,?)', (org, who, 'demo-class'))
            db.execute('INSERT INTO management_approval_scopes VALUES(?,?,?)', (org, who, 'demo-class'))
            for number in range(1, 4):
                student = 'demo-student-' + str(number)
                db.execute('INSERT INTO students VALUES(?,?,?,?,?,?,?,?,?)',
                           (student, org, 'demo-class', '演示学生' + str(number), 'DEMO00' + str(number), 1000, 1, None, 0))
                db.execute('INSERT INTO management_balances VALUES(?,?,?,?)', (org, student, 1000, 0))
                db.execute('INSERT INTO management_attendance VALUES(?,?,?,?,?,?,?,?,?)',
                           (org, 'demo-lesson', student, 'demo-class', starts, 1, 100, 'missing', 0))
        token = store.login('demo', 'demo', 'demo-management-2026', 'preview')['access_token']
        store.open_period(token, 'admin', kind='weekly_report',
                          starts_on=(today - timedelta(days=today.weekday())).isoformat(),
                          owner_ids=[who], command_ref='preview-period')
        print('Fictional preview: http://127.0.0.1:18097 | institution demo | login demo | password demo-management-2026', flush=True)
        uvicorn.run(create_app(path, clock), host='127.0.0.1', port=18097, access_log=False)


if __name__ == '__main__':
    main()
