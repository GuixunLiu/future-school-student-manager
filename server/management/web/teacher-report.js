'use strict';
let teacherWeek='';
function mondayFor(day){const value=new Date(day+'T00:00:00Z');if(!Number.isFinite(value.getTime()))return'';value.setUTCDate(value.getUTCDate()-((value.getUTCDay()+6)%7));return value.toISOString().slice(0,10);}
function percent(value){return value===null?'—':String(value)+'%';}
function teachingFeeMoney(cents){return '¥'+(cents/100).toFixed(2);}
async function teachingFeesDashboard(admin,period='week',day=chinaToday()){
 const endpoint=admin?'/v1/admin/teaching-fees':'/v1/teacher/teaching-fees';
 const data=await api(endpoint+'?'+new URLSearchParams({period,date:day}));
 if(admin)adminIdentity();else teacherIdentity();
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div');
 copy.append(node('span','课时费','teacher-eyebrow'),node('h1',admin?'教师课时费':'我的课时费'),
  node('p',data.start+' 至 '+data.end+' · 合计 '+teachingFeeMoney(data.total_cents)));
 const actions=node('div',undefined,'toolbar teacher-actions');
 actions.append(button(admin?'返回管理':'返回课堂',admin?dashboard:()=>teacherDashboard(),'secondary'));
 heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-date-filter teaching-fee-filter'),form=node('form',undefined,'classroom-date');
 const periodLabel=node('label','统计周期'),periodSelect=node('select');
 periodSelect.append(new Option('按周','week'),new Option('按月','month'));periodSelect.value=period;
 periodLabel.append(periodSelect);form.append(periodLabel);
 const date=field(form,'统计日期','date');date.value=day;
 const submit=node('button','查看');submit.type='submit';form.append(submit);filter.append(form);
 form.addEventListener('submit',async event=>{
  event.preventDefault();submit.disabled=true;
  try{await teachingFeesDashboard(admin,periodSelect.value,date.value);}
  catch(error){showError(error);}finally{submit.disabled=false;}
 });
 const note=node('p','按已结束课堂的实际签到人数计算。优先使用课程自定义费率。默认 90 分钟每人 ¥3.00、120 分钟每人 ¥4.50，其他时长待核算。','weekly-note');
 const list=node('section',undefined,'teaching-fee-list');
 for(const item of data.items){
  const panel=node('details',undefined,'panel'),summary=node('summary');
  summary.append(node('strong',item.display_name+' · '+teachingFeeMoney(item.total_cents)),
   node('p',(item.campus_name||'未设置校区')+' · '+item.lessons.length+' 节已结束课堂'+(item.unrated_lessons?' · '+item.unrated_lessons+' 节待核算':'')));
  panel.open=!admin;panel.append(summary);
  if(!item.lessons.length)panel.append(node('p','本期没有已结束课堂。'));
  else{
   const lessons=node('ul');
   for(const lesson of item.lessons){
    const time=scheduleDateKey(lesson.starts_at)+' '+lessonTime(lesson.starts_at);
    const fee=lesson.rate_cents===null?'待核算':teachingFeeMoney(lesson.rate_cents)+' × '+lesson.arrived_count+' = '+teachingFeeMoney(lesson.total_cents);
    lessons.append(node('li',time+' · '+lesson.class_name+(lesson.is_substitute?'（代课）':'')+' · '+lesson.duration_minutes+' 分钟 · '+fee));
   }
   panel.append(lessons);
  }
  list.append(panel);
 }
 if(!data.items.length)list.append(node('p','没有教师记录。','empty'));
 $('main').replaceChildren(heading,filter,note,list);
}
async function teacherWeeklyReport(week=teacherWeek||mondayFor(chinaToday())){
 const data=await api('/v1/teacher/reports/weekly?week_start='+encodeURIComponent(week));teacherWeek=data.week_start;currentLesson=null;teacherIdentity();const overall=data.overall;
 const heading=node('div',undefined,'runtime-summary teacher-heading'),copy=node('div');copy.append(node('span','出勤统计','teacher-eyebrow'),node('h1','我的周报'),node('p',data.week_start+' 至 '+data.week_end+' · 实到 ÷ 应到（含请假）'));
 const actions=node('div',undefined,'toolbar teacher-actions');actions.append(button('返回课堂列表',()=>teacherDashboard(teacherDay),'secondary'),button('刷新',()=>teacherWeeklyReport(teacherWeek),'secondary'));if(identity.roles.includes('admin'))actions.append(button('设备管理',dashboard,'secondary'));heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter teacher-date-filter'),form=node('form',undefined,'classroom-date'),label=node('label','查看所在周'),date=node('input');date.type='date';date.required=true;date.value=teacherWeek;date.setAttribute('aria-label','查看所在周');label.append(date);const submit=node('button','查看周报');submit.type='submit';form.append(label,submit);filter.append(form,node('p','选择任意日期，将显示它所在的周一至周日。','weekly-note'));
 form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{await teacherWeeklyReport(mondayFor(date.value));}catch(error){showError(error);}finally{submit.disabled=false;}});
 const summary=node('section',undefined,'panel weekly-summary'),rate=node('div',undefined,'weekly-rate');rate.append(node('span','本周到课率'),node('strong',percent(overall.rate_percent)),node('p','实到 '+overall.arrived_count+' / 应到 '+overall.expected_count+' 人次'));summary.append(rate,countCards({expected:overall.expected_count,arrived:overall.arrived_count,leave:overall.leave_count,missing:overall.missing_count}));
 const note=node('p','仅统计已结束且未取消的课堂；未结束课次 '+overall.pending_lessons+' 节，不计入分母。没有应到人次时，到课率显示“—”。','weekly-note');
 const panel=node('section',undefined,'panel attendance-panel weekly-table teacher-weekly-table'),scroll=node('div',undefined,'table-scroll'),table=node('table');table.setAttribute('aria-label','各班到课率');const head=node('thead'),headRow=node('tr');for(const title of ['班级','应到人次','实到人次','请假人次','未签到人次','课次','到课率']){const cell=node('th',title);cell.scope='col';headRow.append(cell);}head.append(headRow);const body=node('tbody');
 if(!data.classes.length){const row=node('tr'),cell=node('td','本周没有你的课堂');cell.colSpan=7;cell.className='empty';row.append(cell);body.append(row);}
 for(const item of data.classes){const row=node('tr'),name=node('td',undefined,'weekly-class');name.append(node('strong',item.class_name),node('small',item.course_name||'未设置课程名称'));const lessons=node('td');lessons.append(node('span','已结束 '+item.completed_lessons+' 节'),node('small','未结束 '+item.pending_lessons+' 节','revision'));const rateCell=node('td',percent(item.rate_percent),'weekly-rate-value');for(const [index,value] of [name,item.expected_count,item.arrived_count,item.leave_count,item.missing_count,lessons,rateCell].entries()){const cell=index===0||index===5||index===6?value:node('td',String(value));if(index>0)cell.dataset.label=['','应到人次','实到人次','请假人次','未签到人次','课次','到课率'][index];row.append(cell);}body.append(row);}
 table.append(head,body);scroll.append(table);panel.append(scroll);$('main').replaceChildren(heading,filter,summary,note,panel);
}
