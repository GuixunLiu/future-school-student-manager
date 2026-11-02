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
function project(raw,mapping,capture,now=Math.floor(Date.now()/1000),segmented=false,boundary=null){
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
  // The current App switches its writer globally. Segmented full-school review
  // must not silently leave active students using an unbound check-in path.
  if(segmented){const active=checked.students.filter(s=>s.status==='active').map(s=>s.local_id);
    if(active.length!==students.length||active.some(x=>!byLocal.has(x)))fail('whole_device_scope_required');}
  if(classes.some(c=>!students.some(s=>s.class_id===c.server_id)))fail('unused_class_mapping');
  unique(mapping.lessons,'lesson_id');
  const matchedAttendance=new Set(),matchedLeaves=new Set(),pairs=new Set(),facts=[];
  const allAttendance=boundary?data.attendance.filter(a=>a.date>=boundary.canonical_from_day):data.attendance;
  const allLeaves=boundary?(data.settings?.studentLeaves||[]).filter(l=>l.date>=boundary.canonical_from_day):(data.settings?.studentLeaves||[]);
  unique(allLeaves,'id');
  const attendanceByDay=new Map(),leavesByDay=new Map(),transactionsByAttendance=new Map();
  function index(map,key,value){if(!map.has(key))map.set(key,[]);map.get(key).push(value);}
  for(const a of allAttendance)if(byLocal.has(a.studentId))index(attendanceByDay,JSON.stringify([a.studentId,a.date]),a);
  for(const l of allLeaves)if(byLocal.has(l.studentId)&&l.status==='approved')index(leavesByDay,JSON.stringify([l.studentId,l.date]),l);
  for(const t of data.transactions)index(transactionsByAttendance,t.attendanceId,t);
  for(const lesson of mapping.lessons){
    exact(lesson,'lesson_id class_id starts_at charge_units student_ids');
    if(!id(lesson.lesson_id)||!byClass.has(lesson.class_id)||!integer(lesson.starts_at)||lesson.starts_at<0||lesson.starts_at>253402185599||!integer(lesson.charge_units)||lesson.charge_units<=0||!Array.isArray(lesson.student_ids)||!lesson.student_ids.length||lesson.student_ids.length>2000||new Set(lesson.student_ids).size!==lesson.student_ids.length)fail('invalid_lesson_mapping');
    if(boundary&&lesson.starts_at<boundary.canonical_from)fail('legacy_lesson_read_only');
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
  if(!segmented&&Buffer.byteLength(wire)>16384)fail('single_class_request_limit');
  if(Buffer.byteLength(wire)>6*1024*1024)fail('review_total_limit');
  return {body,metadata:{format:'future-school-canonical-manifest-v1',org_id:mapping.org_id,source_sha256:sha,source_bytes:bytes.length,request_bytes:Buffer.byteLength(wire),classes:classes.length,students:students.length,facts:facts.length,offline_only:true,server_written:false,tablet_modified:false}};
}
function build(raw,mapping,capture,now){return project(raw,mapping,capture,now);}
function buildSegmented(raw,mapping,capture,commandRef,now){
  const result=project(raw,mapping,capture,now,true);
  const packed=require('./pack-enrollment-review.cjs').pack(result.body,commandRef);
  return{...packed,metadata:{...result.metadata,...packed.metadata}};
}
function nativePayload(envelope){
  exact(envelope,'format captured_at sha256 bytes payload_base64');
  if(envelope.format!=='future-school-native-capture-v1'||typeof envelope.payload_base64!=='string'||envelope.payload_base64.length>28000000||envelope.payload_base64.length%4||!/^[A-Za-z0-9+/]*={0,2}$/.test(envelope.payload_base64))fail('invalid_native_capture');
  const bytes=Buffer.from(envelope.payload_base64,'base64'),raw=bytes.toString('utf8');
  if(bytes.toString('base64')!==envelope.payload_base64||bytes.length>20*1024*1024||!Buffer.from(raw,'utf8').equals(bytes))fail('invalid_native_capture');
  return{raw,capture:{format:'future-school-live-capture-v1',sha256:envelope.sha256,bytes:envelope.bytes,captured_at:envelope.captured_at}};
}
function buildNative(envelope,mapping,now){const {raw,capture}=nativePayload(envelope);return build(raw,mapping,capture,now);}
function buildNativeSegmented(envelope,mapping,commandRef,now){const {raw,capture}=nativePayload(envelope);return buildSegmented(raw,mapping,capture,commandRef,now);}
function boundaryProjection(raw,mapping,capture,boundary,now=Math.floor(Date.now()/1000)){
  exact(boundary,'format org_id initialization_id baseline_sha256 archive_sha256 canonical_from_day canonical_from openings_sha256 rehearsal_only activation_authorized boundary_sha256');
  const {boundary_sha256,...claim}=boundary,packing=require('./pack-enrollment-review.cjs'),history=require('./build-legacy-history-archive.cjs');
  if(boundary.format!=='future-school-history-boundary-v1'||boundary.org_id!==mapping.org_id||boundary.rehearsal_only!==true||boundary.activation_authorized!==false
    ||['initialization_id','baseline_sha256','archive_sha256','openings_sha256','boundary_sha256'].some(k=>typeof boundary[k]!=='string'||!/^[a-f0-9]{64}$/.test(boundary[k]))
    ||packing.digest(claim)!==boundary_sha256||boundary.baseline_sha256!==capture.sha256
    ||typeof boundary.canonical_from_day!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(boundary.canonical_from_day)||!integer(boundary.canonical_from)
    ||Date.parse(boundary.canonical_from_day+'T00:00:00+08:00')/1000!==boundary.canonical_from
    ||now>=boundary.canonical_from||capture.captured_at>=boundary.canonical_from)fail('history_boundary_differs');
  const archived=history.build(raw);
  if(archived.archive_sha256!==boundary.archive_sha256)fail('history_boundary_differs');
  for(const section of ['attendance','transactions','leaves'])if(archived.artifact.sections[section].some(r=>typeof r.date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(r.date)||r.date>=boundary.canonical_from_day))fail('future_legacy_admission_required');
  const result=project(raw,mapping,capture,now,true,boundary);
  const openings=result.body.source.students.map(s=>({student_id:s.server_id,class_id:s.class_id,units:s.balance_units,revision:0})).sort((a,b)=>a.student_id<b.student_id?-1:a.student_id>b.student_id?1:0);
  if(packing.digest(openings)!==boundary.openings_sha256)fail('history_openings_differ');
  result.metadata={...result.metadata,history_boundary_sha256:boundary_sha256,legacy_read_only:true,legacy_replayed:false,activation_authorized:false};
  return result;
}
function buildBoundary(raw,mapping,capture,boundary,now){const result=boundaryProjection(raw,mapping,capture,boundary,now);if(result.metadata.request_bytes>16384)fail('single_class_request_limit');return result;}
function buildBoundarySegmented(raw,mapping,capture,boundary,commandRef,now){
  const result=boundaryProjection(raw,mapping,capture,boundary,now),packed=require('./pack-enrollment-review.cjs').pack(result.body,commandRef);
  return{...packed,metadata:{...result.metadata,...packed.metadata}};
}
function buildNativeBoundarySegmented(envelope,mapping,boundary,commandRef,now){const{raw,capture}=nativePayload(envelope);return buildBoundarySegmented(raw,mapping,capture,boundary,commandRef,now);}
module.exports={build,buildNative,buildSegmented,buildNativeSegmented,buildBoundary,buildBoundarySegmented,buildNativeBoundarySegmented};
if(require.main===module){
  const fs=require('node:fs'),path=require('node:path');
  try{
    const args=process.argv.slice(2),options={};
    if(!args.length){console.log('Offline manifest builder. Use --native-capture --mapping --output, or --backup --capture-receipt --mapping --output. Optional --segmented-ref creates full-active-scope pages. Never sends a request.');process.exit(0);}
    for(let n=0;n<args.length;n+=2){if(!['--backup','--mapping','--capture-receipt','--native-capture','--output','--segmented-ref','--history-boundary'].includes(args[n])||!args[n+1]||options[args[n]])fail('invalid_arguments');options[args[n]]=args[n+1];}
    const extra=(options['--segmented-ref']?1:0)+(options['--history-boundary']?1:0);
    if(!options['--mapping']||!options['--output']||(options['--native-capture']?Object.keys(options).length!==3+extra:Object.keys(options).length!==4+extra||!options['--backup']||!options['--capture-receipt']))fail('invalid_arguments');
    const output=path.resolve(options['--output']),parent=fs.realpathSync(path.dirname(output)),repo=fs.realpathSync(path.resolve(__dirname,'../..'));
    const relative=path.relative(repo,parent);if(!relative||relative!=='..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative))fail('private_output_must_be_outside_repository');
    const mapping=JSON.parse(fs.readFileSync(options['--mapping'],'utf8'));
    let result;
    if(options['--history-boundary']){
      const boundary=JSON.parse(fs.readFileSync(options['--history-boundary'],'utf8'));
      const input=options['--native-capture']?nativePayload(JSON.parse(fs.readFileSync(options['--native-capture'],'utf8'))):{raw:fs.readFileSync(options['--backup'],'utf8'),capture:JSON.parse(fs.readFileSync(options['--capture-receipt'],'utf8'))};
      result=options['--segmented-ref']?buildBoundarySegmented(input.raw,mapping,input.capture,boundary,options['--segmented-ref']):buildBoundary(input.raw,mapping,input.capture,boundary);
    }else result=options['--segmented-ref']?
      options['--native-capture']?buildNativeSegmented(JSON.parse(fs.readFileSync(options['--native-capture'],'utf8')),mapping,options['--segmented-ref']):buildSegmented(fs.readFileSync(options['--backup'],'utf8'),mapping,JSON.parse(fs.readFileSync(options['--capture-receipt'],'utf8')),options['--segmented-ref']):
      options['--native-capture']?buildNative(JSON.parse(fs.readFileSync(options['--native-capture'],'utf8')),mapping):build(fs.readFileSync(options['--backup'],'utf8'),mapping,JSON.parse(fs.readFileSync(options['--capture-receipt'],'utf8')));
    // Exclusive creation: never overwrite an earlier review or its inputs.
    fs.writeFileSync(path.join(parent,path.basename(output)),JSON.stringify(result,null,2)+'\n',{flag:'wx',mode:0o600});
    console.log(JSON.stringify({...result.metadata,contains_private_data:true}));
  }catch(e){console.error('Manifest stopped: '+(e.code||'invalid_input')+'. No upload or business writes performed.');process.exitCode=1;}
}
