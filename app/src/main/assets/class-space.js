/* Presentation-only state: never persisted with student records. */
let spaceSelectedDate='';
let spaceSelectedClass='';
let spaceRosterTab='absent';
let spaceClockMinute=-1;

function classSpaceCacheFilters(){return[spaceSelectedDate,spaceSelectedClass,spaceRosterTab,listPages.dashboard,(spaceSelectedDate||localDate())===localDate()?Math.floor(Date.now()/60000):0]}
function classSpaceWeek(date){
  const monday=new Date(`${date}T12:00:00`);monday.setDate(monday.getDate()-((monday.getDay()+6)%7));
  return Array.from({length:7},(_,index)=>{const day=new Date(monday);day.setDate(day.getDate()+index);return{date:localDate(day),label:['一','二','三','四','五','六','日'][index],day:day.getDate()}});
}
function classSpaceContext(date=localDate(),requestedClass=spaceSelectedClass){
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
    const roster=(byId.get(String(course.id))||[]).concat(byName.get(normalizedGroupLabel(course.name))||[]),partial=classHasPersonalLessonOnDate(course,validDate)&&!wholeClassHasLessonOnDate(course,validDate),members=roster.filter(profile=>!studentCancelledLessonAt(profile,validDate)&&(!partial||Boolean(studentMovedLessonAt(profile,validDate)))),ids=new Set(members.map(s=>Number(s.id))),checked=new Map();
    dayRecords.forEach(record=>{
      if(!ids.has(Number(record.studentId)))return;
      const belongs=record.classId?String(record.classId)===String(course.id):record.className?normalizedGroupLabel(record.className)===normalizedGroupLabel(course.name):true;
      if(belongs&&!checked.has(Number(record.studentId)))checked.set(Number(record.studentId),record);
    });
    return{course,members,checked,arrived:members.filter(s=>checked.has(Number(s.id))),absent:members.filter(s=>!checked.has(Number(s.id)))};
  };
  let selected=courses.find(course=>String(course.id)===String(requestedClass));
  if(!selected){
    const candidates=scheduled.length?scheduled:courses,now=new Date(),time=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
    selected=validDate===localDate()&&scheduled.length?(candidates.find(course=>course.scheduleStart&&course.scheduleStart>=time)||candidates[candidates.length-1]):candidates[0];
  }
  const visible=scheduled.length?scheduled:courses;
  return{date:validDate,weekday,courses,scheduled,visible,selected:selected?makeGroup(selected):null,dayRecords,makeGroup};
}
function classSpaceStatus(course,date){
  if(classHasPersonalLessonOnDate(course,date)&&!wholeClassHasLessonOnDate(course,date))return'个别学生提前 / 延期';
  if(cancelledLessonAt({classId:course.id},date))return'已调课 · 原日停课';
  if(movedLessonAt({classId:course.id},date))return'调课补上';
  if(hasExtraClassLesson({classId:course.id},date)&&Number(course.scheduleWeekday)!==(new Date(`${date}T12:00:00`).getDay()||7))return'加课安排';
  if(!effectiveClassWeekday({...course,classId:course.id},date))return'未设置排课';
  if(!classHasLessonOnDate(course,date))return'非当日排课';
  if(date<localDate())return'往日课堂';
  if(date>localDate())return'课程预览';
  const now=new Date(),time=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  return course.scheduleStart&&course.scheduleStart>time?'即将上课':'今日课堂';
}
function renderClassSpaceGauge(arrived,total){
  const filled=total?Math.round(Math.min(arrived/total,1)*20):0;
  return `<div class="space-arrival-gauge" role="img" aria-label="${arrived} 人已签到，共 ${total} 人">${Array.from({length:20},(_,index)=>`<i class="${index<filled?'filled':''}" style="transform:rotate(${index*18}deg) translateY(-62px)"></i>`).join('')}<div><strong>${arrived}<small> / ${total}</small></strong><span>已签到</span></div></div>`;
}
function renderClassSpaceDashboard(){
  spaceClockMinute=Math.floor(Date.now()/60000);
  const context=classSpaceContext(spaceSelectedDate||localDate()),group=context.selected,course=group&&group.course,date=context.date;
  const weekly=buildWeeklyStats(0).all,target=weekly.expected,actual=weekly.records.length;
  const weekDays=classSpaceWeek(date),otherCourses=context.visible.filter(item=>!course||item.id!==course.id),low=lowHoursStudents();
  const heading=date===localDate()?'今天，课堂准备好了吗？':date<localDate()?'回看课堂，每一次成长都有记录':'提前看看，下一堂课';
  const feature=course?`<section class="space-current-course"><div class="space-current-copy"><span class="space-course-status">${esc(classSpaceStatus(course,date))}</span><h2>${esc(course.name)}</h2><p>${uiIcon('hours')}<span>${esc(course.scheduleStart?`${course.scheduleStart} 开课`:'上课时间未设置')}</span></p><p>${uiIcon('location')}<span>${esc(course.classroom||'教室未设置')} · ${esc(course.teacher||'老师未设置')}</span></p></div><div class="space-current-actions">${renderClassSpaceGauge(group.arrived.length,group.members.length)}<button type="button" class="space-yellow-btn" data-space-roster-tab="absent">查看未到名单 ${uiIcon('arrow')}</button></div></section>`:`<section class="space-current-course space-first-course"><div><span class="space-course-status">欢迎使用班级空间</span><h2>从第一间课堂开始</h2><p>建立班级并录入学生，就能在这里查看课堂和签到名单。</p><button type="button" class="space-yellow-btn" data-add-class>＋ 新增班级</button></div></section>`;
  const options=context.courses.map(item=>`<option value="${item.id}" ${course&&item.id===course.id?'selected':''}>${esc(item.name)}${item.scheduleStart?` · ${esc(item.scheduleStart)}`:''}</option>`).join('');
  const roster=group?(spaceRosterTab==='arrived'?group.arrived:group.absent):[],page=listPage(roster,'dashboard');
  const rosterMarkup=page.items.map(profile=>{const record=group.checked.get(Number(profile.id));return `<button type="button" class="space-person" data-edit="${profile.id}" aria-label="查看 ${esc(profile.name)} 的学生资料">${avatarMarkup(studentById(profile.id)||profile)}<span><b>${esc(profile.name)}</b><small>${esc(profile.studentNo||'系统学号')}${record?` · ${esc(record.time||'')}`:''}</small></span><em class="${record?'arrived':'waiting'}">${record?'已到':date>localDate()?'待签到':'未到'}</em></button>`}).join('');
  return `<section class="space-heading"><div><h1>${heading}</h1><p>${shortDate(date)} · 星期${['日','一','二','三','四','五','六'][new Date(`${date}T12:00:00`).getDay()]}<span>课堂与名单，集中在这里</span></p></div><label class="space-date-picker" aria-label="选择课堂日期">${uiIcon('classes')}<input id="spaceDateInput" type="date" value="${date}" aria-label="选择课堂日期"></label></section><div class="space-metrics" aria-label="教务概览"><button data-dashboard-stat="students">学生 <b>${state.students.length}</b></button><span>·</span><button data-dashboard-stat="today">今日签到 <b>${todayAttendance().length}</b></button><span>·</span><button data-dashboard-stat="week">本周到课率 <b>${reportRate(weekly)}</b></button><button class="space-low-link" data-dashboard-stat="low-hours">${uiIcon('alert')}课时不足 <b>${low.length}</b><small>≤15 节</small></button></div><div class="space-week-strip" aria-label="选择本周课堂日期"><button class="space-week-arrow" data-space-week="-1" aria-label="上一周">‹</button>${weekDays.map(item=>`<button class="space-day ${item.date===date?'selected':''} ${item.date===localDate()?'is-today':''}" data-space-date="${item.date}" aria-pressed="${item.date===date}"><span>${item.label}</span><b>${item.day}</b><i></i></button>`).join('')}<button class="space-week-arrow" data-space-week="1" aria-label="下一周">›</button></div><div class="space-workspace"><div class="space-class-column">${feature}<div class="space-classes-heading"><div><b>${context.scheduled.length?'当日其他课堂':'班级一览'}</b><small>${context.scheduled.length?`${context.scheduled.length} 个班级在当天有排课`:'当天暂无排课，仍可选择任一班级查看名单'}</small></div><button type="button" data-go="classes">全部班级 ${uiIcon('arrow')}</button></div><div class="space-other-courses">${otherCourses.slice(0,4).map(item=>{const info=context.makeGroup(item);return `<button type="button" class="space-course-tile" data-space-class="${item.id}"><span class="space-tile-head"><b>${esc(item.name)}</b><em>${esc(classSpaceStatus(item,date))}</em></span><span>${uiIcon('hours')}${esc(item.scheduleStart||'时间未设置')}<small> ${info.arrived.length} / ${info.members.length} 已到</small></span><span>${uiIcon('location')}${esc(item.classroom||'教室未设置')} · ${esc(item.teacher||'老师未设置')}</span></button>`}).join('')||`<div class="space-class-note">${course?'可在“班级管理”中设置上课星期与时间，课堂会按日期自动归类。':'录入班级后，这里会显示其他课堂。'}</div>`}</div><div class="space-shortcuts"><button data-go="checkin">${uiIcon('face')}打开签到中心</button><button data-go="hours">${uiIcon('hours')}管理学生课时</button><button data-go="report">${uiIcon('report')}查看到课周报</button></div></div><section class="space-roster" id="spaceRoster"><div class="space-roster-head"><h2>班级学生</h2><button type="button" data-space-full-roster>查看全部 ${uiIcon('arrow')}</button></div><label class="space-roster-select"><span class="sr-only">选择班级</span><select id="spaceClassSelect" ${context.courses.length?'':'disabled'}>${options||'<option>还没有班级</option>'}</select></label><div class="space-roster-tabs"><button type="button" class="${spaceRosterTab==='arrived'?'active':''}" data-space-roster-tab="arrived" aria-pressed="${spaceRosterTab==='arrived'}">已到 ${group?group.arrived.length:0}</button><button type="button" class="${spaceRosterTab==='absent'?'active':''}" data-space-roster-tab="absent" aria-pressed="${spaceRosterTab==='absent'}">${date>localDate()?'待签到':'未到'} ${group?group.absent.length:0}</button></div><div class="space-roster-list">${rosterMarkup||empty('✓',course?spaceRosterTab==='arrived'?'还没有签到记录':group.members.length?'本班学生均已签到':'班级暂无在读学生':'先建立一个班级',course?'名单已自动排除长期不来学生':'建立班级并录入学生后，即可查看名单')}</div>${renderListPager(page)}<div class="space-roster-foot"><small>${date>localDate()?'未来日期仅预览名单，不能提前补签。':'按所选日期统计，已排除当天长期不来学生。'}</small><button type="button" class="secondary-btn" data-space-makeup ${!group||!group.absent.length||date>localDate()?'disabled':''}>${uiIcon('classes')}老师补签</button></div></section></div><p class="space-offline-note"><i></i>本机离线保存 · 学生与人脸资料无需上传网络</p>`;
}
function renderClassSpaceKiosk(){
  const today=todayAttendance(),recent=today.slice(-3).reverse();
  return `<section class="space-kiosk-home"><div class="space-kiosk-intro"><span class="space-kiosk-date">${new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})}</span><h1>你好，<br>小小探索家</h1><p>每一次到来，都是新的进步。</p><div class="space-kiosk-count">今日 <strong>${today.length}</strong> 人已签到</div>${recent.length?`<div class="space-kiosk-recent"><small>最近签到</small>${recent.map(record=>{const student=studentById(record.studentId);return student?`<span><b>${esc(student.name)}</b><em>${esc(record.time||'')}</em></span>`:''}).join('')}</div>`:'<div class="space-kiosk-ready"><i></i>签到已就绪，欢迎来上课</div>'}</div><div class="space-kiosk-stage"><button type="button" class="space-face-action" data-check="人脸" aria-label="开始人脸识别签到">${uiIcon('face')}<b>人脸签到</b><span>轻点开始 ${uiIcon('arrow')}</span></button><p>轻点开始，正对镜头</p><div class="space-alternative-methods"><button type="button" data-check="姓名">${uiIcon('name')}姓名签到</button><span></span><button type="button" data-check="学号">${uiIcon('number')}学号签到</button></div></div></section><p class="space-kiosk-privacy">离线识别 · 资料仅保存在本机 · 签到后显示教室与课时余额</p>`;
}
function setClassSpaceDate(date){
  if(!isValidLocalDate(date))return;
  spaceSelectedDate=date;spaceSelectedClass='';listPages.dashboard=1;render();
}
function handleClassSpaceAction(event){
  const target=event.target.closest('[data-space-date],[data-space-week],[data-space-class],[data-space-roster-tab],[data-space-full-roster],[data-space-makeup]');
  if(!target||!$('#content').contains(target))return false;
  if(target.disabled)return true;
  const data=target.dataset,date=spaceSelectedDate||localDate();
  if(data.spaceDate!==undefined){setClassSpaceDate(data.spaceDate);return true}
  if(data.spaceWeek!==undefined){const day=new Date(`${date}T12:00:00`);day.setDate(day.getDate()+Number(data.spaceWeek)*7);setClassSpaceDate(localDate(day));return true}
  if(data.spaceClass!==undefined){spaceSelectedClass=data.spaceClass;listPages.dashboard=1;render();return true}
  if(data.spaceRosterTab!==undefined){spaceRosterTab=data.spaceRosterTab==='arrived'?'arrived':'absent';listPages.dashboard=1;render();const roster=$('#spaceRoster');if(roster)roster.scrollIntoView({block:'nearest'});return true}
  const context=classSpaceContext(date),group=context.selected;
  if(!group){toast('请先建立一个班级',true);return true}
  if(data.spaceMakeup!==undefined){const available=makeupClassGroupsAt(context.date),match=available.find(item=>String(item.classId)===String(group.course.id))||available.find(item=>!item.classId&&normalizedGroupLabel(item.className)===normalizedGroupLabel(group.course.name));openMakeupCheckin(context.date,match?match.key:`id:${group.course.id}`);return true}
  if(data.spaceFullRoster!==undefined){studentClassFilter=`class:${normalizedGroupLabel(group.course.name)}`;studentStatusFilter='all';studentSearchQuery='';listPages.students=1;go('students');return true}
  return false;
}
function bindClassSpaceViewEvents(){
  const date=$('#spaceDateInput'),course=$('#spaceClassSelect');
  if(date)date.onchange=()=>setClassSpaceDate(date.value);
  if(course)course.onchange=()=>{spaceSelectedClass=course.value;listPages.dashboard=1;render()};
}
function activateClassSpaceNavigation(button){
  if(button.hasAttribute('data-dock-add'))return isAdminAuthenticated?openStudentModal():openAdminLogin();
  if(button.hasAttribute('data-more'))return openMoreMenu();
  go(button.dataset.view);
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
  const search=$('#globalStudentSearchForm'),settings=$('#shellSettingsBtn');
  if(search)search.onsubmit=event=>{event.preventDefault();if(!isAdminAuthenticated)return openAdminLogin();studentSearchQuery=String($('#globalStudentSearchInput').value||'').trim();studentClassFilter='all';studentStatusFilter='all';listPages.students=1;go('students')};
  if(settings)settings.onclick=()=>go('settings');
}
function refreshClassSpaceClock(){
  if(!isAdminAuthenticated||currentView!=='dashboard'||Math.floor(Date.now()/60000)===spaceClockMinute)return;
  if(!$('#modalBackdrop').classList.contains('hidden'))return;
  render();
}
