// Synthetic local fixtures only; this test must never call WeChat or any server.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const {chromium}=require('playwright');
const root=path.join(__dirname,'app/src/main/assets');
const server=http.createServer((req,res)=>{const file=path.resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]==='/'?'/index.html':req.url.split('?')[0]));if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return}fs.readFile(file,(error,bytes)=>{if(error){res.writeHead(404).end();return}res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.json':'application/json','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(bytes)})});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],remote=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{if(new URL(route.request().url()).hostname!=='127.0.0.1'){remote.push(route.request().url());return route.abort()}return route.continue()});
    await page.addInitScript(()=>{const RealDate=Date;window.Date=class extends RealDate{constructor(...args){super(...(args.length?args:['2026-10-22T12:00:00+08:00']))}static now(){return new RealDate('2026-10-22T12:00:00+08:00').getTime()}}});
    const url='http://127.0.0.1:'+server.address().port;
    await page.goto(url);await page.waitForFunction(()=>window.__futureSchoolAppStarted);
    await page.evaluate(()=>{
      window.resetWxFixture=()=>{closeModal();state=emptyState();state.settings.cameraPositionConfigured=true;state.classes=[{id:11,name:'蓝黄示例班',teacher:'示例老师',classroom:'示例教室',scheduleWeekday:4}];state.students=Array.from({length:85},(_,i)=>({id:i+1,name:`示例学生${i+1}`,studentNo:`2601${String(i+1).padStart(4,'0')}`,className:'蓝黄示例班',teacher:'示例老师',hours:20,createdAt:'2026-01-01',contact:'private-contact-marker',faceImage:'private-photo-marker',sfaceDescriptors:[]}));ensureEnrollmentHistory();saveState();isAdminAuthenticated=true;currentView='settings';render()};resetWxFixture();
    });
    assert.equal(await page.evaluate(()=>state.wechatNotices===undefined),true,'upgrade stays offline with no automatic mutation');
    await page.locator('#openWechatNotices').click();assert.match(await page.locator('#modal').innerText(),/尚未接通微信/);
    assert.equal(await page.locator('#wxPrepareEnabled').isChecked(),false);assert.equal(await page.locator('#wxIncludeMakeup').isChecked(),false);
    assert.equal(await page.evaluate(()=>recordStudentAttendance(1,'姓名').ok),true);assert.equal(await page.evaluate(()=>state.wechatNotices===undefined),true,'disabled never captures old or new notices');
    assert.equal(await page.evaluate(()=>setWechatStudentConsent(1,true,'母亲').ok),true);
    await page.locator('#wxPrepareEnabled').check();await page.locator('#wxPreparationForm button[type=submit]').click();
    assert.equal(await page.evaluate(()=>state.wechatNotices.events.length),0,'enabling does not backfill');
    assert.equal(await page.evaluate(()=>recordStudentAttendance(2,'姓名').ok),true);assert.equal(await page.evaluate(()=>state.wechatNotices.events.length),0,'no parent consent, no draft');
    await page.evaluate(()=>{setWechatStudentConsent(3,true,'家长');recordStudentAttendance(3,'人脸')});
    const draft=await page.evaluate(()=>state.wechatNotices.events[0]);assert.equal(draft.status,'prepared');assert.equal(draft.studentId,3);assert.equal(draft.makeup,false);
    assert(!JSON.stringify(draft).includes('private-'));assert(!Object.keys(draft).some(key=>/face|contact|hours|teacher/i.test(key)),'draft uses explicit minimal fields');
    assert.equal(await page.evaluate(()=>recordStudentAttendance(3,'学号').ok),false);assert.equal(await page.evaluate(()=>state.wechatNotices.events.length),1,'duplicate checkin cannot duplicate draft');
    assert.equal(await page.evaluate(()=>{try{validateBackupState(JSON.parse(JSON.stringify(state)));return true}catch(_){return false}}),true);
    await page.evaluate(()=>undoAttendanceRecord(state.attendance.find(item=>item.studentId===3).id));
    assert.deepEqual(await page.evaluate(()=>({status:state.wechatNotices.events[0].status,reason:state.wechatNotices.events[0].cancelReason,hours:state.students[2].hours})),{status:'cancelled',reason:'attendance_revoked',hours:20});
    assert.equal(await page.evaluate(()=>{validateBackupState(JSON.parse(JSON.stringify(state)));return true}),true,'cancelled draft can reference revoked attendance');
    await page.evaluate(()=>{setWechatStudentConsent(4,true,'父亲');window.originalWxPersist=persistCurrentState;window.wxBeforeFailure=JSON.stringify(state);persistCurrentState=()=>{throw Error('test quota')}});
    assert.equal(await page.evaluate(()=>recordStudentAttendance(4,'姓名').ok),false);assert.equal(await page.evaluate(()=>JSON.stringify(state)===window.wxBeforeFailure),true,'failed checkin rolls back attendance, balance and draft together');
    assert.equal(await page.evaluate(()=>setWechatStudentConsent(5,true,'家长').ok),false);assert.equal(await page.evaluate(()=>JSON.stringify(state)===window.wxBeforeFailure),true,'consent save failure rolls back');
    await page.evaluate(()=>{persistCurrentState=window.originalWxPersist;recordStudentAttendance(4,'姓名')});
    assert.equal(await page.evaluate(()=>state.wechatNotices.events.filter(item=>item.studentId===4).length),1,'retry creates exactly one draft');
    await page.evaluate(()=>setWechatStudentConsent(4,false,'父亲'));assert.equal(await page.evaluate(()=>state.wechatNotices.events.find(item=>item.studentId===4).cancelReason),'consent_revoked');
    await page.evaluate(()=>{setWechatStudentConsent(6,true,'家长');recordMakeupAttendance([6],'2026-10-21','示例补签')});assert.equal(await page.evaluate(()=>state.wechatNotices.events.some(item=>item.studentId===6)),false,'makeup default excluded');
    await page.evaluate(()=>{setWechatPreparation(true,true);setWechatStudentConsent(7,true,'母亲');recordMakeupAttendance([7],'2026-10-20','示例补签')});
    assert.deepEqual(await page.evaluate(()=>{const item=state.wechatNotices.events.find(item=>item.studentId===7);return{date:item.date,time:item.time,makeup:item.makeup}}),{date:'2026-10-20',time:'',makeup:true});
    await page.evaluate(()=>setWechatPreparation(true,false));assert.equal(await page.evaluate(()=>state.wechatNotices.events.find(item=>item.studentId===7).cancelReason),'makeup_disabled');
    await page.evaluate(()=>{setWechatStudentConsent(8,true,'家长');recordStudentAttendance(8,'姓名');setWechatPreparation(false,false);setWechatPreparation(true,false)});
    assert.equal(await page.evaluate(()=>state.wechatNotices.events.find(item=>item.studentId===8).cancelReason),'preparation_disabled');
    assert.equal(await page.evaluate(()=>state.wechatNotices.events.filter(item=>item.status==='prepared').length),0,'re-enable never revives cancelled drafts');
    const invalid=await page.evaluate(()=>{
      const rejected=mutate=>{const copy=JSON.parse(JSON.stringify(state));mutate(copy);try{validateBackupState(copy);return false}catch(_){return true}};
      return{secret:rejected(copy=>copy.wechatNotices.AppSecret='not-allowed'),face:rejected(copy=>copy.wechatNotices.events[0].faceImage='not-allowed'),sent:rejected(copy=>copy.wechatNotices.events[0].status='sent'),orphan:rejected(copy=>copy.wechatNotices.preferences[0].studentId=999999),duplicate:rejected(copy=>copy.wechatNotices.events.push(copy.wechatNotices.events[0])),schema:rejected(copy=>copy.wechatNotices.version=2)};
    });assert(Object.values(invalid).every(Boolean));
    await page.reload();await page.waitForFunction(()=>window.__futureSchoolAppStarted);assert.equal(await page.evaluate(()=>state.wechatNotices.events.length),4,'drafts and cancellations survive restart');
    await page.evaluate(()=>{isAdminAuthenticated=true;currentView='students';render()});assert(await page.locator('[data-wechat-student]').count()>0,'student entry mounted');
    await page.locator('[data-wechat-student="9"]').click();await page.locator('#wxStudentConsent').check();await page.locator('#wxStudentConsentForm button[type=submit]').click();
    assert.equal(await page.evaluate(()=>Boolean(wechatStudentPreference(studentById(9)))),true);
    await page.evaluate(()=>openWechatNotices('students'));assert.equal(await page.locator('.wx-list-row').count(),40,'paged student consent list');
    await page.locator('[data-wx-page="1"]').click();assert.match(await page.locator('.wx-pagination').innerText(),/2 \/ 3/);
    await page.locator('#wxNoticeSearch').fill('26010085');assert.equal(await page.locator('.wx-list-row').count(),1);assert.match(await page.locator('.wx-list-row').innerText(),/示例学生85/);
    await page.evaluate(()=>{setWechatStudentConsent(10,true,'家长');recordStudentAttendance(10,'姓名');openWechatNotices('events')});
    await page.locator('[data-wx-cancel]').click();assert.equal(await page.evaluate(()=>state.wechatNotices.events.find(item=>item.studentId===10).status),'prepared','cancel needs confirmation');
    await page.locator('#confirmDangerAction').click();assert.equal(await page.evaluate(()=>state.wechatNotices.events.find(item=>item.studentId===10).cancelReason),'manual_cancel');
    assert.equal(await page.evaluate(()=>state.attendance.some(item=>item.studentId===10)),true);assert.equal(await page.evaluate(()=>studentById(10).hours),19);
    await page.evaluate(()=>{state.students.forEach(student=>student.faceImage='');saveState();currentView='settings';render()});
    for(const size of [{width:1280,height:900},{width:800,height:1280},{width:600,height:960},{width:360,height:740}]){
      await page.setViewportSize(size);await page.evaluate(()=>openWechatNotices());await page.waitForFunction(()=>Number(getComputedStyle(document.querySelector('#modal')).opacity)===1);
      const layout=await page.evaluate(()=>{const modal=document.querySelector('#modal'),r=modal.getBoundingClientRect();return{left:r.left,right:r.right,width:innerWidth,overflow:modal.scrollWidth>modal.clientWidth+2}});
      assert(layout.left>=-1&&layout.right<=layout.width+1&&!layout.overflow,JSON.stringify({size,layout}));
      if(size.width===1280)await page.screenshot({path:path.join(__dirname,'docs/screenshots/wechat-preparation.png')});
    }
    await page.evaluate(()=>{isAdminAuthenticated=false;closeModal();openWechatNotices()});assert.equal(await page.locator('#modalBackdrop').isVisible(),false,'kiosk cannot open notification management');
    await page.evaluate(()=>{isAdminAuthenticated=true;setWechatStudentConsent(11,true,'家长');state.students.push({...studentById(11),studentNo:'different-number'});saveState()});
    assert.equal(await page.evaluate(()=>setWechatStudentConsent(11,true,'家长').ok),false,'ambiguous internal id cannot register parent intent');
    await page.evaluate(()=>{state.students.pop();saveState();setWechatStudentConsent(12,true,'家长');recordStudentAttendance(12,'姓名');window.wxDeleteSnapshot=JSON.stringify(state);window.originalWxSafety=requireSafetyBackup;requireSafetyBackup=()=>true;deleteStudent(12)});
    await page.locator('#confirmDangerAction').click();assert.equal(await page.evaluate(()=>state.wechatNotices.events.some(item=>item.studentId===12)||state.wechatNotices.preferences.some(item=>item.studentId===12)),false,'student deletion removes local notice references');
    await page.evaluate(()=>{state=validateBackupState(JSON.parse(window.wxDeleteSnapshot));saveState();requireSafetyBackup=window.originalWxSafety});assert.equal(await page.evaluate(()=>state.wechatNotices.events.some(item=>item.studentId===12)),true,'backup restores local drafts only');
    await page.evaluate(()=>{state.wechatNotices.events=Array.from({length:WECHAT_PREPARATION_LIMIT},(_,i)=>({id:`capacity-${i}`,attendanceId:i+100000,status:'cancelled'}));setWechatStudentConsent(13,true,'家长')});
    assert.equal(await page.evaluate(()=>recordStudentAttendance(13,'姓名').ok),true,'full preparation list cannot block checkin');assert.equal(await page.evaluate(()=>state.wechatNotices.skipped),1);assert.equal(await page.evaluate(()=>studentById(13).hours),19);
    assert.deepEqual(remote,[],'no external network requests');assert.deepEqual(errors,[]);
    console.log('WeChat preparation regressions passed: default offline, no external requests, explicit parent intent, no historical replay, minimal drafts, atomic rollback/retry, duplicate check-in, makeup opt-in, undo/disable/revoke cancellation, backup validation, restart, student deletion, queue capacity, admin gate, paging/search and 4 screen sizes.');
  }finally{await browser.close();server.close()}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
