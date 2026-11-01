'use strict';
// Offline projection only: no network, grants, imports, balance writes or replay.
const crypto=require('node:crypto');
const review=require('./tablet-review/core.js');
const fail=code=>{const e=new Error(code);e.code=code;throw e;};
const object=x=>!!x&&typeof x==='object'&&!Array.isArray(x);
const id=x=>typeof x==='string'&&/^[A-Za-z0-9_.:-]{1,100}$/.test(x);
const integer=Number.isSafeInteger;
const day=t=>new Date((t+28800)*1000).toISOString().slice(0,10);
function exact(x,keys){if(!object(x)||Object.keys(x).sort().join('|')!==keys.split(' ').sort().join('|'))fail('invalid_mapping');}
function unique(rows,key){const m=new Map();for(const r of rows){if(m.has(r[key]))fail('ambiguous_mapping');m.set(r[key],r);}return m;}
function build(raw,mapping,capture,now=Math.floor(Date.now()/1000)){
  if(typeof raw!=='string'||Buffer.byteLength(raw)>50000000)fail('invalid_backup');
  const bytes=Buffer.from(raw,'utf8'),sha=crypto.createHash('sha256').update(bytes).digest('hex');
  exact(capture,'format sha256 bytes captured_at');
  if(capture.format!=='future-school-live-capture-v1'||capture.sha256!==sha||capture.bytes!==bytes.length)fail('capture_digest_mismatch');
  if(!integer(now)||!integer(capture.captured_at)||capture.captured_at<0||capture.captured_at>now+120||now-capture.captured_at>=900)fail('source_not_fresh');
  let data;try{data=JSON.parse(raw);}catch(_){fail('invalid_backup');}
  let checked;try{checked=review.review(data);}catch(_){fail('invalid_backup');}
  if(checked.issue_counts.error)fail('structural_issues');
  exact(mapping,'protocol org_id device_id generation classes students lessons');
  if(mapping.protocol!==1||![mapping.org_id,mapping.device_id,mapping.generation].every(id))fail('invalid_mapping');
  for(const [k,max]of [['classes',1000],['students',2000],['lessons',10000]])if(!Array.isArray(mapping[k])||k!=='lessons'&&!mapping[k].length||mapping[k].length>max)fail('invalid_mapping');
  const localClasses=unique(data.classes,'id'),localStudents=unique(data.students,'id');
  const classes=mapping.classes.map(c=>{
    exact(c,'server_id local_id name teacher_id teacher_name');
    if(!id(c.server_id)||!id(c.teacher_id)||typeof c.name!=='string'||!c.name||typeof c.teacher_name!=='string'||!c.teacher_name||!integer(c.local_id)||c.local_id<=0)fail('invalid_mapping');
    const local=localClasses.get(c.local_id);
    if(!local||local.name!==c.name||local.teacher!==c.teacher_name)fail('class_identity_differs');
    return {...c};
  });
  unique(classes,'local_id');const byClass=unique(classes,'server_id');
  const students=mapping.students.map(s=>{
    exact(s,'server_id local_id external_ref name class_id class_local_id');
    if(!id(s.server_id)||!integer(s.local_id)||s.local_id<=0||!integer(s.class_local_id))fail('invalid_mapping');
    const local=localStudents.get(s.local_id),c=byClass.get(s.class_id);
    if(!local||!c||c.local_id!==s.class_local_id||local.name!==s.name||local.studentNo!==s.external_ref||local.className!==c.name||local.longTermAbsent===true||local.status==='archived')fail('student_identity_differs');
    const units=review.units(local.hours);if(typeof local.hours!=='number'||units===null)fail('balance_invalid');
    return {...s,balance_units:units};
  });
  const byLocal=unique(students,'local_id'),byStudent=unique(students,'server_id');unique(students,'external_ref');
  if(classes.some(c=>!students.some(s=>s.class_id===c.server_id)))fail('unused_class_mapping');
  unique(mapping.lessons,'lesson_id');
  const matchedAttendance=new Set(),matchedLeaves=new Set(),pairs=new Set(),facts=[];
  const allAttendance=data.attendance,allLeaves=data.settings?.studentLeaves||[];
  unique(allLeaves,'id');
  const attendanceByDay=new Map(),leavesByDay=new Map(),transactionsByAttendance=new Map();
  function index(map,key,value){if(!map.has(key))map.set(key,[]);map.get(key).push(value);}
  for(const a of allAttendance)if(byLocal.has(a.studentId))index(attendanceByDay,JSON.stringify([a.studentId,a.date]),a);
  for(const l of allLeaves)if(byLocal.has(l.studentId)&&l.status==='approved')index(leavesByDay,JSON.stringify([l.studentId,l.date]),l);
  for(const t of data.transactions)index(transactionsByAttendance,t.attendanceId,t);
  for(const lesson of mapping.lessons){
    exact(lesson,'lesson_id class_id starts_at charge_units student_ids');
    if(!id(lesson.lesson_id)||!byClass.has(lesson.class_id)||!integer(lesson.starts_at)||lesson.starts_at<0||lesson.starts_at>253402185599||!integer(lesson.charge_units)||lesson.charge_units<=0||!Array.isArray(lesson.student_ids)||!lesson.student_ids.length||lesson.student_ids.length>2000||new Set(lesson.student_ids).size!==lesson.student_ids.length)fail('invalid_lesson_mapping');
    const date=day(lesson.starts_at),course=byClass.get(lesson.class_id);
    for(const studentId of lesson.student_ids){
      const s=byStudent.get(studentId);
      if(!s||s.class_id!==lesson.class_id)fail('historical_class_review_required');
      // The existing tablet writer has one attendance fact per student per day.
      // Two server lessons on that day cannot be disambiguated safely here.
      const pair=JSON.stringify([s.local_id,date]);if(pairs.has(pair))fail('ambiguous_lesson_day');pairs.add(pair);
      const attendance=attendanceByDay.get(pair)||[];
      const leaves=leavesByDay.get(pair)||[];
      if(attendance.length>1||leaves.length>1||attendance.length&&leaves.length)fail('conflicting_attendance');
      for(const record of [...attendance,...leaves]){
        if(record.classId!==course.local_id||record.className!==course.name)fail('historical_class_review_required');
      }
      if(attendance.length){
        const a=attendance[0],tx=transactionsByAttendance.get(a.id)||[];
        if(tx.length!==1||tx[0].studentId!==s.local_id||tx[0].date!==date||review.units(tx[0].amount)!==-lesson.charge_units)fail('charge_evidence_differs');
        matchedAttendance.add(a.id);
      }
      if(leaves.length)matchedLeaves.add(leaves[0].id);
      facts.push({lesson_id:lesson.lesson_id,student_id:studentId,status:attendance.length?'arrived':leaves.length?'leave':'missing',expected:true,charge_units:lesson.charge_units,starts_at:lesson.starts_at});
      if(facts.length>10000)fail('single_class_request_limit');
    }
  }
  if(allAttendance.some(a=>byLocal.has(a.studentId)&&!matchedAttendance.has(a.id))||allLeaves.some(l=>byLocal.has(l.studentId)&&l.status==='approved'&&!matchedLeaves.has(l.id)))fail('unmapped_history');
  classes.sort((a,b)=>a.server_id.localeCompare(b.server_id));students.sort((a,b)=>a.server_id.localeCompare(b.server_id));facts.sort((a,b)=>a.lesson_id.localeCompare(b.lesson_id)||a.student_id.localeCompare(b.student_id));
  const body={workspace_role:'admin',device_id:mapping.device_id,generation:mapping.generation,baseline_sha256:sha,source_captured_at:capture.captured_at,source:{classes,students,facts}};
  const wire=JSON.stringify(body);
  if(Buffer.byteLength(wire)>16384)fail('single_class_request_limit');
  return {body,metadata:{format:'future-school-canonical-manifest-v1',org_id:mapping.org_id,source_sha256:sha,source_bytes:bytes.length,request_bytes:Buffer.byteLength(wire),classes:classes.length,students:students.length,facts:facts.length,offline_only:true,server_written:false,tablet_modified:false}};
}
function buildNative(envelope,mapping,now=Math.floor(Date.now()/1000)){
  exact(envelope,'format captured_at sha256 bytes payload_base64');
  if(envelope.format!=='future-school-native-capture-v1'||typeof envelope.payload_base64!=='string'||envelope.payload_base64.length>28000000||envelope.payload_base64.length%4||!/^[A-Za-z0-9+/]*={0,2}$/.test(envelope.payload_base64))fail('invalid_native_capture');
  const bytes=Buffer.from(envelope.payload_base64,'base64'),raw=bytes.toString('utf8');
  if(bytes.toString('base64')!==envelope.payload_base64||bytes.length>20*1024*1024||!Buffer.from(raw,'utf8').equals(bytes))fail('invalid_native_capture');
  return build(raw,mapping,{format:'future-school-live-capture-v1',sha256:envelope.sha256,bytes:envelope.bytes,captured_at:envelope.captured_at},now);
}
module.exports={build,buildNative};
if(require.main===module){
  const fs=require('node:fs'),path=require('node:path');
  try{
    const args=process.argv.slice(2),options={};
    if(!args.length){console.log('Offline manifest builder. Use --native-capture --mapping --output, or --backup --capture-receipt --mapping --output. Never sends a request.');process.exit(0);}
    for(let n=0;n<args.length;n+=2){if(!['--backup','--mapping','--capture-receipt','--native-capture','--output'].includes(args[n])||!args[n+1]||options[args[n]])fail('invalid_arguments');options[args[n]]=args[n+1];}
    if(!options['--mapping']||!options['--output']||(options['--native-capture']?Object.keys(options).length!==3:Object.keys(options).length!==4||!options['--backup']||!options['--capture-receipt']))fail('invalid_arguments');
    const output=path.resolve(options['--output']),parent=fs.realpathSync(path.dirname(output)),repo=fs.realpathSync(path.resolve(__dirname,'../..'));
    const relative=path.relative(repo,parent);if(!relative||relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))fail('private_output_must_be_outside_repository');
    const mapping=JSON.parse(fs.readFileSync(options['--mapping'],'utf8'));
    const result=options['--native-capture']?buildNative(JSON.parse(fs.readFileSync(options['--native-capture'],'utf8')),mapping):build(fs.readFileSync(options['--backup'],'utf8'),mapping,JSON.parse(fs.readFileSync(options['--capture-receipt'],'utf8')));
    // Exclusive creation: never overwrite an earlier review or its inputs.
    fs.writeFileSync(path.join(parent,path.basename(output)),JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
    console.log(JSON.stringify({...result.metadata,contains_private_data:true}));
  }catch(e){console.error('Manifest stopped: '+(e.code||'invalid_input')+'. No upload or business writes performed.');process.exitCode=1;}
}
