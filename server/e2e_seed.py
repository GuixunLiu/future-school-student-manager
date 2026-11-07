# 端到端联调用种子脚本（虚构资料，仅本机回环测试）
# 用法：.venv/Scripts/python e2e_seed.py <db_path>   （已存在则只补数据）
import json
import sys
import urllib.request
from pathlib import Path

DB = Path(sys.argv[1])
BASE = 'http://127.0.0.1:18081'
ORG, LOGIN, PASSWORD = 'school-test', 'owner-test', 'e2e-test-password-12345'

if not DB.exists():
    DB.parent.mkdir(parents=True, exist_ok=True)
    from business.store import Store
    Store.bootstrap(DB, ORG, LOGIN, 'E2E Owner', PASSWORD)
    print('bootstrapped', DB)


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Accept', 'application/json')
    if body is not None:
        req.add_header('Content-Type', 'application/json')
        data = json.dumps(body).encode()
    else:
        data = None
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, data) as res:
        return json.loads(res.read() or b'null')


tok = call('POST', '/v1/auth/login', {'organization': ORG, 'login': LOGIN, 'password': PASSWORD})['access_token']
print('login ok')

teacher = call('POST', '/v1/admin/people', {
    'login': 'teacher-e2e', 'display_name': '演示老师',
    'password': 'teacher-password-12345', 'roles': ['teacher']}, tok)
print('teacher', teacher)

cls = call('POST', '/v1/admin/classes', {'name': 'E2E演示班', 'course_name': '演示课程'}, tok)
print('class', cls)

call('PUT', '/v1/admin/classes/' + cls['id'] + '/teachers', {'teacher_ids': [teacher['id']]}, tok)

students = []
for i, name in enumerate(['林小满', '陈星宇', '王雨桐']):
    s = call('POST', '/v1/admin/students', {
        'class_id': cls['id'], 'name': name,
        'external_ref': 'E2E%04d' % (i + 1), 'remaining_units': 2000}, tok)
    students.append(s)
    print('student', s)

device = call('POST', '/v1/admin/devices', {'name': 'E2E测试平板', 'class_ids': [cls['id']]}, tok)
print('device', device)

from datetime import datetime, timezone, timedelta
TZ = timezone(timedelta(hours=8))
today = datetime.now(TZ)
starts = today.replace(hour=9, minute=0, second=0, microsecond=0)
ends = today.replace(hour=10, minute=30, second=0, microsecond=0)
lesson = call('POST', '/v1/admin/lessons', {
    'class_id': cls['id'], 'teacher_id': teacher['id'],
    'starts_at': starts.isoformat(), 'ends_at': ends.isoformat()}, tok)
print('lesson', lesson)

# 一节已结束的课（今天 07:00-08:00）+ 两笔设备签到，供周报出数
past = call('POST', '/v1/admin/lessons', {
    'class_id': cls['id'], 'teacher_id': teacher['id'],
    'starts_at': today.replace(hour=7, minute=0, second=0, microsecond=0).isoformat(),
    'ends_at': today.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()}, tok)
print('past lesson', past)

dtoken = device['device_token']
for i, s in enumerate(students[:2]):
    r = call('POST', '/v1/device/attendance', {
        'event_ref': 'seed-att-%d-%s' % (i, s['id'][:8]),
        'lesson_id': past['id'], 'student_id': s['id'],
        'occurred_at': today.replace(hour=7, minute=5 + i, second=0, microsecond=0).isoformat(),
        'consumed_units': 100, 'remaining_units': 1900,
        'student_revision': 1, 'source': 'live'}, token=dtoken)
    print('seed checkin', r)

summary = {'token': tok, 'device_token': device['device_token'], 'lesson_id': lesson['id'],
           'students': students}
Path('D:/projects/web/e2e-seed.json').write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
