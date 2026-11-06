/* Read-only web view of the tablet's actual state. Never includes faces, contacts or secrets. */
function buildWebMirrorDataset(){
  const weeks=[],lessons=[],asOfDate=localDate(),earliest=state.attendance.reduce((a,r)=>r.date&&r.date<a?r.date:a,asOfDate),first=dateWeekStart(earliest),start=calendarWeekRange(0).start;
  const count=Math.max(12,Math.round((new Date(start+'T12:00:00')-new Date(first+'T12:00:00'))/604800000)+1);
  if(count>260)throw Error('历史超过五年，请先分段处理，未上传');
  const units=s=>{const n=Math.round(Number(s.hours||0)*100);if(!Number.isSafeInteger(n)||n< -1000000||n>10000000)throw Error('课时数值需要核对');return n};
  const summary=g=>({name:g.name,expected:g.expected,arrived:g.records.length,leave_count:g.leaves.size,rate:reportRate(g),needs_review:reportNeedsScheduleReview(g)});
  for(let offset=2;offset>=1-count;offset--){
    const stats=buildWeeklyStats(offset);weeks.push({...stats.range,...summary(stats.all),fallback:stats.fallback,classes:[...stats.groups.class.values()].map(summary),teachers:[...stats.groups.teacher.values()].map(summary)});
    for(const session of stats.all.sessions.values()){
      if(!session.date||session.kind==='cancelled')continue;
      const c=classById(session.classId),timing=c?classTimingProfile(c,session.date):null,records=new Map(session.records.map(r=>[r.studentId,r])),members=new Map(session.members);
      for(const r of session.records){if(!members.has(r.studentId)){const s=studentById(r.studentId);if(s)members.set(r.studentId,s)}}
      lessons.push({key:String(session.classId||'deleted:'+session.className)+':'+session.date,class_local_id:String(session.classId||''),class_name:session.className,teacher:session.teacher,date:session.date,kind:session.kind,start_time:timing?.scheduleStart||'',end_time:timing?.scheduleEnd||'',expected:session.expected,arrived:session.records.length,leave_count:session.leaves.size,needs_review:reportNeedsScheduleReview(session),members:[...members.values()].map(s=>{const r=records.get(s.id);return{student_local_id:String(s.id),name:s.name,external_ref:s.studentNo||'',remaining_units:units(studentById(s.id)||s),expected:session.members.has(s.id),status:r?'arrived':session.leaves.has(s.id)?'leave':'missing',attendance_time:r?.time||'',attendance_method:r?.method||'',makeup:Boolean(r?.makeup)}})});
    }
  }
  return{format:'future-school-teacher-web-dataset',schema_version:1,as_of_date:asOfDate,source_captured_at:new Date().toISOString(),source_records:state.attendance.length,classes:state.classes.map(c=>({local_id:String(c.id),name:c.name,course_name:c.courseName||'',teacher:c.teacher||''})),students:state.students.map(s=>({local_id:String(s.id),name:s.name,external_ref:s.studentNo||'',remaining_units:units(s)})),weeks,lessons,history_import_is_read_only:true,balances_charged:false,wechat_enabled:false};
}
