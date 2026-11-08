"""Actual Python approval -> JS local persistence -> Python durable receipt."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from test_management_delivery import DeliveryTests


class RoundtripTests(unittest.TestCase):
    setUp = DeliveryTests.setUp
    approve = DeliveryTests.approve
    poll = DeliveryTests.poll
    def test_python_event_applied_by_javascript_and_acknowledged_once(self):
        node = shutil.which('node')
        if not node:
            bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if not bundled.is_file():
                self.skipTest('Node runtime needed for cross-language contract test')
            node = str(bundled)
        # Actual current app default: one check-in costs one course credit.
        with self.store.transaction() as db:
            db.execute('UPDATE management_attendance SET charge_units=100')
        self.approve()
        offered = self.poll()
        script = r'''
const {receive}=require('./server/management/tablet/approval-receiver.js');
globalThis.crypto ??= require('node:crypto').webcrypto;
let text='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>text+=s);
process.stdin.on('end',async()=>{
 const envelope=JSON.parse(text),e=JSON.parse(envelope.item.event_json);
 const day=new Date((e.starts_at+28800)*1000).toISOString().slice(0,10);
 const state={students:[{id:1,studentNo:'TEST',name:'虚构学生',className:'虚构班',hours:10}],
  classes:[{id:2,name:'虚构班',teacher:'虚构教师',durationMinutes:120}],attendance:[],transactions:[],settings:{studentLeaves:[]},
  managementSync:{protocol:1,enabled:true,mode:'server_authoritative',legacyMirrorDisabled:true,
   orgId:envelope.org_id,deviceId:envelope.device_id,generation:envelope.generation,receipts:{},queue:[],
   students:{student:{localId:1,localClassId:2,studentNo:'TEST',name:'虚构学生',classId:'class-a',className:'虚构班',teacher:'虚构教师',balanceUnits:1000,balanceRevision:0}},
   lessons:{lesson:{classId:'class-a',startsAt:1900,date:day,studentIds:['student']}},
   facts:{lesson:{student:{status:'missing',expected:true,revision:0,chargeUnits:100}}}}};
 const storage={raw:JSON.stringify(state),writes:0,read(){return this.raw;},compareAndSwap(old,next){if(old!==this.raw)return false;this.raw=next;this.writes++;return true;}};
 const first=await receive(storage,envelope),retry=await receive(storage,envelope);
 const saved=JSON.parse(storage.raw);
 process.stdout.write(JSON.stringify({first,retry,writes:storage.writes,hours:saved.students[0].hours,
  attendance:saved.attendance.length,transactions:saved.transactions.map(t=>t.amount)}));
});'''
        output = subprocess.run([node, '-e', script], input=json.dumps(offered), encoding='utf-8',
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=20, check=True)
        applied = json.loads(output.stdout)
        self.assertTrue(applied['first']['ok'], applied)
        self.assertTrue(applied['retry']['duplicate'], applied)
        self.assertEqual((applied['writes'], applied['hours'], applied['attendance'], applied['transactions']),
                         (1, 9, 1, [-1]))
        self.store.acknowledge(self.token, **applied['first']['receipt'])
        self.assertTrue(self.store.acknowledge(self.token, **applied['retry']['receipt'])['duplicate'])
        self.assertIsNone(self.poll()['item'])
        self.assertEqual(self.f.state(), (('arrived', 1, 1), (900, 1), 1))
