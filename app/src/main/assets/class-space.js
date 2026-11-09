function classSpaceContext(date=localDate(),requestedClass=''){
  const validDate=isValidLocalDate(date)?date:localDate(),weekday=new Date(`${validDate}T12:00:00`).getDay()||7;
  const profiles=reportableStudentsAt(validDate),byId=new Map(),byName=new Map();
  profiles.forEach(profile=>{
    const key=String(profile.classId||''),name=normalizedGroupLabel(profile.className);
    if(key){if(!byId.has(key))byId.set(key,[]);byId.get(key).push(profile)}
    else{if(!byName.has(name))byName.set(name,[]);byName.get(name).push(profile)}
  });
  const courses=state.classes.slice().sort((a,b)=>String(a.scheduleStart||'99:99').localeCompare(String(b.scheduleStart||'99:99'))||compareLabels(a.name,b.name));
  const scheduled=courses.filter(course=>classHasLessonOnDate(course,validDate));
  const dayRecords=state.attendance.filter(record=>record.date===validDate);
  const makeGroup=course=>{
    const roster=(byId.get(String(course.id))||[]).concat(byName.get(normalizedGroupLabel(course.name))||[]),partial=classHasPersonalLessonOnDate(course,validDate)&&!wholeClassHasLessonOnDate(course,validDate),members=roster.filter(profile=>!studentCancelledLessonAt(profile,validDate)&&(!partial||Boolean(studentMovedLessonAt(profile,validDate)))&&(lessonCycleWeeks(profile)!==2||(studentLessonPlan(profile,validDate).physical||studentLessonPlan(profile,validDate).plannedPhysical)||dayRecords.some(record=>record.studentId===profile.id&&(record.classId?String(record.classId)===String(course.id):normalizedGroupLabel(record.className)===normalizedGroupLabel(course.name))))),ids=new Set(members.map(s=>Number(s.id))),checked=new Map();
    dayRecords.forEach(record=>{
      if(!ids.has(Number(record.studentId)))return;
      const belongs=record.classId?String(record.classId)===String(course.id):record.className?normalizedGroupLabel(record.className)===normalizedGroupLabel(course.name):true;
      if(belongs&&!checked.has(Number(record.studentId)))checked.set(Number(record.studentId),record);
    });
    return{course,members,checked,restDay:lessonCycleWeeks(classScheduleProfile(course,validDate))===2&&!classHasLessonOnDate(course,validDate),arrived:members.filter(s=>checked.has(Number(s.id))),leaves:members.filter(s=>!checked.has(Number(s.id))&&studentLeaveAt(s,validDate)),absent:members.filter(s=>!checked.has(Number(s.id))&&!studentLeaveAt(s,validDate))};
  };
  let selected=courses.find(course=>String(course.id)===String(requestedClass));
  if(!selected){
    const candidates=scheduled.length?scheduled:courses,now=new Date(),time=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
    selected=validDate===localDate()&&scheduled.length?(candidates.find(course=>course.scheduleStart&&course.scheduleStart>=time)||candidates[candidates.length-1]):candidates[0];
  }
  const visible=scheduled.length?scheduled:courses;
  return{date:validDate,weekday,courses,scheduled,visible,selected:selected?makeGroup(selected):null,dayRecords,makeGroup};
}
function renderClassSpaceKiosk(){
  const today=todayAttendance(),recent=today.slice(-3).reverse(),sync=typeof canonicalQueueStatus==='function'?canonicalQueueStatus():null;
  const syncMarkup=sync?`<button type="button" class="space-sync-status ${sync.blocked?'blocked':sync.pending?'pending':''}" data-go="settings" aria-label="查看设备与同步状态"><i></i>${sync.blocked?`待核对 ${sync.blocked} 条 · 原记录已保留`:sync.pending?`待上传 ${sync.pending} 条 · 断网也不会丢失`:'已连接 · 签到自动上传'}</button>`:'';
  return `<section class="space-kiosk-home"><div class="space-kiosk-intro"><span class="space-kiosk-date">${new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})}</span><h1>你好，<br>小小探索家</h1><p>每一次到来，都是新的进步。</p><div class="space-kiosk-count">今日 <strong>${today.length}</strong> 人已签到</div>${recent.length?`<div class="space-kiosk-recent"><small>最近签到</small>${recent.map(record=>{const student=studentById(record.studentId);return student?`<span><b>${esc(student.name)}</b><em>${esc(record.time||'')}</em></span>`:''}).join('')}</div>`:'<div class="space-kiosk-ready"><i></i>签到已就绪，欢迎来上课</div>'}</div><div class="space-kiosk-stage"><button type="button" class="space-face-action" data-check="人脸" aria-label="开始人脸识别签到">${uiIcon('face')}<b>人脸签到</b><span>轻点开始 ${uiIcon('arrow')}</span></button><p>轻点开始，正对镜头</p><div class="space-alternative-methods"><button type="button" data-check="姓名">${uiIcon('name')}姓名签到</button><span></span><button type="button" data-check="学号">${uiIcon('number')}学号签到</button></div></div></section>${syncMarkup}<p class="space-kiosk-privacy">${sync?'离线可签到 · 待传记录保存在本机 · 联网后自动同步':'离线识别 · 资料仅保存在本机 · 签到后显示教室与课时余额'}</p>`;
}
function bindClassSpaceShell(){
  if(typeof document.addEventListener==='function'){
    document.addEventListener(window.PointerEvent?'pointerdown':'touchstart',()=>{skipOpeningBackdropClick=false},true);
    document.addEventListener('click',event=>{
      if(!skipOpeningBackdropClick)return;
      skipOpeningBackdropClick=false;
      if(event.detail===0)return;
      event.preventDefault();event.stopPropagation();
    },true);
  }
  const settings=$('#shellSettingsBtn');
  if(settings)settings.onclick=()=>go('settings');
}
