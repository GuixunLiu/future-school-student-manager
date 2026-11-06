'use strict';
let adminMonth='';
function shiftMonth(month,offset){const [year,part]=month.split('-').map(Number),value=new Date(Date.UTC(year,part-1+offset,1));return value.toISOString().slice(0,7);}
function monthlyTime(seconds){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(seconds*1000));}
function monthlyMetric(label,value,unit){const item=node('div',undefined,'monthly-metric');item.append(node('span',label),node('strong',String(value)),node('small',unit));return item;}
function monthlyTable(title,headers,items,empty,render){
 const panel=node('section',undefined,'panel monthly-table');panel.append(node('h2',title));const scroll=node('div',undefined,'table-scroll'),table=node('table');table.setAttribute('aria-label',title);const head=node('thead'),headRow=node('tr');for(const label of headers)headRow.append(node('th',label));head.append(headRow);const body=node('tbody');
 if(!items.length){const row=node('tr'),cell=node('td',empty);cell.colSpan=headers.length;cell.className='empty';row.append(cell);body.append(row);}
 for(const item of items){const row=node('tr');for(const value of render(item)){row.append(value instanceof Node?value:node('td',String(value)));}body.append(row);}
 table.append(head,body);scroll.append(table);panel.append(scroll);return panel;
}
async function adminMonthlyReport(month=adminMonth||chinaToday().slice(0,7)){
 const data=await api('/v1/admin/reports/monthly?month='+encodeURIComponent(month));adminMonth=data.month;adminIdentity();const overall=data.overall;
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','招新续费月报'),node('p',data.month_start+' 至 '+data.month_end+' · 按业务发生时间统计，不从课时余额反推。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回设备管理',dashboard,'secondary'),button('刷新',()=>adminMonthlyReport(adminMonth),'secondary'));heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter'),form=node('form',undefined,'classroom-date'),label=node('label','查看月份'),date=node('input');date.type='month';date.required=true;date.value=adminMonth;date.setAttribute('aria-label','查看月份');label.append(date);const submit=node('button','查看月报');submit.type='submit';form.append(label,submit);
 const navigation=node('nav',undefined,'weekly-navigation');navigation.setAttribute('aria-label','月份切换');const previous=button('上一月',()=>adminMonthlyReport(shiftMonth(adminMonth,-1)),'secondary'),current=button('回到本月',()=>adminMonthlyReport(chinaToday().slice(0,7)),'secondary'),next=button('下一月',()=>adminMonthlyReport(shiftMonth(adminMonth,1)),'secondary');previous.dataset.action='previous-month';current.dataset.action='current-month';next.dataset.action='next-month';navigation.append(previous,current,next);filter.append(form,navigation,node('p','招新人数按独立招新记录统计；续费人数按学生去重，续费笔数和课时按记录汇总。','weekly-note'));
 form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{await adminMonthlyReport(date.value);}catch(error){showError(error);}finally{submit.disabled=false;}});
 const metrics=node('section',undefined,'panel monthly-metrics');metrics.append(monthlyMetric('招新人数',overall.new_students,'人'),monthlyMetric('续费人数',overall.renewal_students,'人（去重）'),monthlyMetric('续费笔数',overall.renewal_events,'笔'),monthlyMetric('续费课时',overall.renewal_units,'课时'));
 const note=node('p',(data.final_period?'自然月已结束，按当前不可变记录汇总。':'本月尚未结束，数据会随新记录更新。')+' 不包含金额，不计入期初、签到或普通调账。'+(data.details_truncated?' 明细按时间顺序最多显示前 1000 条，汇总仍包含全部记录。':''),'weekly-note');
 const enrollments=monthlyTable('招新明细',['时间','学生','入班班级','来源编号','经办人'],data.enrollments,'本月没有招新记录',item=>[monthlyTime(item.occurred_at),item.student_name,item.class_name,item.source_ref,item.actor_name]);
 const renewals=monthlyTable('续费明细',['时间','学生','当前班级','续费课时','原因','来源编号','经办人'],data.renewals,'本月没有续费记录',item=>{const units=node('td','+'+item.units_added+' 课时','monthly-units');return[monthlyTime(item.occurred_at),item.student_name,item.class_name,units,item.reason,item.source_ref,item.actor_name];});
 $('main').replaceChildren(heading,filter,metrics,note,enrollments,renewals);
}
