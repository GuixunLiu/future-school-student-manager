/* Offline review only. Never imports records, changes a backup or sends requests. */
(function(root){
  'use strict';
  const VERSION='0.1.0',MAX_ROWS=10000,MAX_HISTORY=200000,MAX_ISSUES=2000;
  const plain=x=>!!x&&typeof x==='object'&&!Array.isArray(x);
  const text=x=>typeof x==='string'?x.replace(/[\u0000-\u001f\u007f]/g,'').slice(0,120):'';
  const localId=x=>Number.isSafeInteger(x)&&x>0;
  const remoteId=x=>typeof x==='string'&&/^[0-9a-f]{32}$/.test(x);
  function date(x){if(typeof x!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(x))return false;const d=new Date(x+'T12:00:00Z');return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===x;}
  function label(x,required=true){return typeof x==='string'&&x.length<=80&&(!required||x.trim().length>0)&&!/[\u0000-\u001f\u007f]/.test(x);}
  function units(x){if(typeof x!=='number'&&typeof x!=='string'||typeof x==='string'&&(!x||!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(x)))return null;const n=Number(x),u=Math.round(n*100);return Number.isFinite(n)&&Number.isSafeInteger(u)&&u>=-1000000&&u<=10000000&&Math.abs(n*100-u)<.000001?u:null;}
  function rows(value,limit,name){if(!Array.isArray(value)||value.length>limit||value.some(x=>!plain(x)))throw Error(name+'格式无效或超过核对上限');return value;}
  function parse(raw){try{const result=JSON.parse(raw);if(!plain(result))throw Error();return result;}catch(_){throw Error('文件不是完整的JSON对象，请选择备份原文件');}}
  function validateSnapshot(data){
    if(!plain(data)||data.format!=='wxnotify-roster-review'||data.schema_version!==1||data.complete!==true||!label(data.organization)||typeof data.org_id!=='string'||!remoteId(data.org_id))throw Error('服务器清单标识不完整，不能将部分列表当作完整清单');
    const classes=rows(data.classes,MAX_ROWS,'服务器班级清单'),students=rows(data.students,MAX_ROWS,'服务器学生清单'),ids=new Set(),names=new Set(),studentIds=new Set(),numbers=new Set();
    classes.forEach(c=>{if(!remoteId(c.id)||ids.has(c.id)||!label(c.name)||names.has(c.name)||!label(c.course_name,false)||!Array.isArray(c.teachers)||c.teachers.length>50||c.teachers.some(t=>!plain(t)||!remoteId(t.id)||!label(t.name)))throw Error('服务器班级清单存在重复或无效字段');ids.add(c.id);names.add(c.name);});
    students.forEach(s=>{if(!remoteId(s.id)||studentIds.has(s.id)||!label(s.name)||!label(s.external_ref)||numbers.has(s.external_ref)||!ids.has(s.class_id)||!Number.isSafeInteger(s.remaining_units)||s.remaining_units< -1000000||s.remaining_units>10000000||typeof s.active!=='boolean')throw Error('服务器学生清单存在重复、关联或余额问题');studentIds.add(s.id);numbers.add(s.external_ref);});
    return{data,classes,students};
  }
  function review(source,snapshot=null){
    if(!plain(source))throw Error('备份格式无效');
    const classes=rows(source.classes,MAX_ROWS,'平板班级'),students=rows(source.students,MAX_ROWS,'平板学生'),attendance=rows(source.attendance,MAX_HISTORY,'签到记录'),transactions=rows(source.transactions||[],MAX_HISTORY,'课时流水');
    if(source.settings!==undefined&&!plain(source.settings))throw Error('备份设置格式无效');
    const settings=source.settings||{},issues=[],totals={error:0,warning:0,info:0};
    const add=(severity,code,entity,message)=>{totals[severity]++;if(issues.length<MAX_ISSUES)issues.push({severity,code,entity,message});};
    const classIds=new Map(),classNames=new Map(),studentIds=new Map(),numbers=new Map(),homonyms=new Map(),canonicalNames=new Map();
    const output={format:'wxnotify-migration-review',schema_version:1,tool_version:VERSION,review_only:true,ready_for_import:false,comparison_performed:Boolean(snapshot),summary:{classes:classes.length,students:students.length,active:0,paused:0,attendance:attendance.length,transactions:transactions.length,enrollment_segments:0,schedule_segments:0,approved_leaves:0,cancelled_leaves:0,extra_lessons:0,class_reschedules:0,student_reschedules:0},classes:[],students:[],server_only_students:[],issues,issue_counts:totals,issues_truncated:false,unreviewed:['历史记录未迁移，当前余额不反推充值或新生','不展开真实课次；调课、加课、转班、历史在读须单独核对','教师姓名只作核对，不用于创建账号或授予权限','不验证人脸、照片、联系人、后台密码或设备凭证']};
    function schedule(c,ref){
      const interval=c.scheduleIntervalWeeks===undefined?1:c.scheduleIntervalWeeks,weekday=c.scheduleWeekday===undefined?0:c.scheduleWeekday,anchor=c.scheduleAnchorDate||'';
      if(![1,2].includes(interval)||!Number.isInteger(weekday)||weekday<0||weekday>7)add('error','INVALID_SCHEDULE',ref,'上课星期或周期无效');
      if(interval===2&&(!date(anchor)||weekday<1||(new Date(anchor+'T12:00:00Z').getUTCDay()||7)!==weekday))add('error','INVALID_FORTNIGHT_ANCHOR',ref,'隔周首课日期与上课星期不一致或缺失');
      return{interval_weeks:[1,2].includes(interval)?interval:null,weekday:Number.isInteger(weekday)&&weekday>=0&&weekday<=7?weekday:null,anchor_date:interval===2?text(anchor):''};
    }
    function periods(items,ref,kind){
      if(items===undefined)return;
      rows(items,1000,'日期历史');let previous=null;
      items.forEach(e=>{if(!date(e.startDate)||e.endDate&&!date(e.endDate)||e.endDate&&e.endDate<e.startDate||previous&&(!previous.endDate||previous.endDate>=e.startDate))add('error','INVALID_HISTORY_RANGE',ref,'历史日期有缺失、顺序或重叠问题');if(kind==='schedule'||kind==='enrollment')schedule(e,ref);previous=e;});
      if(kind==='enrollment')output.summary.enrollment_segments+=items.length;
      if(kind==='schedule')output.summary.schedule_segments+=items.length;
    }
    classes.forEach((c,index)=>{
      const ref='班级#'+(localId(c.id)?c.id:index+1);
      if(!localId(c.id)||classIds.has(c.id))add('error','DUPLICATE_OR_INVALID_CLASS_ID',ref,'班级内部编号无效或重复');else classIds.set(c.id,c);
      if(!label(c.name))add('error','INVALID_CLASS_NAME',ref,'完整班名缺失或不符合服务器字段限制');
      if(classNames.has(c.name))add('error','DUPLICATE_CLASS_NAME',ref,'完整班名重复，不能自动映射');else classNames.set(c.name,c);
      if(typeof c.name==='string'){
        const canonical=c.name.trim().normalize('NFKC').toLowerCase();
        if(canonicalNames.has(canonical)&&canonicalNames.get(canonical)!==c.name)add('warning','SIMILAR_CLASS_NAME',ref,'存在仅空格、全半角或大小写不同的班名，需人工核对，不自动合并');
        canonicalNames.set(canonical,c.name);
        if(c.name!==c.name.trim())add('error','CLASS_NAME_WHITESPACE',ref,'完整班名有首尾空格，需要核对');
      }
      if(!c.courseName)add('warning','COURSE_UNCLASSIFIED',ref,'所属课程未明确保存，不按班名前缀猜测');else if(!label(c.courseName))add('error','INVALID_COURSE',ref,'课程名称无效');
      if(!label(c.teacher))add('warning','CLASS_TEACHER_UNCONFIRMED',ref,'未明确任课老师，需要单独授权');
      const s=schedule(c,ref);if(!s.weekday)add('warning','SCHEDULE_UNCONFIRMED',ref,'没有明确课日，不据班名生成课次');
      const start=c.scheduleStart||'',end=c.scheduleEnd||'',clock=x=>typeof x==='string'&&/^([01]\d|2[0-3]):[0-5]\d$/.test(x);
      if(!clock(start)||!clock(end)||end<=start)add('warning','CLASS_TIME_UNCONFIRMED',ref,'开始/结束时间未完整确认或跨午夜，暂不生成课次');
      periods(c.scheduleHistory,ref,'schedule');
      const last=Array.isArray(c.scheduleHistory)?c.scheduleHistory.at(-1):null;
      if(last&&(last.endDate||Number(last.scheduleWeekday||0)!==s.weekday||(last.scheduleIntervalWeeks??1)!==s.interval_weeks||(s.interval_weeks===2&&last.scheduleAnchorDate!==s.anchor_date)))add('error','CURRENT_HISTORY_MISMATCH',ref,'班级当前周期与历史末段不一致');
      output.classes.push({local_id:localId(c.id)?c.id:null,name:text(c.name),course_name:text(c.courseName),display_name:text(c.classLabel),teacher_label:text(c.teacher),...s,start_time:text(start),end_time:text(end),comparison:'not_compared'});
    });
    students.forEach((s,index)=>{
      const ref='学生#'+(localId(s.id)?s.id:index+1);
      if(!localId(s.id)||studentIds.has(s.id))add('error','DUPLICATE_OR_INVALID_STUDENT_ID',ref,'学生内部编号无效或重复');else studentIds.set(s.id,s);
      if(!label(s.name))add('error','INVALID_STUDENT_NAME',ref,'学生姓名缺失或不符合字段限制');
      if(!label(s.studentNo))add('error','INVALID_STUDENT_NUMBER',ref,'学号必须是明确的非空文本，保留前导零');
      else{if(numbers.has(s.studentNo))add('error','DUPLICATE_STUDENT_NUMBER',ref,'同一机构学号重复，禁止自动合并');else numbers.set(s.studentNo,s);if(s.studentNo!==s.studentNo.trim())add('error','STUDENT_NUMBER_WHITESPACE',ref,'学号有首尾空格，需要核对');}
      const remaining=units(s.hours);if(remaining===null)add('error','INVALID_BALANCE',ref,'课时必须在支持范围内且最多两位小数，不能自动四舍五入');
      if(s.longTermAbsent!==undefined&&typeof s.longTermAbsent!=='boolean')add('error','INVALID_PAUSE_STATUS',ref,'长期不来状态格式无效');
      const paused=s.longTermAbsent===true,course=classNames.get(s.className);
      output.summary[paused?'paused':'active']++;
      if(!paused&&(!course||typeof s.className!=='string'))add('error','CLASS_NOT_FOUND',ref,'当前在读班级未唯一找到，不猜测归属');
      if(paused)add('warning','PAUSED_STUDENT_MANUAL',ref,'长期不来学生需单独确认处理方式，不自动按在读学生导入');
      if(course&&s.teacher&&s.teacher!==course.teacher)add('warning','STUDENT_TEACHER_DIFFERS',ref,'学生旧老师标签与当前班级老师不同，以班级配置核对，不覆盖历史');
      periods(s.enrollmentHistory,ref,'enrollment');periods(s.longTermAbsenceHistory,ref,'pause');
      if(!Array.isArray(s.enrollmentHistory)||!s.enrollmentHistory.length)add('warning','ENROLLMENT_HISTORY_MISSING',ref,'在读历史缺失，不能还原历史转班日期');
      const group=homonyms.get(s.name)||[];group.push({id:localId(s.id)?s.id:index+1,className:text(s.className),pausedClassName:text(s.pausedClassName)});homonyms.set(s.name,group);
      output.students.push({local_id:localId(s.id)?s.id:null,student_no:text(s.studentNo),name:text(s.name),class_name:text(s.className),paused_class_name:text(s.pausedClassName),remaining_units:remaining,status:paused?'paused':'active',comparison:'not_compared'});
    });
    homonyms.forEach(group=>{if(group.length>1)add('warning',new Set(group.map(s=>s.className||s.pausedClassName||'')).size>1?'HOMONYM_ACROSS_CLASSES':'HOMONYM_SAME_CLASS',group.map(s=>'学生#'+s.id).join('、').slice(0,500),'有同名学生，必须按唯一学号和班级区分，不合并记录');});
    const attendanceIds=new Map(),attendanceDays=new Set(),transactionIds=new Set(),links=new Set();
    attendance.forEach((a,index)=>{const ref='签到#'+(localId(a.id)?a.id:index+1),key=a.studentId+':'+a.date;if(!localId(a.id)||attendanceIds.has(a.id)||!studentIds.has(a.studentId)||!date(a.date)||attendanceDays.has(key))add('error','INVALID_ATTENDANCE_REFERENCE',ref,'签到存在重复编号/日期、无效日期或学生关联');attendanceIds.set(a.id,a);attendanceDays.add(key);});
    transactions.forEach((t,index)=>{const ref='流水#'+(localId(t.id)?t.id:index+1);if(!localId(t.id)||transactionIds.has(t.id)||!studentIds.has(t.studentId)||!date(t.date)||units(t.amount)===null)add('error','INVALID_TRANSACTION',ref,'课时流水编号、学生关联、日期或数值无效');transactionIds.add(t.id);if(t.attendanceId!==undefined&&t.attendanceId!==null&&t.attendanceId!==''){const a=attendanceIds.get(t.attendanceId);if(!a||a.studentId!==t.studentId||a.date!==t.date||Number(t.amount)!==-1||links.has(t.attendanceId))add('error','ATTENDANCE_TRANSACTION_MISMATCH',ref,'签到扣课关联不一致，不据流水重算当前余额');links.add(t.attendanceId);}});
    const leaveDays=new Set(),leaveIds=new Set();
    rows(settings.studentLeaves||[],MAX_HISTORY,'请假记录').forEach((l,index)=>{const ref='请假#'+(localId(l.id)?l.id:index+1),key=l.studentId+':'+l.date;if(!localId(l.id)||leaveIds.has(l.id)||!studentIds.has(l.studentId)||!date(l.date)||!['approved','cancelled'].includes(l.status))add('error','INVALID_LEAVE',ref,'请假编号、学生、日期或状态无效');leaveIds.add(l.id);if(l.status==='approved'){output.summary.approved_leaves++;if(leaveDays.has(key)||attendanceDays.has(key))add('error','LEAVE_ATTENDANCE_CONFLICT',ref,'有效请假重复或与同日签到冲突');leaveDays.add(key);}if(l.status==='cancelled')output.summary.cancelled_leaves++;});
    for(const [key,target]of [['extraClassLessons','extra_lessons'],['classReschedules','class_reschedules'],['studentReschedules','student_reschedules']]){const arrangements=rows(settings[key]||[],MAX_HISTORY,'调课/加课安排');output.summary[target]=arrangements.length;if(arrangements.length)add('warning','ARRANGEMENTS_MANUAL_REVIEW',key,'包含历史加课或调课，只统计数量，不自动展开或迁移课次');}
    if(output.summary.approved_leaves)add('warning','EXISTING_LEAVES_NOT_REPLAYED','请假','已有有效请假不由启用新事件同步补传，需单独核对');
    if(output.summary.active>2000||classes.length>100)add('warning','DEVICE_SCOPE_LIMIT','同步范围','超过单设备计划核对范围，需要按班级划分设备授权');
    if(snapshot){
      const remote=validateSnapshot(snapshot),remoteClasses=new Map(remote.classes.map(c=>[c.name,c])),remoteStudents=new Map(remote.students.map(s=>[s.external_ref,s])),remoteById=new Map(remote.classes.map(c=>[c.id,c])),seenRemote=new Set();
      output.server_organization=text(remote.data.organization);output.server_org_id=remote.data.org_id;
      add('warning','ORGANIZATION_CONFIRMATION_REQUIRED','机构','请确认这是本校的服务器清单；同名机构或相同学号不代表身份已核验');
      const stamp=typeof remote.data.generated_at==='string'&&remote.data.generated_at.length<=40?Date.parse(remote.data.generated_at):NaN;
      if(!Number.isFinite(stamp))add('warning','SERVER_SNAPSHOT_TIME_UNKNOWN','服务器清单','导出时间未确认，需要重新导出当前完整清单');
      else if(Date.now()-stamp>86400000||stamp>Date.now()+300000)add('warning','SERVER_SNAPSHOT_STALE','服务器清单','导出时间已过一天或晚于当前时间，需要重新核对清单时效');
      output.classes.forEach(c=>{const r=remoteClasses.get(c.name);c.comparison=r?'match':'new';if(r){if(c.course_name!==r.course_name){c.comparison='conflict';add('error','SERVER_COURSE_DIFFERS','班级#'+c.local_id,'同名班级所属课程不同，不能自动覆盖');}if(!r.teachers.some(t=>t.name===c.teacher_label))add('warning','SERVER_TEACHER_UNCONFIRMED','班级#'+c.local_id,'服务器任课授权未与平板老师标签对应，须按人员身份核对');}});
      output.students.forEach(s=>{const r=remoteStudents.get(s.student_no);s.comparison=r?'match':'new';if(!r)return;seenRemote.add(r.id);const rclass=remoteById.get(r.class_id),fields=[];if(s.name!==r.name)fields.push('姓名');if((s.status==='paused'?s.paused_class_name:s.class_name)!==rclass?.name)fields.push('班级');if(s.remaining_units!==r.remaining_units)fields.push('余额');if((s.status==='active')!==r.active)fields.push('在读状态');if(fields.length){s.comparison='conflict';add('error','SERVER_STUDENT_DIFFERS','学生#'+s.local_id,'同学号的'+fields.join('、')+'不同，不能自动覆盖');}});
      remote.students.forEach(s=>{if(!seenRemote.has(s.id)){output.server_only_students.push({student_no:text(s.external_ref),name:text(s.name),class_name:text(remoteById.get(s.class_id)?.name),remaining_units:s.remaining_units,status:s.active?'active':'paused'});add('warning','SERVER_ONLY_STUDENT','服务器学生#'+s.id,'服务器已有但平板未匹配；可能是测试资料或来源不同，不自动删除');}});
    }
    output.issues_truncated=Object.values(totals).reduce((a,b)=>a+b,0)>issues.length;
    output.review_status=totals.error?'blocked':totals.warning?'manual_review':'checks_passed_pending_confirmation';
    return output;
  }
  function projectBackup(data){
    const primitive=x=>x===null||['string','number','boolean','undefined'].includes(typeof x)?x:null;
    const pick=(o,keys)=>Object.fromEntries(keys.filter(k=>Object.prototype.hasOwnProperty.call(o,k)).map(k=>[k,primitive(o[k])]));
    const history=x=>x===undefined?undefined:rows(x,1000,'日期历史').map(e=>pick(e,['startDate','endDate','scheduleWeekday','scheduleIntervalWeeks','scheduleAnchorDate']));
    const ckeys=['id','name','courseName','classLabel','teacher','scheduleWeekday','scheduleIntervalWeeks','scheduleAnchorDate','scheduleStart','scheduleEnd'];
    const skeys=['id','name','studentNo','className','pausedClassName','teacher','hours','longTermAbsent'];
    return{classes:data.classes.map(c=>({...pick(c,ckeys),scheduleHistory:history(c.scheduleHistory)})),students:data.students.map(s=>({...pick(s,skeys),enrollmentHistory:history(s.enrollmentHistory),longTermAbsenceHistory:history(s.longTermAbsenceHistory)})),attendance:data.attendance.map(a=>pick(a,['id','studentId','date'])),transactions:(data.transactions||[]).map(t=>pick(t,['id','studentId','date','amount','attendanceId'])),settings:{studentLeaves:(data.settings?.studentLeaves||[]).map(l=>pick(l,['id','studentId','date','status'])),...Object.fromEntries(['extraClassLessons','classReschedules','studentReschedules'].map(k=>[k,(data.settings?.[k]||[]).map(()=>({}))]))}};
  }
  const api=Object.freeze({VERSION,review,parse,units,validateSnapshot,projectBackup});
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.TabletMigrationReview=api;
})(globalThis);
