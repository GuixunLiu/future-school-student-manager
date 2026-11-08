"""JS durable offline capture -> actual Python ledger -> JS durable ack."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_management_delivery as fixture


class QueueRoundtripTests(unittest.TestCase):
    setUp = fixture.DeliveryTests.setUp

    def node(self, data):
        node = shutil.which('node')
        if not node:
            bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if not bundled.is_file():
                self.skipTest('Node required for actual JS/Python queue roundtrip')
            node = str(bundled)
        script = r'''
const fs=require('node:fs'),q=require('./server/management/tablet/checkin-queue.js');
globalThis.crypto ??= require('node:crypto').webcrypto;
const d=JSON.parse(fs.readFileSync(0,'utf8'));
const s={raw:JSON.stringify(d.state),read(){return this.raw;},compareAndSwap(a,b){if(a!==this.raw)return false;this.raw=b;return true;}};
(async()=>{const result=d.action==='record'?q.record(s,d.input,2000):await q.acknowledge(s,d.input.eventRef,d.receipt);
process.stdout.write(JSON.stringify({result,next:q.next(s),state:JSON.parse(s.raw)}));})();'''
        run = subprocess.run([node, '-e', script], input=json.dumps(data), encoding='utf-8', capture_output=True,
                             cwd=Path(__file__).resolve().parents[2], check=True, timeout=20)
        return json.loads(run.stdout)

    def test_complete_offline_capture_server_commit_lost_ack_and_restart(self):
        with self.store.transaction() as db:
            db.execute('UPDATE management_attendance SET charge_units=100')
        state = dict(students=[dict(id=1, name='虚构学生', studentNo='TEST', className='演示班', hours=10)],
            classes=[dict(id=2, name='演示班', teacher='演示教师', durationMinutes=120)],
            attendance=[], transactions=[], settings={'studentLeaves':[]}, managementSync=dict(
                protocol=1, enabled=True, mode='server_authoritative', legacyMirrorDisabled=True,
                orgId=self.f.org, deviceId=self.device['id'], generation='fixture-generation', queue=[], receipts={},
                students={'student':dict(localId=1, localClassId=2, name='虚构学生',studentNo='TEST',className='演示班',
                    classId='class-a',teacher='演示教师',balanceUnits=1000,balanceRevision=0)},
                lessons={'lesson':dict(classId='class-a',startsAt=1900,date='1970-01-01',studentIds=['student'])},
                facts={'lesson':{'student':dict(expected=True,status='missing',revision=0,chargeUnits=100)}}))
        input = dict(studentId='student', lessonId='lesson', occurredAt=1950, eventRef='actual-js-offline')
        captured = self.node(dict(action='record', state=state, input=input))
        self.assertTrue(captured['result']['ok'], captured)
        self.assertEqual(captured['state']['students'][0]['hours'], 9)
        self.assertEqual(self.f.state()[:2], (('missing', 1, 0), (1000, 0)))
        body = json.loads(captured['next']['item']['wire'])
        accepted = self.store.upload_attendance(self.token, **body)
        # Server committed but the first response was lost; process restarted.
        restarted = self.node(dict(action='record', state=captured['state'], input=input))
        self.assertTrue(restarted['result']['duplicate'])
        self.assertEqual(restarted['next']['item']['wire'], captured['next']['item']['wire'])
        repeated = self.store.upload_attendance(self.token, **json.loads(restarted['next']['item']['wire']))
        self.assertTrue(repeated['duplicate'])
        ack = self.node(dict(action='ack',state=restarted['state'],input=input,receipt=repeated))
        self.assertTrue(ack['result']['ok'], ack)
        self.assertIsNone(ack['next']['item'])
        self.assertEqual(ack['state']['students'][0]['hours'], 9)
        self.assertEqual(len(ack['state']['attendance']), 1)
        self.assertEqual([t['amount'] for t in ack['state']['transactions']], [-1])
        self.assertEqual(self.f.state()[:2], (('arrived', 1, 1), (900, 1)))
        duplicate = self.node(dict(action='ack',state=ack['state'],input=input,receipt=accepted))
        self.assertTrue(duplicate['result']['duplicate'])
        self.assertEqual(duplicate['state'], ack['state'])
