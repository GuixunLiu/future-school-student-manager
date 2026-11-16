'use strict';
// Fictional HTTP fixtures; never connects to an application database.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const assets=path.resolve(__dirname,'../management/web');
async function verify(browser,scale){
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const errors=[],writes=[];let balance=35*scale;
 page.on('pageerror',error=>errors.push(error.message));
 const student=()=>({id:'s',name:'虚构学生',external_ref:'fiction-s',active:true,class_ids:['c'],class_names:['虚构班'],balance_units:balance,balance_revision:1});
 await page.route('http://192.0.2.10:8080/**',async route=>{
  const request=route.request(),name=new URL(request.url()).pathname;
  if(name==='/bad-response')return route.fulfill({status:502,contentType:'text/html',body:'upstream unavailable'});
  if(name==='/v1/admin/renewals'){
   const data=request.postDataJSON();writes.push(data);const previous=balance;balance+=data.units_added;
   return route.fulfill({json:{...data,previous_balance_units:previous,balance_units:balance,course_name:'虚构课',notification_tasks:0}});
  }
  if(request.method()==='POST'&&['/v1/admin/students','/v1/admin/lessons'].includes(name)){
   writes.push({path:name,...request.postDataJSON()});
   return route.fulfill({status:409,json:{error:{message:'虚构提交已捕获，未写入数据库'}}});
  }
  const responses={
   '/v1/auth/login':{access_token:'fiction'},
   '/v1/me':{display_name:'虚构管理员',roles:['admin'],web_hour_unit_scale:scale,web_face_enabled:false},
   '/v1/admin/classes':{items:[{id:'c',name:'虚构班',course_id:'co',course_name:'虚构课',course_active:true}]},
   '/v1/admin/devices':{items:[]},'/v1/admin/students':{items:[student(),{id:'pending',name:'待核对学生',active:true,class_ids:[],balance_units:null}]},
   '/v1/admin/courses':{items:[{id:'co',name:'虚构课',content:'测试',active:true,unit_price_cents:2000}]},
   '/v1/admin/teachers':{items:[{id:'t',login:'fiction',display_name:'虚构教师',class_ids:['c']}]},
   '/v1/admin/lessons':{items:[],date:'2026-11-16',server_time:1},
   '/v1/admin/schedule-templates':{items:[]},
   '/v1/admin/report-runs':{items:[]},
   '/v1/admin/reports/monthly':{month:'2026-11',month_start:'2026-11-01',month_end:'2026-11-30',generated_at:1,final_period:false,enrollments:[],
    overall:{new_students:0,renewal_students:1,renewal_events:1,renewal_units:10*scale,renewal_amount_cents:20000},
    renewals:[{occurred_at:1,student_name:'虚构学生',course_name:'虚构课',units_added:10*scale,amount_cents:20000,unit_price_cents:2000}]}
  };
  if(responses[name])return route.fulfill({json:responses[name]});
  const file=name==='/runtime'?'runtime.html':path.basename(name),target=path.join(assets,file);
  if(fs.existsSync(target)&&fs.statSync(target).isFile())return route.fulfill({path:target,contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'})[path.extname(file)]});
  return route.fulfill({status:404,body:''});
 });
 try{
  await page.goto('http://192.0.2.10:8080/runtime');
  await page.getByLabel('账号',{exact:true}).fill('fiction');await page.getByLabel('密码',{exact:true}).fill('fixture');
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'续费登记',exact:true}).click();
  await page.locator('.renewal-balance').filter({hasText:/^35 课时/}).waitFor();
  assert.equal(await page.locator('[data-action=renew][data-student-id=pending]').isDisabled(),true);
  await page.locator('[data-action=renew][data-student-id=s]').click();
  await page.getByLabel('续费课时',{exact:true}).fill('10');
  assert.equal(await page.getByLabel('实收金额（元）',{exact:true}).inputValue(),'200.00');
  await page.getByLabel('业务来源编号',{exact:true}).fill('fiction-renew');
  await page.getByRole('checkbox').check();
  await page.getByRole('button',{name:'确认续费并入账',exact:true}).click();
  await page.locator('.renewal-balance').filter({hasText:/^45 课时/}).waitFor();
  assert.equal(writes[0].units_added,10*scale);
  assert.equal(writes[0].amount_cents,20000);
  await page.evaluate(()=>adminEnrollmentDashboard());
  await page.getByLabel('学生姓名',{exact:true}).fill('虚构新生');await page.getByLabel('学生学号',{exact:true}).fill('fiction-new');
  await page.getByLabel('期初课时（允许负数）',{exact:true}).fill(scale===100?'-1.25':'-1');
  await page.locator('#main').getByLabel('业务来源编号',{exact:true}).fill('fiction-opening');
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'确认招新并入班',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('notice').textContent.includes('虚构提交已捕获'));
  assert.equal(writes.find(w=>w.path==='/v1/admin/students').opening_units,scale===100?-125:-1);
  await page.evaluate(()=>adminScheduleDashboard());
  await page.getByLabel('统一扣课课时',{exact:true}).fill(scale===100?'1.5':'1');
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'确认创建课次',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('.schedule-create button[type=submit]')?.disabled);
  assert.equal(writes.find(w=>w.path==='/v1/admin/lessons').charge_units,scale===100?150:1);
  await page.evaluate(()=>adminMonthlyReport());
  await page.getByText('+10 课时',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const result=await page.evaluate(async()=>{try{await api('/bad-response');return null;}catch(error){return {message:error.message,status:error.status};}});
  assert.deepEqual(result,{message:'服务暂时不可用，请稍后重试（502）',status:502});
  assert.equal(await page.evaluate(()=>lessonUnits('0.001').toString()),'NaN');
  assert.equal(await page.evaluate(()=>lessonUnits('1.001').toString()),'NaN');
  assert.equal(await page.evaluate(()=>lessonUnits('0.29')),scale===100?29:NaN);
  assert.deepEqual(errors,[]);
 }finally{await page.close();}
}
(async()=>{
 const options={headless:true};if(process.env.CHROME_PATH)options.executablePath=process.env.CHROME_PATH;
 const browser=await chromium.launch(options);
 try{for(const scale of [1,100])await verify(browser,scale);console.log('PASS mobile renewal, opening hours, lesson charge, monthly report, pending balance and non-JSON errors at both unit scales');}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
