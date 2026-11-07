import unittest

from fastapi.testclient import TestClient

from management.api import create_app
from management.delivery import DeliveryStore
from test_management_rules import ApprovalTests


class ManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ApprovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        DeliveryStore(self.fixture.path, lambda: self.fixture.now).initialize()
        self.app = create_app(self.fixture.path, lambda: self.fixture.now, allow_test_client=True)
        self.client = TestClient(self.app, base_url='http://127.0.0.1:18087')
        self.addCleanup(self.client.close)

    def headers(self, who):
        return {'Authorization': 'Bearer ' + self.fixture.tokens[who]}

    def submit(self):
        r = self.client.post('/v1/management/requests', headers=self.headers('teacher'), json={
            'workspace_role': 'teacher', 'kind': 'makeup', 'lesson_id': 'lesson',
            'student_id': 'student', 'reason': '虚构核实', 'request_ref': 'api-ref', 'expected_revision': 0,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()['id']

    def test_extended_roles_login_and_identity(self):
        for who in ('assessor', 'principal'):
            r = self.client.post('/v1/auth/login', json={'organization': 'fictional', 'login': who, 'password': self.fixture.password})
            self.assertEqual(r.status_code, 200, r.text)
            me = self.client.get('/v1/me', headers={'Authorization': 'Bearer ' + r.json()['access_token']})
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()['roles'], [who])
        ws = self.client.get('/v1/management/workspace?workspace_role=assessor', headers=self.headers('assessor'))
        self.assertTrue(ws.json()['can_score'])
        self.assertFalse(ws.json()['can_manage'])
        self.assertEqual(ws.json()['approval_class_ids'], ['class-a'])

    def test_anonymous_forged_role_extra_fields_and_remote_denied(self):
        self.assertEqual(self.client.get('/v1/management/requests?workspace_role=admin').status_code, 401)
        self.assertEqual(self.client.get('/v1/management/requests?workspace_role=admin', headers=self.headers('teacher')).status_code, 403)
        self.assertEqual(self.client.post('/v1/management/requests', headers=self.headers('teacher'), json={'roles': ['admin']}).status_code, 422)
        with TestClient(self.app, base_url='http://public.invalid') as c:
            self.assertEqual(c.get('/v1/me', headers=self.headers('owner')).status_code, 403)

    def test_scoped_lists_and_decision_uses_authenticated_actor(self):
        key = self.submit()
        for who, expected in (('teacher', 1), ('other-teacher', 0), ('assessor', 1), ('owner', 1)):
            role = 'admin' if who == 'owner' else 'teacher' if who == 'other-teacher' else who
            r = self.client.get('/v1/management/requests?workspace_role=' + role, headers=self.headers(who))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(len(r.json()['items']), expected)
        body = {'workspace_role': 'teacher', 'approve': True, 'reason': '虚构审批', 'decision_ref': 'api-decision'}
        path = '/v1/management/requests/' + key + '/decision'
        self.assertEqual(self.client.post(path, headers=self.headers('teacher'), json=body).status_code, 403)
        body['workspace_role'] = 'assessor'
        approved = self.client.post(path, headers=self.headers('assessor'), json=body)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()['effective'])
        self.assertEqual(approved.json()['tablet_delivery'], 'pending')
        self.assertEqual(self.fixture.state(), (('arrived', 1, 1), (850, 1), 1))

    def test_explicit_candidate_health_and_missing_workspace(self):
        data = self.client.get('/health/ready').json()
        self.assertEqual(data['mode'], 'management_candidate')
        self.assertFalse(data['production_ready'])
        self.assertFalse(data['tablet_delivery_enabled'])
        self.assertEqual(self.client.get('/v1/management/requests', headers=self.headers('owner')).status_code, 422)

    def test_canonical_attendance_scope_and_request_status(self):
        path = '/v1/management/attendance?on=1970-01-01&workspace_role='
        for who, role, count in (('teacher', 'teacher', 1), ('other-teacher', 'teacher', 0), ('owner', 'admin', 1), ('principal', 'principal', 1)):
            r = self.client.get(path + role, headers=self.headers(who))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(len(r.json()['items']), count)
        self.assertEqual(self.client.get(path + 'assessor', headers=self.headers('assessor')).status_code, 403)
        self.submit()
        row = self.client.get(path + 'teacher', headers=self.headers('teacher')).json()['items'][0]
        self.assertEqual(row['balance_units'], 1000)
        self.assertEqual(row['student_name'], '虚构学生')
        self.assertTrue(row['has_pending_request'])
        self.assertEqual(self.client.get(path.replace('1970-01-01', '1970-02-30') + 'admin', headers=self.headers('owner')).status_code, 422)

    def test_candidate_page_assets_and_strict_csp(self):
        for path, content_type in (('/', 'text/html'), ('/workspace.js', 'application/javascript'), ('/workspace.css', 'text/css')):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertIn(content_type, r.headers['content-type'])
            self.assertIn("script-src 'self'", r.headers['content-security-policy'])
            self.assertNotIn('unsafe-inline', r.headers['content-security-policy'])
        self.assertEqual(self.client.get('/v1/management/assessment-rules?workspace_role=admin', headers=self.headers('owner')).status_code, 200)
        self.assertEqual(self.client.get('/v1/management/assessment-teachers?workspace_role=teacher', headers=self.headers('teacher')).status_code, 403)


if __name__ == '__main__':
    unittest.main()
