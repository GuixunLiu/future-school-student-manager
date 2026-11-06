// Synthetic data only. Exercise production dialogs without a camera or attendance writes.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const {chromium}=require('playwright');
const root=path.join(__dirname,'app/src/main/assets');
const css=fs.readFileSync(path.join(root,'class-space.css'),'utf8');
const brandBlock=css.split('/* Check-in brand completion:')[1].split('/* Use native orientation')[0];
const declarations=brandBlock.slice(brandBlock.indexOf('*/')+2).matchAll(/\{([^}]+)\}/g);
for(const [,body] of declarations)for(const declaration of body.split(';').filter(Boolean)){
  assert.match(declaration.trim(),/^(color|background|border-color|box-shadow):/,'Theme patch must not change camera geometry');
}
const server=http.createServer((req,res)=>{
  const file=path.resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]==='/'?'/index.html':req.url.split('?')[0]));
  if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return}
  fs.readFile(file,(error,bytes)=>{if(error){res.writeHead(404).end();return}res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.png':'image/png'})[path.extname(file)]||'application/octet-stream');res.end(bytes)});
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:800}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.__futureSchoolAppStarted);
    await page.addStyleTag({content:'*{transition:none!important}'});
    await page.evaluate(()=>{
      closeModal();state=emptyState();state.settings.cameraPositionConfigured=true;
      state.classes=[{id:11,name:'机器人创意班',teacher:'胡晓波',classroom:'创客教室 1'}];
      state.students=[{id:1,name:'林小满',studentNo:'26010001',className:'机器人创意班',teacher:'胡晓波',hours:20,createdAt:'2026-01-01',sfaceModelVersion:4,sfaceDescriptors:[Array(128).fill(.1)]}];
      ensureEnrollmentHistory();invalidatePageCache();isAdminAuthenticated=false;currentView='checkin';render();
      startAutomaticRecognition=()=>{};
    });
    const color=async(selector,property='backgroundColor',pseudo=null)=>page.locator(selector).evaluate((el,{property,pseudo})=>getComputedStyle(el,pseudo)[property],{property,pseudo});
    const geometry=()=>page.evaluate(()=>Object.fromEntries(['#modal','.face-scan-stage','.recognition-camera','.face-scan-aside'].map(selector=>{const r=document.querySelector(selector).getBoundingClientRect();return[selector,[r.x,r.y,r.width,r.height]]})));
    for(const [label,width,height] of [['landscape',1280,800],['portrait',800,1280],['compact',600,960],['phone',390,844]]){
      await page.setViewportSize({width,height});
      for(const edge of ['top','right','bottom','left']){
        await page.evaluate(edge=>{closeModal();state.settings.cameraLandscapeEdge=edge;state.settings.cameraPortraitEdge=edge;openFaceCheckin()},edge);
        await page.waitForFunction(()=>document.querySelector('#modal').classList.contains('camera-popup-animate'));
        await page.locator('#modal').evaluate(el=>el.getAnimations().forEach(animation=>animation.finish()));
        assert.equal(await color('.scan-state-icon'),'rgb(1, 101, 179)');
        assert.equal(await color('.scan-state-icon i'),'rgb(244, 171, 4)');
        assert.equal(await color('.face-guide','borderTopColor'),'rgb(244, 171, 4)');
        assert.equal(await color('#modal','backgroundColor','::after'),'rgb(1, 101, 179)');
        assert.match(await color('.face-scan-aside','backgroundImage'),/234, 244, 252/);
        assert.equal(await page.locator('#modalBackdrop').getAttribute('data-camera-edge'),edge);
        const before=await geometry();
        await page.evaluate(()=>{document.querySelector('#cameraStatus').textContent='当前人脸未通过严格安全阈值，请重新录入本人资料或改用姓名、学号签到。较长的识别提示内容不应让取景框或状态栏发生缩放。'});
        assert.deepEqual(await geometry(),before,label+' '+edge+' camera layout must remain stable');
        assert.equal(await color('#camera','transform'),'matrix(-1, 0, 0, 1, 0, 0)','mirror behavior unchanged');
        // Compare geometry with the brand-completion block removed, to guard existing positioning.
        await page.evaluate(async()=>{
          const link=document.querySelector('link[href="class-space.css"]'),text=await(await fetch(link.href)).text();
          const style=document.createElement('style');style.id='preBrandGeometry';style.textContent=text.replace(/\/\* Check-in brand completion:[\s\S]*?(?=\/\* Use native orientation)/,'');link.after(style);link.disabled=true;
        });
        assert.deepEqual(await geometry(),before,label+' '+edge+' geometry must match prior build');
        await page.evaluate(()=>{document.querySelector('#preBrandGeometry').remove();document.querySelector('link[href="class-space.css"]').disabled=false});
        if(edge==='top'&&label==='landscape'){
          await page.evaluate(()=>{document.querySelector('#cameraStatus').textContent='请正对摄像头，保持光线充足';document.querySelector('#recognitionBadge').textContent='正在寻找人脸'});
          await page.screenshot({path:path.join(__dirname,'../face-theme-scan.png'),animations:'disabled'});
        }
        await page.evaluate(()=>showFaceConfirmation(state.students[0],.9));
        assert.equal(await page.locator('#faceScanStage').isVisible(),false,'camera hidden after recognition');
        assert.equal(await color('.recognition-success-mark'),'rgb(1, 101, 179)');
        assert.match(await color('#recognitionResult','backgroundImage'),/234, 244, 252/);
        assert.equal(await color('#confirmRecognized'),'rgb(1, 101, 179)');
        if(edge==='top'&&label==='landscape')await page.screenshot({path:path.join(__dirname,'../face-theme-confirm.png'),animations:'disabled'});
      }
      await page.evaluate(()=>showCheckinSuccess(state.students[0],'人脸','，人脸档案已完成第 1 次学习'));
      assert.equal(await color('.checkin-classroom-card'),'rgb(1, 101, 179)');
      assert.equal(await color('.checkin-classroom-card strong','color'),'rgb(244, 171, 4)');
      assert.equal(await color('.checkin-success-mark'),'rgb(1, 101, 179)');
      assert.match(await page.locator('#modal').innerText(),/创客教室 1/);assert.match(await page.locator('#modal').innerText(),/胡老师/);assert.doesNotMatch(await page.locator('#modal').innerText(),/胡晓波/);
      if(label==='landscape')await page.screenshot({path:path.join(__dirname,'../face-theme-success.png'),animations:'disabled'});
      await page.evaluate(()=>{closeModal();openCameraPositionSetup(false)});
      assert.equal(await color('.camera-edge-options button.selected[data-camera-mode="landscape"]'),'rgb(1, 101, 179)');
      await page.locator('[data-camera-mode="landscape"][data-camera-edge="right"]').click();
      assert.equal(await page.locator('[data-camera-mode="landscape"][data-camera-edge="right"]').getAttribute('aria-pressed'),'true');
      assert.equal(await color('[data-camera-mode="landscape"][data-camera-edge="right"]'),'rgb(1, 101, 179)');
      await page.evaluate(()=>{closeModal();openStudentModal(state.students[0])});
      assert.equal(await color('.face-preview'),'rgb(243, 248, 252)');
      await page.evaluate(()=>closeModal());
    }
    assert.deepEqual(await page.evaluate(()=>({hours:state.students[0].hours,attendance:state.attendance.length,transactions:state.transactions.length})),{hours:20,attendance:0,transactions:0});
    assert.deepEqual(errors,[]);
    console.log('Face theme checks passed: 4 screen sizes x 4 camera edges; blue/yellow scan, confirmation, success, setup and enrollment; stable geometry, mirror preserved, camera hidden after recognition, surname teacher label, no data mutations.');
  }finally{await browser.close();server.close()}
})().catch(error=>{console.error(error);server.close();process.exitCode=1});
