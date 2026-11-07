'use strict';
// A minimal immutable history artifact, NOT executable events or an import.
const crypto=require('node:crypto'),{review}=require('./tablet-review/core.js');
// History keeps original fractional lesson balances. Enrollment transport uses
// integer hundredths instead, so its stricter integer encoder is not reused.
function encoded(value){
 const sorted=v=>{if(Array.isArray(v))return v.map(sorted);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])]));return primitive(v);};
 return JSON.stringify(sorted(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
const digest=value=>crypto.createHash('sha256').update(encoded(value)).digest('hex');
const FIELDS={
 attendance:'id studentId classId className teacher classroom school date time method makeup note recordedAt',
 transactions:'id studentId classId className teacher classroom school date time amount attendanceId reason recordedAt',
 leaves:'id studentId studentNo classId className teacher date reason recordedBy source status createdAt cancelledAt cancelledBy cancelReason',
 arrangements:'id classId className studentId studentNo teacher classroom school date fromDate toDate note reason regularWeekday sourceExtra createdAt verifiedAt scheduleStart scheduleEnd lessonDurationMinutes durationMinutes endDayOffset scheduleEndDayOffset',
 schedule:'startDate endDate scheduleWeekday scheduleIntervalWeeks scheduleAnchorDate scheduleStart scheduleEnd lessonDurationMinutes scheduleEndDayOffset durationMinutes endDayOffset',
 enrollment:'classId className teacher classroom startDate endDate scheduleWeekday scheduleIntervalWeeks scheduleAnchorDate',
 absence:'startDate endDate'
};
function primitive(v){if(v===null||typeof v==='boolean'||typeof v==='string'&&v.length<=4000||typeof v==='number'&&Number.isFinite(v)&&Math.abs(v)<=Number.MAX_SAFE_INTEGER)return v;throw Error('Unsupported history value');}
function copy(row,kind){
 if(!row||typeof row!=='object'||Array.isArray(row))throw Error('Invalid history row');const allowed=FIELDS[kind].split(' '),out={};
 for(const [key,value]of Object.entries(row)){
  if(kind==='arrangements'&&['sourceSchedule','targetSchedule'].includes(key)){out[key]=copy(value,'schedule');continue;}
  if(!allowed.includes(key))throw Error('Unknown history field requires explicit review');out[key]=primitive(value);
 }
 return out;
}
function project(row,fields,histories={}){
 const out={};for(const key of fields.split(' '))if(Object.hasOwn(row,key))out[key]=primitive(row[key]);
 for(const [key,kind]of Object.entries(histories))if(Object.hasOwn(row,key)){if(!Array.isArray(row[key])||row[key].length>1000)throw Error('History segment limit');out[key]=row[key].map(x=>copy(x,kind));}
 return out;
}
function build(raw){
 if(typeof raw!=='string'||Buffer.byteLength(raw)>50*1024*1024)throw Error('Complete bounded original backup required');
 const input=JSON.parse(raw),checked=review(input);if(checked.issue_counts.error)throw Error('Resolve structural history errors first');
 const sections={
  classes:input.classes.map(c=>project(c,'id name courseName classLabel teacher classroom school createdAt schedule scheduleWeekday scheduleIntervalWeeks scheduleAnchorDate scheduleStart scheduleEnd lessonDurationMinutes scheduleEndDayOffset',{scheduleHistory:'schedule',classTimingHistory:'schedule'})),
  students:input.students.map(s=>project(s,'id name studentNo className teacher classroom hours createdAt longTermAbsent longTermAbsentSince pausedClassId pausedClassName pausedTeacher pausedClassroom',{enrollmentHistory:'enrollment',longTermAbsenceHistory:'absence'})),
  attendance:input.attendance.map(a=>copy(a,'attendance')),
  transactions:(input.transactions||[]).map(t=>copy(t,'transactions')),
  leaves:(input.settings?.studentLeaves||[]).map(l=>copy(l,'leaves')),
  extra_lessons:(input.settings?.extraClassLessons||[]).map(x=>copy(x,'arrangements')),
  class_reschedules:(input.settings?.classReschedules||[]).map(x=>copy(x,'arrangements')),
  student_reschedules:(input.settings?.studentReschedules||[]).map(x=>copy(x,'arrangements'))
 };
 const artifact={format:'future-school-legacy-history-archive-v1',schema_version:1,source_sha256:crypto.createHash('sha256').update(raw).digest('hex'),source_bytes:Buffer.byteLength(raw),
  provenance:'offline_original_backup',fresh_capture_authorization:false,read_only_history:true,replay_allowed:false,contains_private_data:true,
  sections,manifest:Object.fromEntries(Object.entries(sections).map(([k,rows])=>[k,{rows:rows.length,sha256:digest(rows)}]))};
 if(Buffer.byteLength(encoded(artifact))>10*1024*1024)throw Error('History archive exceeds supported limit; never truncated');
 return{artifact,archive_sha256:digest(artifact),summary:Object.fromEntries(Object.entries(sections).map(([k,rows])=>[k,rows.length])),server_written:false,tablet_modified:false};
}
module.exports={build,encoded,digest};
