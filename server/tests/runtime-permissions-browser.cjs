'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
const assets=path.resolve(__dirname,'../management/web'),origin='http://192.0.2.12:8080';
async function scenario(browser,role){
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const errors=[],writes=[],requests=[];let units=3500,staffRole=null,reviewStatus='submitted';
 const roles=role==='owner'?['admin','owner']:role==='dual'?['teacher','campus_admin']:['campus_admin'];
 const identity={display_name:'虚构操作员',roles,campus_id:role==='owner'?null:'north',campus_name:role==='owner'?null:'虚构北校区',teacher_campus_name:'虚构南校区',web_hour_unit_scale:100,web_face_enabled:false};
 const campus=[{id:'north',name:'虚构北校区'},{id:'south',name:'虚构南校区'}];
 const snapshot={start:'2026-10-01',end:'2026-10-31',total_cents:900,items:[{display_name:'虚构教师',total_cents:900,lessons:[{starts_at:1791000000,class_name:'虚构班',duration_minutes:90,arrived_count:3,rate_cents:300,total_cents:900}]}]};
 page.on('pageerror',error=>errors.push(error.message));
 await page.route(origin+'/**',async route=>{
  const request=route.request(),name=new URL(request.url()).pathname;requests.push(name);
  if(['POST','PUT'].includes(request.method())&&name!=='/v1/auth/login'){
   const body=request.postDataJSON();writes.push({path:name,body});
   if(name==='/v1/owner/students/s/balance')units=body.units;
   if(name==='/v1/owner/staff-access')staffRole=body.role;
   if(name==='/v1/fee-reviews/review')reviewStatus=body.action;
   return route.fulfill({json:{ok:true}});
  }
  const replies={
   '/v1/auth/login':{access_token:'fiction'},
   '/v1/me':identity,
   '/v1/admin/students':{items:[{id:'s',name:'虚构学生',external_ref:'fiction-student',active:true,class_ids:['c'],class_names:['虚构班'],balance_units:units,balance_revision:1}]},
   '/v1/admin/classes':{items:[{id:'c',name:'虚构班',course_id:'co',campus_id:'north',course_name:'虚构课程',course_active:true}]},
   '/v1/admin/teachers':{items:[]},
   '/v1/admin/courses':{items:[{id:'co',name:'虚构课程',content:'虚构内容',active:true,unit_price_cents:2000,class_count:1}]},
   '/v1/admin/devices':{items:[]},
   '/v1/admin/campuses':{items:campus},
   '/v1/owner/staff-access':{items:[{id:'t',login:'fiction-teacher',display_name:'虚构教师',role:staffRole}]},
   '/v1/admin/reports/monthly':{month:'2026-11',month_start:'2026-11-01',month_end:'2026-11-30',generated_at:1,final_period:false,enrollments:[],renewals:[],overall:{new_students:0,renewal_students:0,renewal_events:0,renewal_units:0,renewal_amount_cents:0}},
   '/v1/admin/report-runs':{items:[]},
   '/v1/fee-reviews':{campuses:campus,items:[{id:'review',campus_name:'虚构北校区',period:'month',period_start:'2026-10-01',status:reviewStatus,version:1,reason:'虚构月度审核'}]},
   '/v1/fee-reviews/review':{version:1,snapshot},
   '/v1/teacher/lessons':{date:'2026-11-16',items:[]},
   '/v1/balance-adjustment-requests':{items:[{id:'request',student_name:'虚构学生',campus_name:'虚构北校区',units:3550,previous_units:3500,requested_by_name:'虚构管理员',requested_at:1,reason:'虚构核对',status:'pending',version:1}]}
  };
  if(replies[name])return route.fulfill({json:replies[name]});
  const file=name==='/runtime'?'runtime.html':path.basename(name),target=path.join(assets,file);
  if(fs.existsSync(target)&&fs.statSync(target).isFile())return route.fulfill({path:target,contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]});
  return route.fulfill({status:404,body:''});
 });
 try{
  await page.goto(origin+'/runtime');
  await page.getByLabel('账号',{exact:true}).fill('fiction');await page.getByLabel('密码',{exact:true}).fill('fixture');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'学生与班级',exact:true}).click();
  await page.getByRole('button',{name:role==='owner'?'调整课时':'申请调课时',exact:true}).click();
  await page.getByLabel('调整后的剩余课时',{exact:true}).fill('35.5');
  await page.getByLabel(role==='owner'?'调整原因':'申请原因',{exact:true}).fill('虚构核对');
  await page.getByRole('button',{name:role==='owner'?'确认调整课时':'提交调整申请',exact:true}).click();
  await page.getByText(role==='owner'?'课时已调整并记录流水':'申请已提交，课时余额未改变',{exact:true}).waitFor();
  assert.equal(writes[0].body.units,3550);assert.equal(writes[0].body.expected_revision,1);
  assert.match(writes[0].body.command_ref,/^[a-f0-9]{32}$/);
  assert.equal(units,role==='owner'?3550:3500);
  if(role==='owner'){
   await page.evaluate(()=>ownerStaffDashboard());
   await page.getByRole('button',{name:'调整权限',exact:true}).click();
   await page.getByLabel('身份',{exact:true}).selectOption('campus_admin');
   await page.getByLabel('管理校区',{exact:true}).selectOption('north');
   await page.getByLabel('操作原因',{exact:true}).fill('虚构兼任');
   await page.getByRole('button',{name:'保存权限',exact:true}).click();
   await page.getByText('管理权限已更新',{exact:true}).waitFor();
   const access=writes.find(item=>item.path==='/v1/owner/staff-access').body;
   assert.equal(access.login,'fiction-teacher');assert.equal(access.password,null);
   await page.evaluate(()=>feeReviewDashboard());
   await page.locator('summary').filter({hasText:'虚构北校区 · 月'}).click();
   await page.getByRole('button',{name:'审核通过',exact:true}).click();
   await page.getByLabel('操作说明',{exact:true}).fill('老板复核');
   await page.locator('#device-dialog').getByRole('button',{name:'审核通过',exact:true}).click();
   await page.getByText('审核状态已更新',{exact:true}).waitFor();
   assert.equal(reviewStatus,'approved');
  }else{
   await page.evaluate(()=>balanceRequestsDashboard());
   assert.equal(await page.getByRole('button',{name:'批准调整',exact:true}).count(),0);
   await page.evaluate(()=>adminMonthlyReport());
   assert.equal(requests.includes('/v1/admin/report-runs'),false);
   if(role==='dual'){
    await page.evaluate(()=>dashboard());
    await page.getByRole('button',{name:'我的课堂',exact:true}).click();
    await page.locator('#identity').getByText('虚构操作员 · 虚构南校区',{exact:true}).waitFor();
    await page.getByRole('button',{name:'设备管理',exact:true}).click();
    await page.locator('#identity').getByText('虚构操作员 · 虚构北校区',{exact:true}).waitFor();
   }
  }
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
 }finally{await page.close();}
}
(async()=>{
 const options={headless:true};if(process.env.CHROME_PATH)options.executablePath=process.env.CHROME_PATH;
 const browser=await chromium.launch(options);
 try{for(const role of ['owner','campus','dual'])await scenario(browser,role);console.log('PASS mobile owner correction/review, campus requests/reports, teacher assignment and dual workspace');}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
