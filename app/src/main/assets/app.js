const STORAGE_KEY = 'mashang_offline_v1';
const emptyState = () => ({
  students: [], classes: [], attendance: [], transactions: [],
  settings: { orgName: '未来学校学生管理系统', campusCode: '01', studentNoCounters: {}, weeklyTarget: 2, extraClassLessons: [], classReschedules: [], studentReschedules: [], studentLeaves: [], adminUser: 'admin', adminPassword: '123456', cameraPositionConfigured: false, cameraPortraitEdge: 'top', cameraLandscapeEdge: 'top', faceStorageVersion: 2 }
});

let state = loadState();
if(!Array.isArray(state.settings.classReschedules))state.settings.classReschedules=[];
if(state.settings.adminPattern||state.settings.adminAuthMode==='pattern'){delete state.settings.adminPassword;delete state.settings.adminUser}
let lastGeneratedId = ['students','classes','attendance','transactions'].reduce((highest,key)=>(Array.isArray(state[key])?state[key]:[]).reduce((max,item)=>Math.max(max,Number(item&&item.id)||0),highest),0);
if(state.settings.orgName==='码上成长')state.settings.orgName='未来学校学生管理系统';
state.settings.campusCode=normalizeCampusCode(state.settings.campusCode);
if(!state.settings.studentNoCounters||typeof state.settings.studentNoCounters!=='object')state.settings.studentNoCounters={};
let currentView = 'checkin';
let isAdminAuthenticated = false;
let selectedReportWeekOffset = 0;
let selectedReportScopeType = 'school', selectedReportScopeValue = '';
let hoursClassFilter = 'all';
let hoursSortOrder = 'asc';
let studentStatusFilter = 'all';
let studentClassFilter = 'all';
let studentSearchQuery = '';
let studentSearchFrame = null;
let dataRevision = 0;
let studentLookupSource=null,studentLookupLength=-1,studentLookupRevision=-1;
let studentLookup=new Map();
const pageCache = new Map();
let mountedPageKey = '';
let mountedPageName = '';
let mountedPageRevision = -1;
const LIST_PAGE_SIZE = 40;
const listPages = {students:1,hours:1,attendance:1,dashboard:1};
const nameCollator = typeof Intl!=='undefined'&&Intl.Collator?new Intl.Collator('zh-CN'):null;
const compareLabels = (a,b) => nameCollator?nameCollator.compare(String(a),String(b)):String(a).localeCompare(String(b));
const compareStudentNames = (a,b) => compareLabels(a.name,b.name);
let activeStream = null;
let faceModelsPromise = null;
let faceLibrariesPromise = null;
let sfaceSessionPromise = null;
let faceScanToken = 0;
let cameraSetupCallback = null;
let checkinSuccessTimer = null;
let skipOpeningBackdropClick = false;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const formEntriesObject = formData => {const output={};formData.forEach((value,key)=>output[key]=value);return output};
const esc = (value = '') => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const uid = () => {lastGeneratedId=Math.max(Date.now()*1000,lastGeneratedId+1);return lastGeneratedId};
const localDate = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const money = n => Number(n || 0).toFixed(Number(n || 0) % 1 ? 1 : 0);
const pct = (a,b) => b ? Math.min(100, Math.round(a / b * 1000) / 10) : 0;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const SECURE_FACE_VERSION = 2;
const PHOTO_FACE_VERSION = 3;
const SFACE_MODEL_VERSION = 4;
const MAX_ADAPTIVE_FACE_SAMPLES = 2;
const MAX_ANCHOR_FACE_SAMPLES = 3;
const MAX_FACE_THUMBNAIL_LENGTH = 8000;
const HOURS_CRITICAL_THRESHOLD = 10;
const HOURS_WARNING_THRESHOLD = 15;
const CAMERA_EDGES = ['top','right','bottom','left'];
const CAMERA_EDGE_LABELS = {top:'上边',right:'右边',bottom:'下边',left:'左边'};
const hasLegacyFaceProfile = s => Number(s.faceSecurityVersion) >= SECURE_FACE_VERSION && Array.isArray(s.faceDescriptors) && (s.faceDescriptors.length >= 5 || Number(s.faceSecurityVersion) >= PHOTO_FACE_VERSION);
const hasSFaceProfile = s => Number(s.sfaceModelVersion) >= SFACE_MODEL_VERSION && Array.isArray(s.sfaceDescriptors) && s.sfaceDescriptors.some(isStoredFaceDescriptor);
const hasFaceProfile = s => hasSFaceProfile(s)||hasLegacyFaceProfile(s);
const canPrepareSFaceProfile = s => hasSFaceProfile(s)||(hasLegacyFaceProfile(s)&&Boolean(s.faceImage));
const studentFaceSamples = student => [...(Array.isArray(student.sfaceDescriptors)?student.sfaceDescriptors:[]),...(Array.isArray(student.sfaceAdaptiveDescriptors)?student.sfaceAdaptiveDescriptors:[])].map(unpackFaceDescriptor).filter(sample=>sample.length===128);
const hoursWarningLevel = value => Number(value)<=HOURS_CRITICAL_THRESHOLD?'critical':Number(value)<=HOURS_WARNING_THRESHOLD?'low':'';
const hoursWarningLabel = value => Number(value)<0?`欠课 ${money(Math.abs(Number(value)))} 节`:Number(value)<=HOURS_CRITICAL_THRESHOLD?'10 节课以内':Number(value)<=HOURS_WARNING_THRESHOLD?'15 节课以内':'';
const studentsUnderHours = (threshold=HOURS_WARNING_THRESHOLD,students=reportableStudents()) => students.filter(student=>!isLongTermAbsent(student)&&Number(student.hours)<=Number(threshold)).slice().sort((a,b)=>Number(a.hours)-Number(b.hours)||compareStudentNames(a,b));
const lowHoursStudents = (students=reportableStudents()) => studentsUnderHours(HOURS_WARNING_THRESHOLD,students);
const studentHoursWarningLevel = student => isLongTermAbsent(student)?'':hoursWarningLevel(student.hours);
const studentHoursWarningLabel = student => isLongTermAbsent(student)?'':hoursWarningLabel(student.hours);

ensureClasses();
ensureEnrollmentHistory();
normalizeLongTermStudentAssignments();
ensureAttendanceSnapshots();
compactAllFaceProfiles();
let committedStateSnapshot = JSON.stringify(state);

function loadLocalScript(src){
  return new Promise((resolve,reject)=>{
    const script=document.createElement('script');script.src=src;script.async=true;
    script.onload=()=>resolve();
    script.onerror=()=>{script.remove();reject(new Error('离线人脸组件加载失败，请重试'))};
    document.head.appendChild(script);
  });
}
function loadFaceLibraries(){
  if(!faceLibrariesPromise){
    faceLibrariesPromise=Promise.all([
      typeof faceapi==='undefined'?loadLocalScript('face-api.min.js'):Promise.resolve(),
      !nativeSFaceAvailable()&&typeof ort==='undefined'?loadLocalScript('ort.wasm.min.js'):Promise.resolve()
    ]).catch(error=>{faceLibrariesPromise=null;throw error});
  }
  return faceLibrariesPromise;
}
function loadFaceModels(){
  if(faceModelsPromise) return faceModelsPromise;
  faceModelsPromise = loadFaceLibraries().then(()=>Promise.all([
    faceapi.nets.tinyFaceDetector.loadFromUri('models'),
    faceapi.nets.faceLandmark68TinyNet.loadFromUri('models'),
    loadSFaceEngine()
  ])).catch(error=>{faceModelsPromise=null;throw error});
  return faceModelsPromise;
}
function nativeSFaceAvailable(){try{return Boolean(window.AndroidBridge&&typeof AndroidBridge.hasNativeSFace==='function'&&AndroidBridge.hasNativeSFace())}catch(_){return false}}
function loadSFaceEngine(){return nativeSFaceAvailable()?Promise.resolve('android-native'):loadSFaceModel()}
function loadSFaceModel(){
  if(sfaceSessionPromise)return sfaceSessionPromise;
  if(typeof ort==='undefined')return Promise.reject(new Error('高精度人脸组件未加载'));
  ort.env.wasm.numThreads=1;
  ort.env.wasm.simd=true;
  ort.env.wasm.wasmPaths={mjs:'ort-wasm-simd-threaded.mjs',wasm:'ort-wasm-simd-threaded.wasm'};
  sfaceSessionPromise=ort.InferenceSession.create('models/sface_int8bq.onnx',{executionProviders:['wasm'],graphOptimizationLevel:'all',executionMode:'sequential'}).catch(error=>{sfaceSessionPromise=null;throw error});
  return sfaceSessionPromise;
}
function averageLandmarks(points,indexes){const total=indexes.reduce((sum,index)=>({x:sum.x+points[index].x,y:sum.y+points[index].y}),{x:0,y:0});return{x:total.x/indexes.length,y:total.y/indexes.length}}
function sfaceLandmarks(result){
  const points=result&&result.landmarks&&result.landmarks.positions;if(!Array.isArray(points)||points.length<55)return null;
  const eyes=[averageLandmarks(points,[36,37,38,39,40,41]),averageLandmarks(points,[42,43,44,45,46,47])].sort((a,b)=>a.x-b.x);
  const mouths=[points[48],points[54]].map(point=>({x:point.x,y:point.y})).sort((a,b)=>a.x-b.x);
  return[eyes[0],eyes[1],{x:points[30].x,y:points[30].y},mouths[0],mouths[1]];
}
function sfaceSimilarityTransform(source){
  const target=[{x:38.2946,y:51.6963},{x:73.5318,y:51.5014},{x:56.0252,y:71.7366},{x:41.5493,y:92.3655},{x:70.7299,y:92.2041}],sourceMean=source.reduce((sum,p)=>({x:sum.x+p.x/5,y:sum.y+p.y/5}),{x:0,y:0}),targetMean=target.reduce((sum,p)=>({x:sum.x+p.x/5,y:sum.y+p.y/5}),{x:0,y:0});
  let denominator=0,numeratorA=0,numeratorB=0;
  source.forEach((point,index)=>{const sx=point.x-sourceMean.x,sy=point.y-sourceMean.y,dx=target[index].x-targetMean.x,dy=target[index].y-targetMean.y;denominator+=sx*sx+sy*sy;numeratorA+=sx*dx+sy*dy;numeratorB+=sx*dy-sy*dx});
  if(!denominator)return null;
  const a=numeratorA/denominator,b=numeratorB/denominator;
  return{a,b,tx:targetMean.x-a*sourceMean.x+b*sourceMean.y,ty:targetMean.y-b*sourceMean.x-a*sourceMean.y};
}
function alignedSFaceCanvas(input,result){
  const landmarks=sfaceLandmarks(result),transform=landmarks&&sfaceSimilarityTransform(landmarks);if(!transform)throw new Error('无法定位完整五官');
  const canvas=document.createElement('canvas');canvas.width=112;canvas.height=112;const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';ctx.setTransform(transform.a,transform.b,-transform.b,transform.a,transform.tx,transform.ty);ctx.drawImage(input,0,0);ctx.setTransform(1,0,0,1,0,0);
  return canvas;
}
function alignedSFaceTensor(canvas){
  const ctx=canvas.getContext('2d',{willReadFrequently:true});
  const rgba=ctx.getImageData(0,0,112,112).data,plane=112*112,data=new Float32Array(plane*3);
  for(let pixel=0;pixel<plane;pixel++){data[pixel]=rgba[pixel*4];data[plane+pixel]=rgba[pixel*4+1];data[plane*2+pixel]=rgba[pixel*4+2]}
  return new ort.Tensor('float32',data,[1,3,112,112]);
}
function normalizedVector(values){const vector=Array.from(values,Number),norm=Math.sqrt(vector.reduce((sum,value)=>sum+value*value,0));if(!norm||!Number.isFinite(norm))throw new Error('人脸特征生成失败');return vector.map(value=>value/norm)}
function isStoredFaceDescriptor(sample){return(Array.isArray(sample)&&sample.length===128)||(typeof sample==='string'&&sample.indexOf('q16:')===0&&sample.length>300)}
function packFaceDescriptor(sample){
  if(typeof sample==='string'&&sample.indexOf('q16:')===0)return sample;
  let vector;try{vector=normalizedVector(sample)}catch(error){return''}
  let binary='';for(let i=0;i<128;i++){let value=Math.max(-32767,Math.min(32767,Math.round(vector[i]*32767)));if(value<0)value+=65536;binary+=String.fromCharCode((value>>8)&255,value&255)}
  return`q16:${btoa(binary)}`;
}
function unpackFaceDescriptor(sample){
  if(Array.isArray(sample))return sample.length===128?sample.map(Number):[];
  if(typeof sample!=='string'||sample.indexOf('q16:')!==0)return[];
  try{const binary=atob(sample.slice(4));if(binary.length!==256)return[];const vector=[];for(let i=0;i<128;i++){let value=(binary.charCodeAt(i*2)<<8)|binary.charCodeAt(i*2+1);if(value&32768)value-=65536;vector.push(value/32767)}return normalizedVector(vector)}catch(error){return[]}
}
function averageFaceDescriptor(samples){
  if(!samples.length)return[];const average=Array(128).fill(0);samples.forEach(sample=>{for(let i=0;i<128;i++)average[i]+=Number(sample[i]||0)});try{return normalizedVector(average)}catch(error){return[]}
}
function compactAnchorFaceSamples(samples){
  const stored=(Array.isArray(samples)?samples:[]).filter(isStoredFaceDescriptor);if(stored.length<=MAX_ANCHOR_FACE_SAMPLES&&stored.every(sample=>typeof sample==='string'&&sample.indexOf('q16:')===0))return stored.slice();
  const decoded=stored.map(unpackFaceDescriptor).filter(sample=>sample.length===128);if(!decoded.length)return[];
  let selected=decoded;if(decoded.length>MAX_ANCHOR_FACE_SAMPLES){const mean=averageFaceDescriptor(decoded),left=decoded[Math.min(1,decoded.length-1)],right=decoded[Math.max(0,decoded.length-2)];selected=[mean,left,right].filter(sample=>sample.length===128)}
  return selected.slice(0,MAX_ANCHOR_FACE_SAMPLES).map(packFaceDescriptor).filter(Boolean);
}
function compactAllFaceProfiles(){
  if(!state||!Array.isArray(state.students))return;
  state.students.forEach(student=>{student.sfaceDescriptors=compactAnchorFaceSamples(student.sfaceDescriptors);const adaptive=(Array.isArray(student.sfaceAdaptiveDescriptors)?student.sfaceAdaptiveDescriptors:[]).filter(isStoredFaceDescriptor).slice(-MAX_ADAPTIVE_FACE_SAMPLES);student.sfaceAdaptiveDescriptors=adaptive.every(sample=>typeof sample==='string'&&sample.indexOf('q16:')===0)?adaptive:adaptive.map(unpackFaceDescriptor).filter(sample=>sample.length===128).map(packFaceDescriptor).filter(Boolean)});
  state.settings.faceStorageVersion=2;
}
async function sfaceDescriptor(input,result){
  const canvas=alignedSFaceCanvas(input,result);
  if(nativeSFaceAvailable()){
    const base64=canvas.toDataURL('image/jpeg',.94).split(',')[1],response=JSON.parse(AndroidBridge.sfaceDescriptor(base64));
    if(!response||!response.ok||!Array.isArray(response.descriptor))throw new Error(response&&response.error||'安卓原生人脸模型运行失败');
    return normalizedVector(response.descriptor);
  }
  const session=await loadSFaceModel(),outputs=await session.run({data:alignedSFaceTensor(canvas)}),feature=outputs.fc1||outputs[session.outputNames[0]];return normalizedVector(feature.data);
}
async function readFaceDescriptor(input){
  await loadFaceModels();
  const results=await faceapi.detectAllFaces(input,new faceapi.TinyFaceDetectorOptions({inputSize:224,scoreThreshold:.65})).withFaceLandmarks(true);
  if(results.length!==1)return null;
  return{...results[0],descriptor:await sfaceDescriptor(input,results[0])};
}
function faceCosine(a,b){if(!a||!b||a.length!==b.length)return-1;let sum=0;for(let i=0;i<a.length;i++)sum+=Number(a[i])*Number(b[i]);return sum}
function learnFromConfirmedFace(student,candidates,confidence){
  if(!hasSFaceProfile(student)||!Array.isArray(candidates)||!candidates.length)return 0;
  if(Number(confidence&&confidence.score)<.56||Number(confidence&&confidence.margin)<.12)return 0;
  const anchors=(Array.isArray(student.sfaceDescriptors)?student.sfaceDescriptors:[]).map(unpackFaceDescriptor).filter(sample=>sample.length===128),adaptive=(Array.isArray(student.sfaceAdaptiveDescriptors)?student.sfaceAdaptiveDescriptors:[]).map(unpackFaceDescriptor).filter(sample=>sample.length===128);
  let added=0;
  for(const raw of candidates){
    const candidate=Array.from(raw||[]);if(candidate.length!==128)continue;
    const current=[...anchors,...adaptive],nearest=Math.max(...current.map(sample=>faceCosine(candidate,sample))),anchorNearest=Math.max(...anchors.map(sample=>faceCosine(candidate,sample)));
    if(nearest>.995||anchorNearest<.48)continue;
    adaptive.push(candidate);added++;if(added>=2)break;
  }
  if(!added)return 0;
  student.sfaceAdaptiveDescriptors=adaptive.slice(-MAX_ADAPTIVE_FACE_SAMPLES).map(packFaceDescriptor).filter(Boolean);student.faceLearningCount=Number(student.faceLearningCount||0)+1;student.lastFaceLearnedAt=new Date().toISOString();return added;
}
function clearFaceFrame(result,input){const box=result&&result.detection&&result.detection.box,width=Number(input.videoWidth||input.naturalWidth||input.width||0);return Boolean(box&&result.detection.score>=.72&&width&&box.width>=width*.2&&box.width<=width*.9)}
function fileAsDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('照片读取失败'));reader.readAsDataURL(file)})}
function loadPhoto(dataUrl){return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('照片格式无法识别'));image.src=dataUrl})}
function compressedPhoto(image){const maxSide=64,scale=Math.min(1,maxSide/Math.max(image.naturalWidth,image.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));canvas.getContext('2d').drawImage(image,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/jpeg',.5)}
async function faceProfileFromPhoto(file){if(!file||(!String(file.type||'').startsWith('image/')&&!/\.(jpe?g|png|webp)$/i.test(String(file.name||''))))throw new Error('请选择 JPG 或 PNG 学生照片');const image=await loadPhoto(await fileAsDataUrl(file)),result=await readFaceDescriptor(image);if(!result)throw new Error('照片中必须只有一张清晰人脸');if(!clearFaceFrame(result,image))throw new Error('照片人脸太小或不够清晰');return{faceImage:compressedPhoto(image),faceDescriptors:[],sfaceDescriptors:[Array.from(result.descriptor)],sfaceAdaptiveDescriptors:[],sfaceModelVersion:SFACE_MODEL_VERSION,faceSecurityVersion:SFACE_MODEL_VERSION,faceProfileSource:'photo',faceRegistered:true}}
function unmirrorStoredCameraPhoto(image){if(!image)return image;const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const ctx=canvas.getContext('2d');ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(image,0,0);return canvas}
async function prepareSFaceProfiles(students,status){
  let migrated=0;
  for(let index=0;index<students.length;index++){
    const student=students[index];if(hasSFaceProfile(student)||!student.faceImage)continue;
    if(status)status.textContent=`首次升级旧人脸资料 ${index+1}/${students.length} · ${student.name}`;
    try{const stored=await loadPhoto(student.faceImage),input=student.faceProfileSource==='photo'?stored:unmirrorStoredCameraPhoto(stored),result=await readFaceDescriptor(input);if(!result)continue;student.sfaceDescriptors=[packFaceDescriptor(result.descriptor)];student.sfaceAdaptiveDescriptors=[];student.sfaceModelVersion=SFACE_MODEL_VERSION;migrated++}catch(_){}
  }
  if(migrated)saveState();
  return students.filter(hasSFaceProfile);
}

function loadState(){
  try {
    let stored=localStorage.getItem(STORAGE_KEY);
    let canonicalStored=false;try{canonicalStored=JSON.parse(stored)?.managementSync?.enabled===true}catch(_){}
    if(!canonicalStored&&window.AndroidBridge&&typeof AndroidBridge.recoverLocalRepairCheckpoint==='function'){
      const approved=AndroidBridge.recoverLocalRepairCheckpoint(stored);
      if(approved){try{const checked=JSON.parse(approved);if(!Array.isArray(checked.students)||!Array.isArray(checked.attendance))throw Error('Invalid checkpoint');localStorage.setItem(STORAGE_KEY,approved);stored=approved;window.__localRepairRecovered=true}catch(_){}}
    }
    const parsed = JSON.parse(stored);
    if (parsed && Array.isArray(parsed.students) && Array.isArray(parsed.attendance)) return {...emptyState(), ...parsed, settings:{...emptyState().settings,...parsed.settings}};
  } catch (_) {}
  return emptyState();
}
function classReminderCalendar(course){
  const extra=new Set(classExtraLessons(course).map(lesson=>lesson.date)),cancelled=new Set();
  reschedulesForClass({classId:course.id}).forEach(change=>{cancelled.add(change.fromDate);extra.add(change.toDate);new Set([dateWeekStart(change.fromDate),dateWeekStart(change.toDate)]).forEach(week=>{const date=new Date(`${week}T12:00:00`);date.setDate(date.getDate()+change.regularWeekday-1);const label=localDate(date),profile=classScheduleProfile(course,label);if(regularLessonOnDate(profile,label,change.regularWeekday))extra.add(label);if(Number(course.scheduleWeekday)>=1&&Number(course.scheduleWeekday)<=7&&Number(course.scheduleWeekday)!==change.regularWeekday){const original=new Date(`${week}T12:00:00`);original.setDate(original.getDate()+Number(course.scheduleWeekday)-1);cancelled.add(localDate(original))}})});
  const personal=personalClassDates(course);personal.forEach(change=>extra.add(change.toDate));
  return{extraLessonDates:[...extra].sort(),cancelledLessonDates:[...cancelled].sort(),...(personal.length?{partialLessonDates:[...new Set(personal.map(change=>change.toDate))].filter(date=>!wholeClassHasLessonOnDate(course,date))}:{})};
}
function reminderSyncPayload(){
  const today=localDate(),yesterday=dateBefore(today);
  return {orgName:state.settings.orgName||'未来学校学生管理系统',classes:state.classes.map(course=>({id:course.id,name:course.name,teacher:course.teacher||'',classroom:course.classroom||'',scheduleWeekday:Number(course.scheduleWeekday||0),...lessonCycleSnapshot(course),scheduleStart:String(course.scheduleStart||''),reminderEnabled:Boolean(course.reminderEnabled),...classReminderCalendar(course)})),students:state.students.map(student=>({id:student.id,name:student.name,contact:student.contact||'',className:student.className||'',longTermAbsent:Boolean(student.longTermAbsent),...studentReminderCalendar(student)})),attendance:state.attendance.filter(record=>record.date>=yesterday&&record.date<=today).map(record=>({studentId:record.studentId,date:record.date}))};
}
function syncNativeReminders(){try{if(window.AndroidBridge&&typeof AndroidBridge.syncReminderState==='function')AndroidBridge.syncReminderState(JSON.stringify(reminderSyncPayload()))}catch(_){}}
function nativeBackupInfo(){try{if(window.AndroidBridge&&typeof AndroidBridge.getAutoBackupInfo==='function')return JSON.parse(AndroidBridge.getAutoBackupInfo()||'{}')}catch(_){}return{}}
function createNativeBackup(reason='每日自动备份',force=false){try{if(!state.students.length&&!state.attendance.length&&!state.classes.length)return false;if(window.AndroidBridge&&typeof AndroidBridge.createAutoBackup==='function'&&(force||typeof AndroidBridge.shouldAutoBackup!=='function'||AndroidBridge.shouldAutoBackup())){return AndroidBridge.createAutoBackup(JSON.stringify(state),reason,Boolean(force))!==false}}catch(_){}return false}
function requireSafetyBackup(reason){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive()&&(reason==='恢复前备份'||reason==='清空前备份')){toast('网页主管理模式下不能在平板清空或恢复旧备份，请由管理员按迁移流程处理',true);return false;}
  if(reason==='恢复前备份'||reason==='清空前备份'){try{window.AndroidBridge?.pauseWebMirror()}catch(_){}}
  if(!state.students.length&&!state.classes.length&&!state.attendance.length&&!state.transactions.length)return true;
  try{
    if(window.AndroidBridge&&typeof AndroidBridge.createSafetyBackup==='function'){
      const result=JSON.parse(AndroidBridge.createSafetyBackup(JSON.stringify(state),reason)||'{}');
      if(result.ok)return true;
      toast(`备份未完成，操作已停止：${result.error||'请稍后重试'}`,true);return false;
    }
  }catch(error){toast('备份失败，操作已停止，原有资料保持不变',true);return false}
  toast('当前环境无法确认备份完成，请在更新后的安卓 App 中执行此操作',true);return false;
}
function validateBackupState(data){
  if(!data||typeof data!=='object'||!Array.isArray(data.students)||!Array.isArray(data.attendance))throw new Error('备份缺少学生或签到数据');
  ['classes','transactions'].forEach(key=>{if(data[key]!==undefined&&!Array.isArray(data[key]))throw new Error(`备份中的${key==='classes'?'班级':'课时记录'}格式错误`)});
  ['students','classes','attendance','transactions'].forEach(key=>{
    const ids=new Set();(data[key]||[]).forEach(item=>{
      if(!item||typeof item!=='object'||!Number.isSafeInteger(item.id)||item.id<=0)throw new Error('备份含有无效的记录编号');
      if(ids.has(item.id))throw new Error('备份含有重复的记录编号，请核对备份来源');ids.add(item.id);
      if(key==='students'&&(typeof item.name!=='string'||!item.name.trim()||item.hours===null||item.hours===''||!Number.isFinite(Number(item.hours))))throw new Error('备份含有无效的学生姓名或课时');
      if(key==='classes'&&(typeof item.name!=='string'||!item.name.trim()))throw new Error('备份含有无效的班级名称');
      if((key==='attendance'||key==='transactions')&&(!Number.isSafeInteger(item.studentId)||!isValidLocalDate(item.date)))throw new Error('备份含有无效的学生编号或记录日期');
      if(key==='transactions'&&!Number.isFinite(Number(item.amount)))throw new Error('备份含有无效的课时变化');
    });
  });
  const studentIds=new Set(data.students.map(student=>student.id)),studentNos=new Set(),attendanceById=new Map(),attendanceDates=new Set(),linkedAttendance=new Set();
  data.students.forEach(student=>{
    const number=String(student.studentNo||'').trim();if(number&&studentNos.has(number))throw new Error('备份含有重复学号，请先核对学生档案');if(number)studentNos.add(number);
    ['enrollmentHistory','longTermAbsenceHistory'].forEach(key=>{if(student[key]!==undefined&&!Array.isArray(student[key]))throw new Error('备份中的学生历史记录格式错误');(student[key]||[]).forEach(entry=>{if(!entry||typeof entry!=='object'||entry.startDate&&!isValidLocalDate(entry.startDate)||entry.endDate&&!isValidLocalDate(entry.endDate)||entry.startDate&&entry.endDate&&entry.startDate>entry.endDate)throw new Error('备份含有无效的在读或暂停日期区间')})});
  });
  data.attendance.forEach(record=>{if(!studentIds.has(record.studentId))throw new Error('备份中有签到记录找不到对应学生');const key=`${record.studentId}:${record.date}`;if(attendanceDates.has(key))throw new Error('备份含有同一学生同一天的重复签到');attendanceDates.add(key);attendanceById.set(record.id,record)});
  (data.transactions||[]).forEach(record=>{
    if(!studentIds.has(record.studentId))throw new Error('备份中有课时记录找不到对应学生');
    if(record.amount===null||record.amount===''||!Number.isFinite(Number(record.amount)))throw new Error('备份含有无效的课时变化');
    if(record.attendanceId!==undefined&&record.attendanceId!==null&&record.attendanceId!==''){
      const attendance=attendanceById.get(record.attendanceId);if(!attendance||attendance.studentId!==record.studentId||attendance.date!==record.date||Number(record.amount)!==-1||linkedAttendance.has(record.attendanceId))throw new Error('备份中的签到扣课关联不一致');linkedAttendance.add(record.attendanceId);
    }
  });
  if(data.settings!==undefined&&(!data.settings||typeof data.settings!=='object'||Array.isArray(data.settings)))throw new Error('备份设置格式错误');
  if(data.settings&&(data.settings.adminPattern!=null||data.settings.adminAuthMode==='pattern')&&!validPatternRecord(data.settings.adminPattern))throw new Error('备份中的图案解锁资料无效');
  if(data.settings&&data.settings.extraClassLessons!==undefined){
    if(!Array.isArray(data.settings.extraClassLessons))throw new Error('备份中的加课日期格式错误');
    const dates=new Set();data.settings.extraClassLessons.forEach(lesson=>{if(!lesson||!Number.isSafeInteger(lesson.classId)||lesson.classId<=0||!isValidLocalDate(lesson.date)||typeof lesson.className!=='string'||!lesson.className.trim()||lesson.note!==undefined&&typeof lesson.note!=='string')throw new Error('备份含有无效的加课安排');const key=`${lesson.classId}:${lesson.date}`;if(dates.has(key))throw new Error('备份含有同一班级同一天的重复加课');dates.add(key)});
  }
  if(data.settings&&data.settings.classReschedules!==undefined){
    if(!Array.isArray(data.settings.classReschedules))throw new Error('备份中的调课安排格式错误');
    const endpoints=new Set(),weekdays=new Map();data.settings.classReschedules.forEach(change=>{
      if(!change||!Number.isSafeInteger(change.classId)||change.classId<=0||typeof change.className!=='string'||!change.className.trim()||!isValidLocalDate(change.fromDate)||!isValidLocalDate(change.toDate)||change.fromDate===change.toDate||!Number.isInteger(change.regularWeekday)||change.regularWeekday<1||change.regularWeekday>7||change.note!==undefined&&typeof change.note!=='string')throw new Error('备份含有无效的调课日期或常规星期');
      const extras=data.settings.extraClassLessons||[],hasSourceExtra=extras.some(lesson=>lesson.classId===change.classId&&lesson.date===change.fromDate),source=change.sourceSchedule||{scheduleWeekday:change.regularWeekday},target=change.targetSchedule||{scheduleWeekday:change.regularWeekday};if(regularLessonOnDate(target,change.toDate,Number(target.scheduleWeekday))||extras.some(lesson=>lesson.classId===change.classId&&lesson.date===change.toDate)||!hasSourceExtra&&!regularLessonOnDate(source,change.fromDate,Number(source.scheduleWeekday)))throw new Error('备份中的调课与常规课、隔周周期或加课日期冲突');
      [change.fromDate,change.toDate].forEach(date=>{const key=`${change.classId}:${date}`;if(endpoints.has(key))throw new Error('备份含有相互冲突的调课日期');endpoints.add(key);const weekKey=`${change.classId}:${dateWeekStart(date)}`;if(weekdays.has(weekKey)&&weekdays.get(weekKey)!==change.regularWeekday)throw new Error('备份中同一班级同周的常规星期不一致');weekdays.set(weekKey,change.regularWeekday)});
    });
  }
  validateCycleData(data);validateClassLessonTiming(data);validateStudentLeaves(data);validateCourseGroups(data);
  validateStudentReschedules(data);
  validateWechatNotices(data);
  if(typeof validateDeviceSyncBackup==='function')validateDeviceSyncBackup(data);
  const restored={...emptyState(),...data,classes:data.classes||[],transactions:data.transactions||[],settings:{...emptyState().settings,...data.settings}};
  delete restored.deviceSync;
  if(restored.settings.adminPattern){delete restored.settings.adminPassword;delete restored.settings.adminUser}return restored;
}
function restoreSnapshotValue(target,saved){
  if(Array.isArray(saved)){
    const current=Array.isArray(target)?target:[],byId=new Map();current.forEach(item=>{if(item&&typeof item==='object'&&item.id!==undefined){if(!byId.has(item.id))byId.set(item.id,[]);byId.get(item.id).push(item)}});
    const items=saved.map((item,index)=>restoreSnapshotValue(item&&typeof item==='object'&&item.id!==undefined?(byId.get(item.id)||[]).shift():current[index],item));
    current.splice(0,current.length);items.forEach(item=>current.push(item));return current;
  }
  if(saved&&typeof saved==='object'){
    const current=target&&typeof target==='object'&&!Array.isArray(target)?target:{};
    Object.keys(current).forEach(key=>{if(!Object.prototype.hasOwnProperty.call(saved,key))delete current[key]});
    Object.keys(saved).forEach(key=>{if(key!=='__proto__'&&key!=='constructor'&&key!=='prototype')current[key]=restoreSnapshotValue(current[key],saved[key])});return current;
  }
  return saved;
}
function persistCurrentState(){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive()){if(!canonicalOrdinarySave())throw Error('canonical_save_rejected');return true;}
  const serialized=JSON.stringify(state);localStorage.setItem(STORAGE_KEY,serialized);committedStateSnapshot=serialized;return true;
}
function rollbackUnsavedState(){state=restoreSnapshotValue(state,JSON.parse(committedStateSnapshot));invalidatePageCache()}
function saveState(){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive())return canonicalOrdinarySave();
  invalidatePageCache();
  syncStudentNoCounters();compactAllFaceProfiles();
  if(state.deviceSync&&state.deviceSync.captureEnabled&&typeof prepareDeviceSyncForSave==='function')prepareDeviceSyncForSave(JSON.parse(committedStateSnapshot));
  let saved=false,releasedPhotos=0,releasedLearning=0,releasedThumbnails=0;
  try{saved=persistCurrentState()}catch(error){}
  if(!saved){
    state.students.forEach(student=>{if(hasSFaceProfile(student)&&student.faceImage&&student.faceImage.length>MAX_FACE_THUMBNAIL_LENGTH){student.faceImage='';releasedPhotos++}});
    try{saved=persistCurrentState()}catch(error){}
  }
  if(!saved){
    state.students.forEach(student=>{if(Array.isArray(student.sfaceAdaptiveDescriptors)&&student.sfaceAdaptiveDescriptors.length){student.sfaceAdaptiveDescriptors=[];releasedLearning++}});
    try{saved=persistCurrentState()}catch(error){}
  }
  if(!saved){
    state.students.forEach(student=>{if(hasSFaceProfile(student)&&student.faceImage){student.faceImage='';releasedThumbnails++}});
    try{saved=persistCurrentState()}catch(error){}
  }
  if(!saved){rollbackUnsavedState();toast('保存失败，本次修改已撤回，原有数据和课时不变；请释放存储空间后重试',true);return false}
  if(releasedPhotos||releasedThumbnails)toast(`已自动释放存储空间：人脸识别特征完整保留，清理了 ${releasedPhotos+releasedThumbnails} 张头像缩略图`);
  else if(releasedLearning)toast('已自动释放人脸学习缓存，基础人脸资料仍然保留');
  syncNativeReminders();createNativeBackup('每日自动备份',false);if(typeof scheduleDeviceSync==='function')scheduleDeviceSync();if(typeof scheduleWebMirror==='function')scheduleWebMirror();return true;
}
async function optimizeExistingFacePhotos(){
  let optimized=0;
  for(let index=0;index<state.students.length;index++){
    const student=state.students[index];if(!hasSFaceProfile(student)||!student.faceImage||student.faceImage.length<=MAX_FACE_THUMBNAIL_LENGTH)continue;
    try{const image=await loadPhoto(student.faceImage),thumbnail=compressedPhoto(image);if(thumbnail.length<student.faceImage.length){student.faceImage=thumbnail;optimized++}}catch(error){}
    if(index%12===0)await delay(0);
  }
  if(optimized)saveState();
  return optimized;
}
window.onNativeBackupCompleted=resultJson=>{let result={};try{result=typeof resultJson==='string'?JSON.parse(resultJson):resultJson||{}}catch(_){}if(result.manual)toast(result.ok?`完整备份已保存：${result.fileName||'备份文件'}`:`自动备份失败：${result.error||'请稍后重试'}`,!result.ok);const status=$('#autoBackupStatus');if(status&&result.ok)status.textContent=`最近备份：${result.timeLabel||'刚刚'} · ${result.fileName||''}`};
function weekStart(){ return calendarWeekRange(0).start; }
function weekAttendance(){ return calendarWeekAttendance(0); }
function todayAttendance(){ const today=localDate();return state.attendance.filter(a => a.date === today); }
function studentById(id){
  if(studentLookupSource!==state.students||studentLookupLength!==state.students.length||studentLookupRevision!==dataRevision){
    studentLookup=new Map();state.students.forEach(student=>{if(!studentLookup.has(student.id))studentLookup.set(student.id,student)});
    studentLookupSource=state.students;studentLookupLength=state.students.length;studentLookupRevision=dataRevision;
  }
  return studentLookup.get(Number(id));
}
function classById(id){ return state.classes.find(c => c.id === Number(id)); }
function isLongTermAbsent(student){return Boolean(student&&student.longTermAbsent)}
function reportableStudents(students=state.students){return students.filter(student=>!isLongTermAbsent(student))}
function isLongTermAbsentAt(student,date){const history=Array.isArray(student.longTermAbsenceHistory)?student.longTermAbsenceHistory:[];if(history.length)return history.some(item=>String(item.startDate||'2000-01-01')<=date&&(!item.endDate||item.endDate>=date));return isLongTermAbsent(student)&&(!student.longTermAbsentSince||student.longTermAbsentSince<=date)}
function reportableStudentsAt(date){return state.students.filter(student=>!isLongTermAbsentAt(student,date)).map(student=>enrolledStudentProfileAtDate(student,date)).filter(Boolean)}
function attendanceForStudents(records,students){const ids=new Set(students.map(student=>student.id));return records.filter(record=>ids.has(record.studentId))}
function parseImportedLongTermStatus(value){
  const normalized=String(value===null||value===undefined?'':value).trim().replace(/\s+/g,'').toLowerCase();
  if(!normalized||['否','正常','正常在读','在读','0','false','n','no'].includes(normalized))return{valid:true,value:false};
  if(['是','长期不来','长期不来学生','长期','暂停','停课','休学','1','true','y','yes','√','✓'].includes(normalized))return{valid:true,value:true};
  return{valid:false,value:false};
}
function studentFacingTeacherName(value){
  const compact=String(value||'').trim().replace(/\s+/g,'').replace(/老师$/,'');if(!compact)return'未设置老师';
  const compoundSurnames=['欧阳','太史','端木','上官','司马','东方','独孤','南宫','万俟','闻人','夏侯','诸葛','尉迟','公羊','赫连','澹台','皇甫','宗政','濮阳','公冶','太叔','申屠','公孙','慕容','仲孙','钟离','长孙','宇文','司徒','鲜于','司空','闾丘','子车','亓官','司寇','巫马','公西','颛孙','壤驷','公良','漆雕','乐正','宰父','谷梁','拓跋','夹谷','轩辕','令狐','段干','百里','呼延','东郭','南门','羊舌','微生','梁丘','左丘','东门','西门'];
  const surname=compoundSurnames.find(item=>compact.startsWith(item))||Array.from(compact)[0];return`${surname}老师`;
}
function groupBy(items,key){ return items.reduce((out,item) => { const k=item[key] || '未填写';if(!out[k])out[k]=[];out[k].push(item); return out; },{}); }
function normalizedGroupLabel(value){ return String(value||'未填写').trim().replace(/\s+/g,' ')||'未填写'; }
function groupByNormalized(items,key){
  return items.reduce((out,item)=>{const label=normalizedGroupLabel(item[key]);if(!out[label])out[label]=[];if(!out[label].some(existing=>existing.id===item.id))out[label].push(item);return out},{});
}
function teacherStudentGroups(students=state.students){
  return students.reduce((out,student)=>{const teacher=reportTeacherForProfile(student);if(!out[teacher])out[teacher]=[];if(!out[teacher].some(existing=>existing.id===student.id))out[teacher].push(student);return out},{});
}
// Class management owns teacher attribution. Historical enrollment still owns
// which class a student attended; never substitute the student's new class.
// An explicit deleted class ID must not match an unrelated reused class name.
function reportCourseForProfile(profile){return profile.classId?classById(profile.classId):state.classes.find(course=>normalizedGroupLabel(course.name)===normalizedGroupLabel(profile.className))}
function reportTeacherForProfile(profile){const course=reportCourseForProfile(profile);return normalizedGroupLabel(course?course.teacher:profile.teacher)}
function reportAttendanceProfile(student,record){
  const historical=studentProfileAtDate(student,record.date),sameClass=!record.className||normalizedGroupLabel(record.className)===normalizedGroupLabel(historical.className),classId=record.classId||(sameClass?historical.classId:'');
  return {...historical,classId,className:record.className||historical.className,teacher:record.teacher||historical.teacher,classroom:record.classroom||historical.classroom};
}
function extraClassLessons(){return Array.isArray(state.settings.extraClassLessons)?state.settings.extraClassLessons:[]}
function classExtraLessons(course){return extraClassLessons().filter(lesson=>String(lesson.classId)===String(course.id)).slice().sort((a,b)=>b.date.localeCompare(a.date))}
function hasExtraClassLesson(profile,date){const course=profile.classId?null:reportCourseForProfile(profile),classId=profile.classId||course&&course.id;return Boolean(classId&&extraClassLessons().some(lesson=>String(lesson.classId)===String(classId)&&lesson.date===date))}
function dateWeekStart(date){const day=new Date(`${date}T12:00:00`);day.setDate(day.getDate()-((day.getDay()+6)%7));return localDate(day)}
function profileClassId(profile){const course=profile.classId?null:reportCourseForProfile(profile);return String(profile.classId||course&&course.id||'')}
function classReschedules(){return Array.isArray(state.settings.classReschedules)?state.settings.classReschedules:[]}
function reschedulesForClass(profile){const id=profileClassId(profile);return classReschedules().filter(change=>String(change.classId)===id)}
function movedLessonAt(profile,date){return reschedulesForClass(profile).find(change=>change.toDate===date)}
function cancelledLessonAt(profile,date){return reschedulesForClass(profile).find(change=>change.fromDate===date)}
function effectiveClassWeekday(profile,date){const week=dateWeekStart(date),change=reschedulesForClass(profile).find(item=>dateWeekStart(item.fromDate)===week||dateWeekStart(item.toDate)===week);return Number(change?change.regularWeekday:profile.scheduleWeekday||0)}
function wholeClassHasLessonOnDate(course,date){const profile=classScheduleProfile(course,date);return !cancelledLessonAt(profile,date)&&(regularLessonOnDate(profile,date,effectiveClassWeekday(profile,date))||hasExtraClassLesson(profile,date)||Boolean(movedLessonAt(profile,date)))}
function classHasLessonOnDate(course,date){return wholeClassHasLessonOnDate(course,date)||classHasPersonalLessonOnDate(course,date)}
function saveClassReschedule(course,fromDate,toDate,regularWeekday,note=''){
  const weekday=Number(regularWeekday);if(!course||!classById(course.id))return{ok:false,error:'班级已变化，请重新打开'};
  if(!isValidLocalDate(fromDate)||!isValidLocalDate(toDate)||fromDate===toDate)return{ok:false,error:'请选择两个不同的有效调课日期'};
  if(!Number.isInteger(weekday)||weekday<1||weekday>7)return{ok:false,error:'请确认调课涉及两周的常规上课星期'};
  if(classLeaveConflict(course,[fromDate,toDate]))return{ok:false,error:'原日期或新日期已有请假，请先核对并撤销相关请假，再调课'};if(personalClassScheduleConflict(course,fromDate,toDate))return{ok:false,error:'这些日期已有个人调课，请先核对并撤销相关个人安排，再登记整班调课'};
  const profile=classScheduleProfile(course,fromDate),targetProfile=classScheduleProfile(course,toDate),sourceExtra=hasExtraClassLesson(profile,fromDate),changes=reschedulesForClass(profile),weeks=new Set([dateWeekStart(fromDate),dateWeekStart(toDate)]);
  if(lessonCycleWeeks(profile)===2&&Number(profile.scheduleWeekday)!==weekday)return{ok:false,error:'隔周班的常规星期须与原日期有效排课一致'};
  if(!sourceExtra&&!regularLessonOnDate(profile,fromDate,weekday))return{ok:false,error:'原日期不是本班实际有课的日期（可能为隔周休课日），请核对；额外加课请先录入原日期'};
  if(regularLessonOnDate(targetProfile,toDate,lessonCycleWeeks(targetProfile)===2?Number(targetProfile.scheduleWeekday):weekday)||hasExtraClassLesson(profile,toDate))return{ok:false,error:'新日期已有本班课程，同一学生同一天只支持一次签到，请选择另一日期'};
  if(changes.some(change=>[change.fromDate,change.toDate].some(date=>date===fromDate||date===toDate)))return{ok:false,error:'该日期已参与其他调课，请先撤销原安排再重新登记'};
  if(changes.some(change=>(weeks.has(dateWeekStart(change.fromDate))||weeks.has(dateWeekStart(change.toDate)))&&change.regularWeekday!==weekday))return{ok:false,error:'同一班级同一周的常规星期须保持一致'};
  state.settings.classReschedules=classReschedules().concat({classId:course.id,className:course.name,teacher:course.teacher||'',fromDate,toDate,regularWeekday:weekday,sourceExtra,sourceSchedule:{scheduleWeekday:weekday,...lessonCycleSnapshot(profile)},targetSchedule:{scheduleWeekday:lessonCycleWeeks(targetProfile)===2?Number(targetProfile.scheduleWeekday):weekday,...lessonCycleSnapshot(targetProfile)},note:String(note||'').trim().slice(0,100),createdAt:new Date().toISOString()});
  return saveState()?{ok:true}:{ok:false,error:'调课保存失败，原有安排未改变'};
}
function saveExtraClassLesson(course,date,note=''){
  if(!course||!classById(course.id))return{ok:false,error:'班级已变化，请重新打开'};
  if(!isValidLocalDate(date))return{ok:false,error:'请选择有效的实际加课日期'};
  if(reschedulesForClass({classId:course.id}).some(change=>change.fromDate===date||change.toDate===date))return{ok:false,error:'该日期已参与调课，请在调课安排中管理'};
  if(classExtraLessons(course).some(lesson=>lesson.date===date))return{ok:false,error:'该班该日期已录入，不会重复计算'};
  state.settings.extraClassLessons=extraClassLessons().concat({classId:course.id,className:course.name,teacher:course.teacher||'',date,note:String(note||'').trim().slice(0,100),createdAt:new Date().toISOString()});
  return saveState()?{ok:true}:{ok:false,error:'加课日期保存失败，原有安排未改变'};
}
function isValidLocalDate(value){return /^\d{4}-\d{2}-\d{2}$/.test(String(value||''))&&localDate(new Date(`${value}T12:00:00`))===value}
function studentCreatedDate(student){const value=String(student.createdAt||'');if(!value)return'';const parsed=new Date(value);return value.includes('T')&&!isNaN(parsed.getTime())?localDate(parsed):value.slice(0,10)}
function dateBefore(value){const date=new Date(`${value}T12:00:00`);date.setDate(date.getDate()-1);return localDate(date)}
function courseForStudent(student){return state.classes.find(course=>normalizedGroupLabel(course.name)===normalizedGroupLabel(student&&student.className))}
function enrollmentSnapshot(student,course=courseForStudent(student)){return{classId:course&&course.id||'',className:course&&course.name||student&&student.className||'',teacher:course&&course.teacher||student&&student.teacher||'',classroom:course&&course.classroom||'',scheduleWeekday:Number(course&&course.scheduleWeekday||0),...lessonCycleSnapshot(course||{})}}
function ensureEnrollmentHistory(){
  state.students.forEach(student=>{if(!Array.isArray(student.enrollmentHistory))student.enrollmentHistory=[];if(!student.enrollmentHistory.length&&(!isLongTermAbsent(student)||student.className)){const snapshot=enrollmentSnapshot(student),created=studentCreatedDate(student);student.enrollmentHistory=[{...snapshot,startDate:/^\d{4}-\d{2}-\d{2}$/.test(created)?created:'2000-01-01',endDate:''}]}if(!Array.isArray(student.longTermAbsenceHistory))student.longTermAbsenceHistory=isLongTermAbsent(student)?[{startDate:student.longTermAbsentSince||'2000-01-01',endDate:''}]:[]});
  state.students.forEach(student=>(student.enrollmentHistory||[]).forEach(entry=>{if(entry.scheduleWeekday===undefined){const course=entry.classId?classById(entry.classId):state.classes.find(item=>normalizedGroupLabel(item.name)===normalizedGroupLabel(entry.className));entry.scheduleWeekday=Number(course&&course.scheduleWeekday||0)}}));
}
function closeStudentEnrollment(student,date=localDate()){
  if(!Array.isArray(student.enrollmentHistory))student.enrollmentHistory=[];
  const open=[...student.enrollmentHistory].reverse().find(item=>!item.endDate);if(!open)return;
  if(open.startDate===date)student.enrollmentHistory.splice(student.enrollmentHistory.indexOf(open),1);else open.endDate=dateBefore(date);
}
function markStudentLongTerm(student,date=localDate()){
  if(!student)return;
  const course=courseForStudent(student);student.pausedClassId=course&&course.id||student.pausedClassId||'';student.pausedClassName=course&&course.name||student.className||student.pausedClassName||'';student.pausedTeacher=course&&course.teacher||student.teacher||student.pausedTeacher||'';student.pausedClassroom=course&&course.classroom||student.pausedClassroom||'';
  closeStudentEnrollment(student,date);if(!Array.isArray(student.longTermAbsenceHistory))student.longTermAbsenceHistory=[];if(!student.longTermAbsenceHistory.some(item=>!item.endDate))student.longTermAbsenceHistory.push({startDate:date,endDate:''});student.longTermAbsent=true;student.longTermAbsentSince=date;student.className='';student.teacher='';
}
function restoreStudentToCourse(student,course,date=localDate()){
  if(!student||!course)return false;if(!Array.isArray(student.longTermAbsenceHistory))student.longTermAbsenceHistory=[];
  const open=[...student.longTermAbsenceHistory].reverse().find(item=>!item.endDate);if(open){if(open.startDate===date)student.longTermAbsenceHistory.splice(student.longTermAbsenceHistory.indexOf(open),1);else open.endDate=dateBefore(date)}
  student.longTermAbsent=false;student.longTermAbsentSince='';student.className=course.name;student.teacher=course.teacher||student.pausedTeacher||'';updateStudentEnrollment(student,course,date);return true;
}
function normalizeLongTermStudentAssignments(){
  state.students.forEach(student=>{if(!isLongTermAbsent(student))return;if(student.className||student.teacher){markStudentLongTerm(student,student.longTermAbsentSince||localDate())}else{closeStudentEnrollment(student,student.longTermAbsentSince||localDate());if(!Array.isArray(student.longTermAbsenceHistory)||!student.longTermAbsenceHistory.length){student.longTermAbsenceHistory=[{startDate:student.longTermAbsentSince||'2000-01-01',endDate:''}]}}});
}
function studentProfileAtDate(student,date=localDate()){
  const history=Array.isArray(student.enrollmentHistory)?student.enrollmentHistory:[],entry=history.filter(item=>String(item.startDate||'2000-01-01')<=date&&(!item.endDate||item.endDate>=date)).sort((a,b)=>String(b.startDate).localeCompare(String(a.startDate)))[0],profile=entry||enrollmentSnapshot(student);
  return {...student,classId:profile.classId||'',className:profile.className||student.className||'',teacher:profile.teacher||student.teacher||'',classroom:profile.classroom||'',scheduleWeekday:Number(profile.scheduleWeekday||0),...lessonCycleSnapshot(profile)};
}
function updateStudentEnrollment(student,course,date=localDate()){
  if(!Array.isArray(student.enrollmentHistory))student.enrollmentHistory=[];
  const next=enrollmentSnapshot({...student,className:course&&course.name||student.className,teacher:course&&course.teacher||student.teacher},course),open=[...student.enrollmentHistory].reverse().find(item=>!item.endDate);
  const unchanged=open&&String(open.classId||'')===String(next.classId||'')&&normalizedGroupLabel(open.className)===normalizedGroupLabel(next.className)&&normalizedGroupLabel(open.teacher)===normalizedGroupLabel(next.teacher)&&String(open.classroom||'')===String(next.classroom||'')&&Number(open.scheduleWeekday||0)===next.scheduleWeekday&&lessonCycleWeeks(open)===lessonCycleWeeks(next)&&lessonCycleSnapshot(open).scheduleAnchorDate===lessonCycleSnapshot(next).scheduleAnchorDate;if(unchanged)return;
  if(open){if(open.startDate===date)Object.assign(open,next);else open.endDate=dateBefore(date)}
  if(!open||open.startDate!==date)student.enrollmentHistory.push({...next,startDate:date,endDate:''});
}
function attendanceSnapshotForStudent(student,date=localDate()){const profile=studentProfileAtDate(student,date);return{classId:profile.classId||'',className:profile.className||'',teacher:profile.teacher||'',classroom:profile.classroom||''}}
function attendanceMethodLabel(method){return method==='老师补签'?'老师补签':`${method||'未知'}签到`}
function enrolledStudentProfileAtDate(student,date){
  const history=Array.isArray(student.enrollmentHistory)?student.enrollmentHistory:[];
  if(history.length&&!history.some(item=>String(item.startDate||'2000-01-01')<=date&&(!item.endDate||item.endDate>=date)))return null;
  const created=studentCreatedDate(student);if(!history.length&&/^\d{4}-\d{2}-\d{2}$/.test(created)&&created>date)return null;
  const profile=studentProfileAtDate(student,date);return profile.className?profile:null;
}
function makeupClassGroupsAt(date){
  if(!isValidLocalDate(date))return[];
  const checkedIds=new Set(state.attendance.filter(record=>record.date===date).map(record=>Number(record.studentId))),groups=new Map();
  state.students.forEach(student=>{if(isLongTermAbsentAt(student,date)||checkedIds.has(Number(student.id)))return;const profile=enrolledStudentProfileAtDate(student,date);if(!profile||studentLeaveAt(profile,date))return;const key=profile.classId?`id:${profile.classId}`:`name:${normalizedGroupLabel(profile.className)}`;if(!groups.has(key))groups.set(key,{key,classId:profile.classId||'',className:profile.className,teacher:profile.teacher||'',classroom:profile.classroom||'',members:[]});groups.get(key).members.push({student,profile})});
  return [...groups.values()].sort((a,b)=>a.className.localeCompare(b.className,'zh-CN')).map(group=>({...group,members:group.members.sort((a,b)=>a.student.name.localeCompare(b.student.name,'zh-CN'))}));
}
function recordMakeupAttendance(studentIds,date,note=''){
  if(!isValidLocalDate(date)||date>localDate())return{ok:false,error:'请选择今天或更早的有效日期'};
  const requested=new Set(studentIds.map(Number)),available=makeupClassGroupsAt(date).reduce((items,group)=>items.concat(group.members),[]),selected=available.filter(item=>requested.has(Number(item.student.id)));
  if(!selected.length)return{ok:false,error:'请选择需要补签的学生'};
  if(selected.length!==requested.size)return{ok:false,error:'所选名单已变化，请重新选择需要补签的学生'};
  const invalid=selected.filter(item=>!Number.isFinite(Number(item.student.hours)));if(invalid.length)return{ok:false,error:`${invalid[0].student.name} 课时无效，请先核对资料`};
  const now=new Date(),time=now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),recordedAt=now.toISOString(),cleanNote=String(note||'').trim().slice(0,100);
  selected.forEach(item=>{const student=item.student,attendanceId=uid(),snapshot={classId:item.profile.classId||'',className:item.profile.className||'',teacher:item.profile.teacher||'',classroom:item.profile.classroom||''};student.hours=Number(student.hours)-1;state.attendance.push({id:attendanceId,studentId:student.id,date,time,method:'老师补签',makeup:true,recordedAt,note:cleanNote,...snapshot});state.transactions.push({id:uid(),attendanceId,studentId:student.id,amount:-1,reason:`老师补签扣减（${date}）`,date,time,recordedAt,...snapshot})});
  state.attendance.filter(record=>record.makeup&&record.recordedAt===recordedAt&&requested.has(record.studentId)).forEach(record=>prepareWechatAttendance(record,studentById(record.studentId)));
  if(!saveState())return{ok:false,error:'补签保存失败，本次课时和签到记录已撤回'};
  listPages.attendance=1;return{ok:true,count:selected.length};
}
function ensureAttendanceSnapshots(){state.attendance.forEach(record=>{if(record.className!==undefined&&record.teacher!==undefined)return;const student=studentById(record.studentId),profile=student?studentProfileAtDate(student,record.date||localDate()):{};record.classId=profile.classId||'';record.className=profile.className||'';record.teacher=profile.teacher||'';record.classroom=profile.classroom||''})}
function calendarWeekRange(offset=0){
  const start=new Date();start.setHours(12,0,0,0);start.setDate(start.getDate()-((start.getDay()+6)%7)+(Number(offset)||0)*7);
  const end=new Date(start);end.setDate(end.getDate()+6);
  return {offset:Number(offset)||0,start:localDate(start),end:localDate(end)};
}
function calendarWeekAttendance(offset=0){const range=calendarWeekRange(offset);return state.attendance.filter(a=>a.date>=range.start&&a.date<=range.end)}
function newWeeklyGroup(name){return{name,members:new Map(),expectedByStudent:new Map(),expected:0,records:[],leaves:new Map(),sessions:new Map()}}
function weeklySessionKey(profile,date=''){return `${profileClassId(profile)||'name:'+normalizedGroupLabel(profile.className)}:${date||'unscheduled'}`}
function weeklySession(group,profile,date,kind,change){
  const key=weeklySessionKey(profile,date);if(!group.sessions.has(key))group.sessions.set(key,{key,classId:profile.classId||'',className:profile.className,teacher:reportTeacherForProfile(profile),date:date||'',kind,fromDate:change&&change.fromDate||'',toDate:change&&change.toDate||'',expected:0,records:[],leaves:new Map(),members:new Map()});
  const session=group.sessions.get(key);if(kind==='cancelled')Object.assign(session,{kind,fromDate:change.fromDate,toDate:change.toDate});return session;
}
// One source of truth for dashboard, class cards, weekly reports and calendar.
// Scheduled classes count actual lesson days; legacy unscheduled enrollment uses
// weeklyTarget / 7 for each active day. Keep this fallback explicit in the UI.
function buildWeeklyStats(offset=0){
  const range=calendarWeekRange(offset),groups={school:new Map(),class:new Map(),teacher:new Map()},all=newWeeklyGroup('全校'),fallbackTarget=Number(state.settings.weeklyTarget||2);
  let fallback=false;
  const bucket=(type,name)=>{const label=normalizedGroupLabel(name);if(!groups[type].has(label))groups[type].set(label,newWeeklyGroup(label));return groups[type].get(label)};
  const buckets=profile=>[all,bucket('school',profile.school),bucket('class',profile.className),bucket('teacher',reportTeacherForProfile(profile))];
  for(let index=0;index<7;index++){
    const day=new Date(`${range.start}T12:00:00`);day.setDate(day.getDate()+index);const date=localDate(day),weekday=day.getDay()||7;
    reportableStudentsAt(date).forEach(profile=>{
      const plan=studentLessonPlan(profile,date,fallbackTarget),{scheduled,base,physical,expected}=plan;
      if(!scheduled)fallback=true;if(plan.leave){buckets(profile).forEach(group=>{group.leaves.set(`${profile.id}:${date}`,{profile,leave:plan.leave});weeklySession(group,profile,date,plan.kind,plan.change).leaves.set(profile.id,{profile,leave:plan.leave})})}if(!expected)return;
      buckets(profile).forEach(group=>{group.expected+=expected;group.expectedByStudent.set(profile.id,(group.expectedByStudent.get(profile.id)||0)+expected);group.members.set(profile.id,profile);if(base){const session=weeklySession(group,profile,'','unscheduled');session.expected+=base;session.members.set(profile.id,profile)}if(physical){const session=weeklySession(group,profile,date,plan.kind,plan.change);session.expected+=1;session.members.set(profile.id,profile)}});
    });
  }
  const seen=new Set();
  calendarWeekAttendance(offset).forEach(record=>{
    const student=studentById(record.studentId),key=`${record.studentId}:${record.date}`;
    if(!student||!isValidLocalDate(record.date)||record.date>localDate()||seen.has(key))return;
    const created=studentCreatedDate(student);if(created&&created>record.date)return;seen.add(key);
    const profile=reportAttendanceProfile(student,record);
    buckets(profile).forEach(group=>{group.records.push(record);group.members.set(profile.id,profile);const scheduled=group.sessions.get(weeklySessionKey(profile,record.date)),fallbackSession=!cancelledLessonAt(profile,record.date)&&group.sessions.get(weeklySessionKey(profile,'')),session=scheduled||fallbackSession||weeklySession(group,profile,record.date,'unplanned');session.records.push(record)});
  });
  classReschedules().filter(change=>change.fromDate>=range.start&&change.fromDate<=range.end).forEach(change=>{const course=classById(change.classId),profiles=reportableStudentsAt(change.fromDate).filter(profile=>profileClassId(profile)===String(change.classId)),fallbackProfile={classId:change.classId,className:course?course.name:change.className,teacher:course?course.teacher:change.teacher};(profiles.length?profiles:[fallbackProfile]).forEach(profile=>buckets(profile).forEach(group=>weeklySession(group,profile,change.fromDate,'cancelled',change)))});
  [all,...Object.values(groups).flatMap(map=>[...map.values()])].forEach(group=>{group.expected=Math.round(group.expected*1e9)/1e9;group.sessions.forEach(session=>session.expected=Math.round(session.expected*1e9)/1e9)});
  return{range,all,groups,fallback};
}
function weeklyScope(stats,type=selectedReportScopeType,value=selectedReportScopeValue){return type==='school'?stats.all:(stats.groups[type]&&stats.groups[type].get(normalizedGroupLabel(value)))||newWeeklyGroup(value||'暂无数据')}
function reportCount(value){return String(Math.round((Number(value)||0)*100)/100)}
function reportNeedsScheduleReview(group){return group.records.length>Number(group.expected||0)+1e-7||Boolean(group.kind&&group.kind!=='unscheduled'&&group.members&&group.records.some(record=>!group.members.has(record.studentId)))||Boolean(group.sessions&&[...group.sessions.values()].some(session=>reportNeedsScheduleReview(session)))}
function reportRateForCounts(actual,expected){return actual>Number(expected||0)+1e-7?'待核对':expected>0?`${pct(actual,expected)}%`:'—'}
function reportRate(group){return reportNeedsScheduleReview(group)?'待核对':reportRateForCounts(group.records.length,group.expected)}
function reportScopeOptions(type){
  if(type==='school')return[];const key=type==='class'?'className':'teacher',names=new Set();
  state.classes.forEach(course=>names.add(normalizedGroupLabel(type==='class'?course.name:course.teacher)));
  const label=profile=>type==='teacher'?reportTeacherForProfile(profile):normalizedGroupLabel(profile[key]);
  state.students.forEach(student=>{names.add(label(student));(student.enrollmentHistory||[]).forEach(entry=>names.add(label(entry)))});
  state.attendance.forEach(record=>{const student=studentById(record.studentId);if(student)names.add(label(reportAttendanceProfile(student,record)))});
  return [...names].filter(name=>name!=='未填写').sort(compareLabels);
}
function renderWeeklyGroupBars(groups){
  const entries=[...groups.values()].filter(group=>group.members.size||group.records.length||group.leaves.size);
  if(!entries.length)return empty('▦','暂无有效应到安排','请设置班级排课或录入签到');
  return `<div class="class-bars">${entries.map(group=>`<div class="bar-row"><div><b>${esc(group.name)}</b><small>${group.members.size} 名学生 · ${group.records.length}/${reportCount(group.expected)} 次${group.leaves.size?` · 请假 ${group.leaves.size} 人次`:``}</small></div><div class="progress"><i style="width:${pct(group.records.length,group.expected)}%"></i></div><strong>${reportRate(group)}</strong></div>`).join('')}</div>`;
}
function renderReportScopeControls(){const names=reportScopeOptions(selectedReportScopeType);return `<div class="week-calendar-controls"><label class="field"><span>统计范围</span><select id="reportScopeType"><option value="school" ${selectedReportScopeType==='school'?'selected':''}>全校</option><option value="class" ${selectedReportScopeType==='class'?'selected':''}>某一个班级</option><option value="teacher" ${selectedReportScopeType==='teacher'?'selected':''}>某一位老师</option></select></label><label class="field"><span>选择对象</span><select id="reportScopeValue" ${selectedReportScopeType==='school'||!names.length?'disabled':''}>${names.length?names.map(name=>`<option ${name===selectedReportScopeValue?'selected':''}>${esc(name)}</option>`).join(''):'<option>全校</option>'}</select></label></div>`}
function renderWeeklyLessonSessions(scope){
  const sessions=[...scope.sessions.values()].sort((a,b)=>(a.date||'9999').localeCompare(b.date||'9999')||compareLabels(a.className,b.className)),planned=sessions.filter(session=>session.kind!=='cancelled'&&session.kind!=='unplanned'&&session.kind!=='unscheduled'),unknown=sessions.some(session=>session.kind==='unscheduled');
  const typeLabel=session=>session.kind==='cancelled'?`原日停课 → ${shortDate(session.toDate)}`:session.kind==='rescheduled'?`调课补上 · 原 ${shortDate(session.fromDate)}`:session.kind==='personal'?'个人提前 / 延期':session.kind==='extra'?'额外加课':session.kind==='unplanned'?'未匹配排课，请核对':session.kind==='unscheduled'?'默认周课次，日期待确认':'常规上课';
  return `<section class="panel weekly-lesson-sessions"><div class="panel-head"><div><h2>逐次上课情况</h2><p>${planned.length} 次已明确日期的班级课程 · 应到 ${reportCount(scope.expected)} 人次 / 实到 ${scope.records.length} 人次${unknown?' · 部分常规课未设置日期，仍按默认周次数折算':''}</p></div></div><p class="weekly-session-rule">同一学生两次上课计两次应到，请假仍计应到、不算实到；周到课率为各次实到人次合计 ÷ 应到人次合计，不按学生人数或各次百分比简单平均。</p>${sessions.length?`<div class="weekly-lessons-scroll"><table class="weekly-lessons-table"><thead><tr><th>实际上课日期</th><th>班级 / 任课老师</th><th>课程安排</th><th>应到人次</th><th>实到人次</th><th>到课率</th><th>名单</th></tr></thead><tbody>${sessions.map(session=>`<tr class="${session.kind==='cancelled'?'cancelled-session':''}"><td>${session.date?esc(shortDate(session.date)):'日期待确认'}</td><td><b>${esc(session.className)}</b><small>${esc(session.teacher)}</small></td><td>${esc(typeLabel(session))}${session.leaves.size?` · 请假 ${session.leaves.size} 人`:``}</td><td>${reportCount(session.expected)}</td><td>${session.records.length}</td><td>${session.kind==='cancelled'&&!session.records.length?'不计应到':reportNeedsScheduleReview(session)?`<button type="button" class="review-rate-btn" data-review-lesson="${esc(session.key)}">待核对 · 处理</button>`:reportRate(session)}</td><td>${session.kind==='cancelled'&&!session.records.length?'—':`<button type="button" class="mini-btn" data-report-lesson="${esc(session.key)}">查看本次名单</button>`}</td></tr>`).join('')}</tbody></table></div>`:empty('▦','该周没有上课安排','常规课程、加课和调课都会在这里分别显示')}</section>`;
}
function shortDate(value){const parts=String(value).split('-');return `${Number(parts[1])}月${Number(parts[2])}日`}
function calendarWeekLabel(offset=0){const range=calendarWeekRange(offset),prefix=offset===0?'本周':offset===-1?'上周':'';return `${prefix?`${prefix} · `:''}${shortDate(range.start)}—${shortDate(range.end)}`}
function availableReportWeekOffsets(){
  if(!state.attendance.length)return Array.from({length:12},(_,index)=>-index);
  const earliest=state.attendance.reduce((value,record)=>!value||record.date<value?record.date:value,''),earliestDate=new Date(`${earliest}T12:00:00`),earliestMonday=new Date(earliestDate);earliestMonday.setDate(earliestMonday.getDate()-((earliestMonday.getDay()+6)%7));
  const currentMonday=new Date(`${calendarWeekRange(0).start}T12:00:00`),recordedWeeks=Math.max(1,Math.floor((currentMonday-earliestMonday)/604800000)+1);
  return Array.from({length:Math.max(12,recordedWeeks)},(_,index)=>-index);
}
function normalizeCampusCode(value){const digits=String(value===null||value===undefined?'':value).replace(/\D/g,'');return(digits||'01').slice(0,2).padStart(2,'0')}
function nextStudentNo(campusCode=state.settings.campusCode){
  const prefix=`${String(new Date().getFullYear()).slice(-2)}${normalizeCampusCode(campusCode)}`;
  const existingMax=state.students.reduce((highest,student)=>{const match=String(student.studentNo||'').match(new RegExp(`^${prefix}(\\d{4})$`));return match?Math.max(highest,Number(match[1])):highest},0),max=Math.max(existingMax,Number(state.settings.studentNoCounters&&state.settings.studentNoCounters[prefix]||0));
  return max>=9999?'':`${prefix}${String(max+1).padStart(4,'0')}`;
}
function nextImportStudentNo(usedStudentNos){
  const prefix=`${String(new Date().getFullYear()).slice(-2)}${normalizeCampusCode(state.settings.campusCode)}`;
  let max=Math.max(0,Number(state.settings.studentNoCounters&&state.settings.studentNoCounters[prefix]||0));
  state.students.forEach(student=>{const match=String(student.studentNo||'').match(new RegExp(`^${prefix}(\\d{4})$`));if(match)max=Math.max(max,Number(match[1]))});
  usedStudentNos.forEach(studentNo=>{const match=String(studentNo||'').match(new RegExp(`^${prefix}(\\d{4})$`));if(match)max=Math.max(max,Number(match[1]))});
  if(max>=9999)return'';
  let sequence=max+1,candidate=`${prefix}${String(sequence).padStart(4,'0')}`;
  while(usedStudentNos.has(candidate)&&sequence<9999){sequence++;candidate=`${prefix}${String(sequence).padStart(4,'0')}`}
  return usedStudentNos.has(candidate)?'':candidate;
}
function syncStudentNoCounters(){
  const counters={...(state.settings.studentNoCounters||{})};state.students.forEach(student=>{const match=String(student.studentNo||'').match(/^(\d{4})(\d{4})$/);if(match)counters[match[1]]=Math.max(Number(counters[match[1]]||0),Number(match[2]))});state.settings.studentNoCounters=counters;
}
function ensureClasses(){
  if(!Array.isArray(state.classes))state.classes=[];
  state.classes.forEach(course=>{course.scheduleWeekday=Number(course.scheduleWeekday||0);course.scheduleStart=String(course.scheduleStart||'');course.reminderEnabled=Boolean(course.reminderEnabled)});
  const known=new Set(state.classes.map(c=>String(c.name||'').trim()).filter(Boolean));
  state.students.forEach((student,index)=>{const name=String(student.className||'').trim();if(!name||known.has(name))return;state.classes.push({id:uid(),name,teacher:String(student.teacher||'').trim(),schedule:'',classroom:'',scheduleWeekday:0,scheduleStart:'',reminderEnabled:false,createdAt:new Date().toISOString()});known.add(name)});
}

const CLASS_WEEKDAYS=['','周一','周二','周三','周四','周五','周六','周日'];
function classScheduleLabel(course){const weekday=Number(course&&course.scheduleWeekday),end=lessonClockEnd(String(course&&course.scheduleStart||''),classLessonDuration(course));return weekday?`${lessonCycleWeeks(course)===2?'每两周':'每'}${CLASS_WEEKDAYS[weekday]}${course.scheduleStart?' '+course.scheduleStart+(end?'—'+(end.dayOffset?'次日':'')+end.time:''):''}${lessonCycleWeeks(course)===2?' · 首课 '+course.scheduleAnchorDate:''}`:String(course&&course.schedule||'暂未设置')}
function classReminderLabel(course){return course&&course.reminderEnabled&&Number(course.scheduleWeekday)&&course.scheduleStart?`开课 5 分钟后提醒 · ${classScheduleLabel(course)}`:'未开启未签到提醒'}

function bindReliableTap(element,handler){
  if(!element)return;
  let lastTouch=0;
  element.ontouchend=event=>{lastTouch=Date.now();if(event&&event.preventDefault)event.preventDefault();handler()};
  element.onclick=event=>{if(Date.now()-lastTouch<700)return;if(event&&event.preventDefault)event.preventDefault();handler()};
}

const uiIcon = name => `<svg class="ui-icon" aria-hidden="true" focusable="false"><use href="ui-icons.svg#${name}"></use></svg>`;
const views = {
  dashboard:['教务工作台','今天也要陪孩子们一起成长'],
  students:['学生信息','录入、修改与查询学生档案'],
  classes:['班级管理','创建班级、安排老师并查看班级数据'],
  checkin:['签到中心','支持姓名、学号和安全人脸签到'],
  hours:['课时管理','查看余额并记录每次课时变化'],
  report:['每周上课报告','按自然周查看全校、班级与老师到课情况'],
  settings:['单机设置','机构名称、统计规则与本机数据备份']
};

function invalidatePageCache(){dataRevision++;pageCache.clear();mountedPageKey=''}
function pageCacheKey(view){
  const filters=view==='classes'?[selectedCourseGroup,courseClassSearch]:view==='dashboard'?classSpaceCacheFilters():view==='students'?[studentStatusFilter,studentClassFilter,studentSearchQuery,listPages.students]:view==='hours'?[hoursClassFilter,hoursSortOrder,listPages.hours]:view==='report'?[selectedReportWeekOffset,selectedReportScopeType,selectedReportScopeValue]:view==='checkin'?[listPages.attendance]:[];
  return JSON.stringify([view,isAdminAuthenticated,dataRevision,localDate(),filters]);
}
function mountPageContent(view,renderer){
  const content=$('#content'),key=pageCacheKey(view);
  if(view!=='settings'&&mountedPageKey===key)return;
  if(mountedPageKey&&mountedPageRevision===dataRevision&&mountedPageName!=='settings'){
    const fragment=document.createDocumentFragment();while(content.firstChild)fragment.appendChild(content.firstChild);
    pageCache.set(mountedPageKey,fragment);
    while(pageCache.size>6)pageCache.delete(pageCache.keys().next().value);
  }else content.textContent='';
  const cached=pageCache.get(key);
  if(cached){content.appendChild(cached);pageCache.delete(key)}else content.innerHTML=renderer();
  mountedPageKey=view==='settings'?'':key;mountedPageName=view;mountedPageRevision=dataRevision;
}
function render(){
  syncScreenOrientationClass();
  document.body.classList.toggle('admin-mode',isAdminAuthenticated);
  document.body.classList.toggle('kiosk-mode',!isAdminAuthenticated);
  document.body.classList.toggle('dashboard-view',isAdminAuthenticated&&currentView==='dashboard');
  const adminAccess=$('#adminAccessBtn');
  if(!isAdminAuthenticated){
    currentView='checkin';
    $('#viewTitle').textContent='学生签到';$('#viewSubtitle').textContent='请选择签到方式，签到成功后会显示剩余课时';
    $('#brandName').textContent=state.settings.orgName||'未来学校学生管理系统';
    $('#quickAddBtn').style.display='none';
    adminAccess.innerHTML=`<span>${uiIcon('lock')}</span><b>后台</b>`;
    bindReliableTap(adminAccess,openAdminLogin);
    mountPageContent('kiosk',renderKioskCheckin);
    bindViewEvents();
    return;
  }
  const [title,subtitle] = views[currentView];
  $('#viewTitle').textContent = title; $('#viewSubtitle').textContent = subtitle;
  $('#brandName').textContent = state.settings.orgName || '未来学校学生管理系统';
  $$('.nav-item').forEach(b => b.classList.toggle('active',b.dataset.view===currentView||(b.hasAttribute('data-more')&&['hours','report','settings'].includes(currentView))));
  const quickAdd=$('#quickAddBtn');quickAdd.style.display=['settings','students','classes'].includes(currentView)?'none':'';quickAdd.innerHTML=currentView==='classes'?'<span>＋</span>新增班级':'<span>＋</span>录入学生';quickAdd.onclick=currentView==='classes'?()=>openClassModal():()=>openStudentModal();
  adminAccess.innerHTML='<span>↩</span><b>退出后台</b>';bindReliableTap(adminAccess,exitAdmin);
  const renderers={dashboard:renderDashboard,students:renderStudents,classes:renderClasses,checkin:renderCheckin,hours:renderHours,report:renderReport,settings:renderSettings};
  mountPageContent(currentView,renderers[currentView]);
  bindViewEvents();
}

function renderKioskCheckin(){return renderClassSpaceKiosk()}

function kioskAttendanceRow(a){const s=studentById(a.studentId);if(!s)return'';return `<article class="list-row">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)}</b><small>${esc(a.className||s.className)} · ${esc(a.classroom||classroomForStudent(s))} · ${esc(attendanceMethodLabel(a.method))}</small></div><div class="row-end"><strong>${esc(a.time)}</strong><small>剩余 ${money(s.hours)} 课时</small></div></article>`}

function renderDashboard(){return renderClassSpaceDashboard()}

function openDashboardStatDetail(kind){
  const today=todayAttendance(),stats=buildWeeklyStats(0),eligible=[...stats.all.members.values()],week=stats.all.records,weeklyTarget=Number(state.settings.weeklyTarget||2),weekTarget=stats.all.expected,excluded=state.students.length-eligible.length;
  let icon='◎',title='学生总数',subtitle=`当前共有 ${state.students.length} 名学生`,body='',targetView='students',targetLabel='进入学生管理';
  if(kind==='students'){
    body=state.students.length?`<div class="list dashboard-detail-list">${state.students.map(s=>`<article class="list-row ${isLongTermAbsent(s)?'long-term-row':''}">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)} ${isLongTermAbsent(s)?'<em class="long-term-badge">长期不来</em>':''}</b><small>${isLongTermAbsent(s)?'长期不来学生':esc(s.className||'未分班')} · ${esc(s.studentNo)}</small></div><div class="row-end"><strong>${money(s.hours)} 课时</strong><small>${esc(s.school)}</small></div></article>`).join('')}</div>`:empty('◎','还没有学生','录入学生后会在这里显示名单');
  }else if(kind==='today'){
    icon='✓';title='今日签到详情';subtitle=`今天共完成 ${today.length} 次签到`;targetView='checkin';targetLabel='进入签到管理';
    body=today.length?`<div class="list dashboard-detail-list">${today.slice().reverse().map(a=>{const s=studentById(a.studentId);return s?`<article class="list-row">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)}</b><small>${esc(s.className)} · ${esc(attendanceMethodLabel(a.method))}</small></div><div class="row-end"><strong>${esc(a.time)}</strong><small>剩余 ${money(s.hours)} 课时</small></div></article>`:''}).join('')}</div>`:empty('✓','今天还没有签到','进入签到管理后可开始记录');
  }else if(kind==='week'){
    const rate=pct(week.length,weekTarget);icon='▥';title='本周到课率详情';subtitle=`本周 ${week.length} 次签到 / ${reportCount(weekTarget)} 次应到 · 按有效排课统计`;targetView='report';targetLabel='查看完整周报';
    body=`<div class="dashboard-rate-summary"><strong>${reportRate(stats.all)}</strong><div><b>总体到课率</b><span>${eligible.length} 名学生 · 按历史排课日和有效在读状态计算</span></div></div>${eligible.length?`<div class="list dashboard-detail-list">${eligible.map(s=>{const count=week.filter(a=>a.studentId===s.id).length,expected=stats.all.expectedByStudent.get(s.id)||0;return `<article class="list-row">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)}</b><small>${esc(s.className)} · 本周签到 ${count} 次</small></div><div class="row-end"><strong>${reportRateForCounts(count,expected)}</strong><small>${count} / ${reportCount(expected)} 次</small></div></article>`}).join('')}</div>`:empty('▥','本周暂无有效应到安排','长期不来从生效日起不再计应到')}`;
  }else if(kind==='low-hours'){
    const lowStudents=lowHoursStudents().sort((a,b)=>Number(a.hours)-Number(b.hours)),criticalCount=lowStudents.filter(s=>Number(s.hours)<=HOURS_CRITICAL_THRESHOLD).length;icon='!';title='课时不足学生';subtitle=`10 节课以内 ${criticalCount} 人 · 15 节课以内共 ${lowStudents.length} 人 · 已排除长期不来学生`;targetView='hours';targetLabel='进入课时管理';
    body=lowStudents.length?`<div class="hours-warning-legend"><span class="critical">10 节课以内 · 需要尽快续费</span><span class="low">15 节课以内 · 提前关注</span></div><div class="list dashboard-detail-list low-hours-detail">${lowStudents.map(s=>`<article class="list-row">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)}</b><small>${esc(s.className)} · ${esc(s.studentNo)}${s.contact?` · ${esc(s.contact)}`:''}</small></div><div class="row-end"><strong class="${hoursWarningLevel(s.hours)}">${money(s.hours)} 课时</strong><small>${hoursWarningLabel(s.hours)}</small></div><button class="mini-btn" data-hours="${s.id}">＋ 加课时</button></article>`).join('')}</div>`:empty('★','没有课时不足学生','所有正常在读学生的剩余课时都大于 15，长期不来学生不参与提醒');
  }
  openModal(`<div class="modal-head"><span>${icon}</span><div><h2>${title}</h2><p>${subtitle}</p></div></div>${body}<div class="form-actions dashboard-detail-actions"><button type="button" class="secondary-btn" id="closeStatDetail">关闭</button><button type="button" class="primary-btn" data-detail-go="${targetView}">${targetLabel}</button></div>`);
  $('#closeStatDetail').onclick=closeModal;
  $$('[data-detail-go]').forEach(button=>button.onclick=()=>{if(kind==='week'){selectedReportWeekOffset=0;selectedReportScopeType='school';selectedReportScopeValue=''}closeModal();go(button.dataset.detailGo)});
  $$('[data-hours]').forEach(button=>button.onclick=()=>openHoursModal(studentById(button.dataset.hours)));
}

function renderGroupBars(groups,att){
  const entries=Object.entries(groups),counts=countByStudent(att);
  if(!entries.length) return empty('▦','还没有班级数据','录入学生时填写班级和老师');
  return `<div class="class-bars">${entries.map(([name,members])=>{const actual=members.reduce((sum,student)=>sum+(counts.get(student.id)||0),0);const rate=pct(actual,members.length*state.settings.weeklyTarget);return `<div class="bar-row"><div><b>${esc(name)}</b><small>${members.length} 名学生</small></div><div class="progress"><i style="width:${rate}%"></i></div><strong>${rate}%</strong></div>`}).join('')}</div>`;
}
function avatarMarkup(s){ return s.faceImage ? `<span class="avatar photo"><img loading="lazy" decoding="async" width="96" height="96" src="${s.faceImage}" alt="${esc(s.name)}登记照"></span>` : `<span class="avatar ${s.gender==='女'?'female':''}">${esc(s.name.slice(-1))}</span>`; }
function attendanceRow(a){ const s=studentById(a.studentId); if(!s)return''; const timeLabel=a.date&&a.date!==localDate()?`${a.date} ${a.time}`:a.time;return `<article class="list-row ${a.makeup?'makeup-attendance-row':''}">${avatarMarkup(s)}<div class="row-main"><b>${esc(s.name)}${a.makeup?'<em class="makeup-badge">老师补签</em>':''}</b><small>${esc(a.className||s.className)} · ${esc(a.classroom||classroomForStudent(s))} · ${esc(attendanceMethodLabel(a.method))}</small></div><div class="row-end"><strong>${esc(timeLabel)}</strong><small>剩余 ${money(s.hours)} 课时</small></div><button class="mini-btn warn undo-checkin" data-undo="${a.id}">撤销</button></article>`; }
function empty(icon,title,text){return `<div class="empty"><span>${icon}</span><b>${title}</b><p>${text}</p></div>`;}

function studentMatchesStatusFilter(student){return studentStatusFilter==='all'||(studentStatusFilter==='long-term')===isLongTermAbsent(student)}
function studentMatchesClassFilter(student){
  if(studentClassFilter==='all')return true;
  if(studentClassFilter==='long-term')return isLongTermAbsent(student);
  if(studentClassFilter==='unassigned')return !isLongTermAbsent(student)&&!String(student.className||'').trim();
  return !isLongTermAbsent(student)&&studentClassFilter===`class:${normalizedGroupLabel(student.className)}`;
}
function countByStudent(records){
  const counts=new Map();records.forEach(record=>counts.set(record.studentId,(counts.get(record.studentId)||0)+1));return counts;
}
function listPage(items,key){
  const pages=Math.max(1,Math.ceil(items.length/LIST_PAGE_SIZE)),page=Math.max(1,Math.min(pages,Number(listPages[key])||1));
  listPages[key]=page;const start=(page-1)*LIST_PAGE_SIZE;
  return{key,page,pages,total:items.length,start,items:items.slice(start,start+LIST_PAGE_SIZE)};
}
function renderListPager(page){
  if(page.total<=LIST_PAGE_SIZE)return'';
  return `<div class="list-pager" aria-label="名单分页"><span>共 ${page.total} 条 · 当前 ${page.start+1}–${Math.min(page.start+LIST_PAGE_SIZE,page.total)} 条</span><div><button type="button" class="secondary-btn" data-list-prev="${page.key}" ${page.page===1?'disabled':''}>上一页</button><b aria-live="polite">${page.page} / ${page.pages}</b><button type="button" class="secondary-btn" data-list-next="${page.key}" ${page.page===page.pages?'disabled':''}>下一页</button></div></div>`;
}
function filteredStudents(){
  const query=studentSearchQuery.trim().toLowerCase();
  return state.students.filter(student=>studentMatchesStatusFilter(student)&&studentMatchesClassFilter(student)&&(!query||[student.name,student.studentNo,student.contact,student.school,student.className].some(value=>String(value||'').toLowerCase().includes(query))));
}
function refreshStudentResults(){
  const table=$('#studentTable');if(!table||currentView!=='students')return;
  const students=filteredStudents();table.innerHTML=studentTable(students);
  const count=$('#studentVisibleCount');if(count)count.textContent=students.length;
  mountedPageKey=pageCacheKey('students');
}
function scheduleStudentSearch(event){
  studentSearchQuery=event.target.value;listPages.students=1;
  if(event.isComposing)return;
  if(studentSearchFrame!==null)cancelAnimationFrame(studentSearchFrame);
  studentSearchFrame=requestAnimationFrame(()=>{studentSearchFrame=null;refreshStudentResults()});
}
function changeListPage(key,direction){
  if(!Object.prototype.hasOwnProperty.call(listPages,key))return;
  listPages[key]=Math.max(1,listPages[key]+direction);
  if(key==='students')refreshStudentResults();else render();
  const target=key==='students'?$('#studentTable'):key==='hours'?$('.hours-balance-panel'):$('#content');
  if(target&&target.getBoundingClientRect().top<0)target.scrollIntoView({block:'start',behavior:'auto'});
}
function pagedAttendanceRows(records){
  const page=listPage(records,'attendance');return renderListPager(page)+page.items.map(attendanceRow).join('');
}
function studentClassFilterItems(){
  const active=reportableStudents(),counts=new Map();active.forEach(student=>{const name=normalizedGroupLabel(student.className);counts.set(name,(counts.get(name)||0)+1)});const names=[...new Set([...state.classes.map(course=>normalizedGroupLabel(course.name)),...counts.keys()])].filter(name=>name&&name!=='未填写').sort(compareLabels);
  const items=[{key:'all',label:'全部班级',count:state.students.length}].concat(names.map(name=>({key:`class:${name}`,label:name,count:counts.get(name)||0})));
  const unassigned=active.filter(student=>!String(student.className||'').trim()).length;if(unassigned)items.push({key:'unassigned',label:'未分班',count:unassigned});
  const longTerm=state.students.filter(isLongTermAbsent).length;if(longTerm)items.push({key:'long-term',label:'长期不来',count:longTerm});
  return items;
}
function renderStudents(){
  const classItems=studentClassFilterItems();if(!classItems.some(item=>item.key===studentClassFilter))studentClassFilter='all';const visible=filteredStudents(),selectedClass=classItems.find(item=>item.key===studentClassFilter)||classItems[0],classOptions=classItems.map(item=>`<option value="${esc(item.key)}" ${item.key===studentClassFilter?'selected':''}>${esc(item.label)}（${item.count}人）</option>`).join(''),classButtons=classItems.map(item=>`<button type="button" class="student-class-chip ${item.key==='long-term'?'long-term':''} ${item.key===studentClassFilter?'active':''}" data-student-class-filter="${esc(item.key)}"><span>${item.key==='long-term'?'◷':item.key==='all'?'◎':'▦'}</span><b>${esc(item.label)}</b><small>${item.count} 人</small></button>`).join('');
  return `<div class="toolbar student-toolbar"><label class="search">⌕<input id="studentSearch" value="${esc(studentSearchQuery)}" placeholder="搜索姓名、学号、联系方式、学校或班级"></label><label class="student-status-filter student-class-select"><span>班级分类</span><select id="studentClassFilter">${classOptions}</select></label><label class="student-status-filter"><span>学生状态</span><select id="studentStatusFilter"><option value="all" ${studentStatusFilter==='all'?'selected':''}>全部学生</option><option value="active" ${studentStatusFilter==='active'?'selected':''}>正常在读</option><option value="long-term" ${studentStatusFilter==='long-term'?'selected':''}>长期不来</option></select></label><button class="primary-btn" data-action="add-student"><span>＋</span>录入学生</button></div><div class="student-class-browser"><div class="student-class-browser-head"><div><b>按班级查看</b><small>点击班级，只显示该班学生</small></div><span>${classItems.length-1} 个分类</span></div><div class="student-class-chips">${classButtons}</div></div><div class="student-status-summary"><span>${esc(selectedClass.label)} <b id="studentVisibleCount">${visible.length}</b> 人</span><span>正常在读 <b>${reportableStudents().length}</b> 人</span><span class="long-term">长期不来 <b>${state.students.filter(isLongTermAbsent).length}</b> 人</span><small>可继续用上方搜索框在当前分类中查找</small></div><div id="studentTable">${studentTable(visible)}</div>`;
}
function studentTable(students){
  const page=listPage(students,'students');return renderListPager(page)+studentRowsTable(page.items);
}
function studentRowsTable(students){
  if(!students.length)return `<section class="panel">${empty('◎','还没有学生','点击“录入学生”建立第一份档案')}</section>`;
  return `<div class="table-wrap student-table-wrap"><table class="data-table"><thead><tr><th>学生</th><th>状态</th><th>联系方式</th><th>人脸</th><th>年龄 / 性别</th><th>学校 / 年级</th><th>所在班级</th><th>老师</th><th>课时</th><th>操作</th></tr></thead><tbody>${students.map(s=>`<tr class="${isLongTermAbsent(s)?'long-term-student-row':''}"><td><div class="student-cell">${avatarMarkup(s)}<div><b>${esc(s.name)}</b><small>${esc(s.studentNo)}</small></div></div></td><td>${isLongTermAbsent(s)?'<span class="long-term-badge">长期不来</span>':'<span class="active-student-badge">正常在读</span>'}</td><td><span class="contact-cell">${esc(s.contact||'未填写')}</span></td><td>${hasFaceProfile(s)?'<span class="face-status ok">安全特征已录入</span>':s.faceImage?'<span class="face-status warn">需升级人脸</span>':'<span class="face-status">未录入</span>'}</td><td>${esc([s.age?`${s.age} 岁`:'',s.gender].filter(Boolean).join(' · ')||'未填写')}</td><td>${esc([s.school,s.grade].filter(Boolean).join(' · ')||'未填写')}</td><td>${isLongTermAbsent(s)?'<span class="long-term-unassigned">长期不来学生</span>':esc(s.className||'未分班')}</td><td>${isLongTermAbsent(s)?'—':esc(s.teacher||'未设置')}</td><td><span class="hours-pill ${hoursWarningLevel(s.hours)}">${money(s.hours)}</span>${hoursWarningLabel(s.hours)?`<small class="hours-warning-text ${hoursWarningLevel(s.hours)}">${hoursWarningLabel(s.hours)}</small>`:''}</td><td><div class="row-actions"><button class="mini-btn ${isLongTermAbsent(s)?'status-restore':'status-pause'}" data-long-term="${s.id}">${isLongTermAbsent(s)?'恢复在读':'设为长期不来'}</button><button class="mini-btn" data-hours="${s.id}">加课时</button><button class="mini-btn alt" data-edit="${s.id}">编辑</button><button class="mini-btn warn" data-delete="${s.id}">删除</button></div></td></tr>`).join('')}</tbody></table></div>`;
}

function renderClasses(){
  return renderCourseClasses();
}
function classCard(course,week,index,memberGroups,weekCounts){
  const members=memberGroups?(memberGroups.get(course.name)||[]):state.students.filter(s=>s.className===course.name),eligible=reportableStudents(members),excluded=members.length-eligible.length,ids=new Set(eligible.map(s=>s.id)),weekly=weekCounts&&weekCounts.get(normalizedGroupLabel(course.name)),actual=weekly?weekly.records.length:0,target=weekly?weekly.expected:0,rate=pct(actual,target),hours=members.reduce((sum,s)=>sum+Number(s.hours||0),0);
  return `<article class="class-card tone-${index%4}" data-class-card="${course.id}"><div class="class-card-head"><span>${uiIcon('classes')}</span><div><h2 title="${esc(course.name)}">${esc(classWithinCourseName(course))}</h2><p>${esc(course.teacher||'暂未安排老师')}</p>${classWithinCourseName(course)!==course.name?`<small class="course-original-name">原班名：${esc(course.name)}</small>`:''}</div><em>${members.length} 人${excluded?` · 长期 ${excluded}`:''}</em></div><div class="class-meta"><div><small>上课安排</small><b>${esc(classScheduleLabel(course))}</b></div><div><small>上课教室</small><b>${esc(course.classroom||'暂未设置')}</b></div></div><div class="class-reminder-status ${course.reminderEnabled?'enabled':''}"><span>${course.reminderEnabled?'◉':'○'}</span><div><b>${course.reminderEnabled?'未签到提醒已开启':'未签到提醒未开启'}</b><small>${esc(classReminderLabel(course))}</small></div></div><div class="class-numbers"><div><strong>${money(hours)}</strong><small>剩余课时</small></div><div><strong>${actual}</strong><small>有效签到</small></div><div><strong>${weekly?reportRate(weekly):'—'}</strong><small>本周到课率</small></div></div><div class="class-progress"><i style="width:${rate}%"></i></div><div class="class-members">${members.length?members.slice(0,8).map(s=>`<span class="${isLongTermAbsent(s)?'long-term-member':''}">${avatarMarkup(s)}<b>${esc(s.name)}</b>${isLongTermAbsent(s)?'<small>长期不来</small>':''}</span>`).join(''):'<small>暂无学生，可在学生资料中选择本班</small>'}${members.length>8?`<button type="button" class="mini-btn" data-class-students="${course.id}">查看全部 ${members.length} 人 →</button>`:''}</div><div class="class-card-actions"><button class="reminder-detail-btn" data-class-absent="${course.id}">查看今日未签到</button><button class="promotion-btn compact" data-class-promote="${course.id}" ${members.length?'':'disabled'}>⇧ 集体升班</button><button class="secondary-btn" data-class-extra-lessons="${course.id}">加课 / 调课${classExtraLessons(course).length?` · ${classExtraLessons(course).length}`:''}</button><button class="secondary-btn" data-class-edit="${course.id}">编辑班级</button><button class="danger-btn" data-class-delete="${course.id}">删除</button></div></article>`;
}

function renderCheckin(){
  const today=todayAttendance(),checked=new Set(today.map(a=>a.studentId)),eligible=reportableStudents(),absent=eligible.filter(s=>!checked.has(s.id)),excluded=state.students.length-eligible.length,recentMakeup=state.attendance.filter(record=>record.makeup||record.method==='老师补签').slice().reverse().slice(0,20);
  return `<div class="checkin-grid"><button class="method-card" data-check="姓名"><span class="method-icon">${uiIcon('name')}</span><b>姓名签到</b><small>输入学生姓名快速查找</small><em>立即签到 →</em></button><button class="method-card" data-check="学号"><span class="method-icon">${uiIcon('number')}</span><b>学号签到</b><small>输入唯一学号准确签到</small><em>立即签到 →</em></button><button class="method-card" data-check="人脸"><span class="method-icon">${uiIcon('face')}</span><b>人脸签到</b><small>实时画面与登记照核对</small><em>打开摄像头 →</em></button><button class="method-card makeup-method-card" data-makeup-checkin><span class="method-icon">${uiIcon('classes')}</span><b>老师补签</b><small>按日期、班级勾选未签到学生</small><em>打开补签名单 →</em></button></div><div class="grid-2" style="margin-top:17px"><section class="panel"><div class="panel-head"><div><h2>今日已到</h2><p>可撤销误签并自动恢复课时</p></div><span class="tag">${today.length} 人次</span></div>${today.length?`<div class="list">${pagedAttendanceRows(today.slice().reverse())}</div>`:empty('✓','暂无签到记录','选择上方任一方式开始签到')}</section><section class="panel"><div class="panel-head"><div><h2>今日未到</h2><p>仅统计正常在读学生${excluded?` · 已排除 ${excluded} 名长期不来学生`:''}</p></div><span class="tag">${absent.length} 人</span></div>${absent.length?`<div class="chips">${absent.map(s=>`<span class="chip absent">${esc(s.name)} · ${esc(s.className)}</span>`).join('')}</div>`:empty('★','正常在读学生均已签到','长期不来学生不会列入未到名单')}</section></div>${recentMakeup.length?`<section class="panel makeup-history-panel"><div class="panel-head"><div><h2>最近补签记录</h2><p>补签会计入对应日期的周报与到课率，可在这里撤销</p></div><span class="tag">最近 ${recentMakeup.length} 条</span></div><div class="list">${recentMakeup.map(attendanceRow).join('')}</div></section>`:''}`;
}

function renderHours(){
  const total=state.students.reduce((sum,s)=>sum+Number(s.hours||0),0),lowStudents=lowHoursStudents(),low10=lowStudents.filter(s=>Number(s.hours)<=HOURS_CRITICAL_THRESHOLD).length,low15=lowStudents.length,classNames=[...new Set([...state.classes.map(course=>normalizedGroupLabel(course.name)),...state.students.map(student=>normalizedGroupLabel(student.className))])].filter(name=>name&&name!=='未填写').sort((a,b)=>compareLabels(a,b));
  if(hoursClassFilter!=='all'&&!classNames.includes(hoursClassFilter))hoursClassFilter='all';
  const visibleStudents=state.students.filter(student=>hoursClassFilter==='all'||normalizedGroupLabel(student.className)===hoursClassFilter).slice().sort((a,b)=>{const difference=Number(a.hours||0)-Number(b.hours||0);return (hoursSortOrder==='desc'?-difference:difference)||compareStudentNames(a,b)});
  const classOptions=`<option value="all" ${hoursClassFilter==='all'?'selected':''}>全部班级</option>${classNames.map(name=>`<option value="${esc(name)}" ${hoursClassFilter===name?'selected':''}>${esc(name)}</option>`).join('')}`;
  const page=listPage(visibleStudents,'hours'),transactionCounts=countByStudent(state.transactions);
  const studentRows=renderListPager(page)+page.items.map(student=>{const recordCount=transactionCounts.get(student.id)||0,warning=studentHoursWarningLabel(student),level=studentHoursWarningLevel(student);return `<article class="list-row hours-student-row" data-hours-detail="${student.id}" tabindex="0" role="button" aria-label="查看 ${esc(student.name)} 的课时变动记录">${avatarMarkup(student)}<div class="row-main"><b>${esc(student.name)} ${isLongTermAbsent(student)?'<em class="long-term-badge">长期不来</em>':''}</b><small>${isLongTermAbsent(student)?'长期不来学生':esc(student.className)} · ${esc(student.studentNo)} · ${recordCount} 条变动</small></div><div class="row-end"><strong class="${level}">${money(student.hours)} 课时</strong><small class="${level}">${isLongTermAbsent(student)?'暂不参与课时不足提醒':warning||'点击查看记录'}</small></div><button class="mini-btn hours-add-button" data-hours="${student.id}">＋ 增加</button><span class="hours-row-arrow">›</span></article>`}).join('');
  return `<div class="stat-grid hours-stat-grid"><article class="stat-card"><span>${uiIcon('hours')}</span><p>全部剩余课时</p><strong>${money(total)}</strong><small>课时</small></article><button type="button" class="stat-card hours-alert hours-alert-button critical" data-hours-alert="10"><span>${uiIcon('alert')}</span><p>10 节课以内</p><strong>${low10}</strong><small>正常在读 · 需要尽快续费</small><em>查看并导出名单 →</em></button><button type="button" class="stat-card hours-alert hours-alert-button low" data-hours-alert="15"><span>${uiIcon('alert')}</span><p>15 节课以内</p><strong>${low15}</strong><small>正常在读 · 提前关注</small><em>查看并导出名单 →</em></button></div><div class="grid-2 hours-page-grid"><section class="panel hours-balance-panel"><div class="panel-head hours-panel-head"><div><h2>学生课时余额</h2><p>课时不足统计已自动排除长期不来学生</p></div><span class="tag">${visibleStudents.length} 人</span></div><div class="hours-toolbar"><label><span>选择班级</span><select id="hoursClassFilter">${classOptions}</select></label><label><span>余额排序</span><select id="hoursSortOrder"><option value="asc" ${hoursSortOrder==='asc'?'selected':''}>剩余课时：从少到多</option><option value="desc" ${hoursSortOrder==='desc'?'selected':''}>剩余课时：从多到少</option></select></label></div>${state.students.length?(visibleStudents.length?`<div class="list hours-student-list">${studentRows}</div>`:empty('⌕','该班级暂无学生','请选择其他班级查看')):empty('◷','还没有课时数据','先录入学生和初始课时')}</section><section class="panel"><div class="panel-head"><div><h2>最近课时变化</h2><p>签到扣减与续费增加记录</p></div></div>${state.transactions.length?`<div class="list recent-hours-list">${state.transactions.slice().reverse().slice(0,20).map(t=>{const s=studentById(t.studentId);return s?`<article class="list-row recent-hours-row" data-hours-detail="${s.id}" role="button" tabindex="0"><div class="transaction-sign ${t.amount>0?'increase':'decrease'}">${t.amount>0?'+':'−'}</div><div class="row-main"><b>${esc(s.name)} · ${esc(t.reason)}</b><small>${esc(t.date)} ${esc(t.time)}</small></div><div class="row-end"><strong class="${t.amount>0?'increase':'decrease'}">${t.amount>0?'+':''}${money(t.amount)}</strong></div></article>`:''}).join('')}</div>`:empty('↕','暂无课时变化','签到或增加课时后会自动记录')}</section></div>`;
}

function lowHoursExportRows(threshold=HOURS_WARNING_THRESHOLD){
  return studentsUnderHours(threshold).map(student=>[student.name,student.studentNo||'',student.contact||'',student.school||'',student.grade||'',student.className||'',student.teacher||'',Number(student.hours||0),Number(student.hours)<=HOURS_CRITICAL_THRESHOLD?'10 节课以内':'15 节课以内']);
}
function exportLowHoursStudents(threshold=HOURS_WARNING_THRESHOLD){
  const limit=Number(threshold)===HOURS_CRITICAL_THRESHOLD?HOURS_CRITICAL_THRESHOLD:HOURS_WARNING_THRESHOLD,rows=lowHoursExportRows(limit);if(!rows.length)return toast(`当前没有 ${limit} 节课以内的正常在读学生`,true);
  const workbook=XLSX.utils.book_new();XLSX.utils.book_append_sheet(workbook,workbookSheet(['学生姓名','学号','联系方式','学校','年级','班级','授课老师','剩余课时','提醒级别'],rows,[14,14,18,20,12,22,14,12,16]),`${limit}节以内`);const safeName=String(state.settings.orgName||'未来学校学生管理系统').replace(/[\\/:*?"<>|]/g,'_');saveWorkbook(workbook,`${safeName}_${limit}节课以内学生_${localDate()}.xlsx`);
}
function openHoursAlertDetail(threshold=HOURS_WARNING_THRESHOLD){
  const limit=Number(threshold)===HOURS_CRITICAL_THRESHOLD?HOURS_CRITICAL_THRESHOLD:HOURS_WARNING_THRESHOLD,students=studentsUnderHours(limit),tone=limit===HOURS_CRITICAL_THRESHOLD?'critical':'low';
  openModal(`<div class="modal-head"><span>${uiIcon('alert')}</span><div><h2>${limit} 节课以内学生</h2><p>共 ${students.length} 名正常在读学生 · 已自动排除长期不来学生</p></div></div><div class="hours-warning-legend"><span class="${tone}">${limit===HOURS_CRITICAL_THRESHOLD?'需要尽快联系续费':'建议提前联系续费'}</span><span>按剩余课时从少到多排列</span></div>${students.length?`<div class="list dashboard-detail-list low-hours-detail hours-alert-detail-list">${students.map(student=>`<article class="list-row">${avatarMarkup(student)}<div class="row-main"><b>${esc(student.name)}</b><small>${esc(student.className||'未分班')} · ${esc(student.studentNo)}${student.contact?` · ${esc(student.contact)}`:''}</small></div><div class="row-end"><strong class="${hoursWarningLevel(student.hours)}">${money(student.hours)} 课时</strong><small>${hoursWarningLabel(student.hours)}</small></div><div class="hours-alert-row-actions"><button type="button" class="mini-btn alt" data-alert-hours-detail="${student.id}">课时记录</button><button type="button" class="mini-btn" data-hours="${student.id}">＋ 加课时</button></div></article>`).join('')}</div>`:empty('★',`没有 ${limit} 节课以内学生`,'长期不来学生不会进入该名单')}<div class="form-actions dashboard-detail-actions"><button type="button" class="secondary-btn" id="closeHoursAlertDetail">关闭</button><button type="button" class="primary-btn" id="exportLowHoursStudents" ${students.length?'':'disabled'}>导出 Excel</button></div>`);
  $('#modal').classList.add('hours-alert-detail-modal');$('#closeHoursAlertDetail').onclick=closeModal;$('#exportLowHoursStudents').onclick=()=>exportLowHoursStudents(limit);$$('[data-hours]').forEach(button=>button.onclick=()=>openHoursModal(studentById(button.dataset.hours)));$$('[data-alert-hours-detail]').forEach(button=>button.onclick=()=>openHoursDetail(studentById(button.dataset.alertHoursDetail)));
}

function openReportLessonDetail(key){
  const session=weeklyScope(buildWeeklyStats(selectedReportWeekOffset)).sessions.get(key);if(!session)return toast('课程安排已变化，请重新打开周报',true);
  const arrivedIds=new Set(session.records.map(record=>record.studentId)),arrived=[...arrivedIds].map(id=>studentById(id)).filter(Boolean),absent=[...session.members.values()].filter(student=>!arrivedIds.has(student.id)&&!session.leaves.has(student.id));
  const roster=(title,students)=>`<section class="report-class-group"><div class="report-class-head"><b>${title}</b><em>${students.length} 人</em></div><div class="list">${students.slice().sort(compareStudentNames).map(student=>`<article class="list-row">${avatarMarkup(student)}<div class="row-main"><b>${esc(student.name)}</b><small>${esc(student.studentNo||'')} · ${esc(session.className)}</small></div>${title==='本次未到'&&session.date?leaveActionMarkup(student,session.classId,session.date):''}</article>`).join('')||'<p class="weekly-session-rule">暂无学生</p>'}</div></section>`;
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>本次上课名单</h2><p>${session.date?esc(session.date):'常规课日期未确认'} · ${esc(session.className)} · ${esc(session.teacher)}</p></div></div><div class="warning-box">本次应到 ${reportCount(session.expected)} 人次，实到 ${session.records.length} 人次；请假仍计应到，单列请假名单且不催签到。${session.kind==='unscheduled'?'日期尚未确认，不能据此判断某次缺勤，请先设置常规排课。':'仅展示这次课的名单，不与同周另一次合并。'}</div>${roster('本次已到',arrived)}${roster('本次未到',absent)}${leaveRecordsMarkup(session.classId,session.date)}<div class="form-actions"><button type="button" class="primary-btn" id="closeReportLessonDetail">关闭名单</button></div>`);
  $('#modal').classList.add('lesson-detail-modal');bindStudentLeaveButtons($('#modal'));$('#closeReportLessonDetail').onclick=closeModal;
  const actions=$('#closeReportLessonDetail').parentElement,reviewButton=document.createElement('button');reviewButton.type='button';reviewButton.id='reviewLessonButton';reviewButton.className='primary-btn review-lesson-button';reviewButton.textContent='核对并处理';reviewButton.onclick=()=>openReportLessonReview(key);actions.prepend(reviewButton);$('#closeReportLessonDetail').className='secondary-btn';
}
function renderReport(){
  if(selectedReportScopeType!=='school'&&!reportScopeOptions(selectedReportScopeType).includes(selectedReportScopeValue))selectedReportScopeValue=reportScopeOptions(selectedReportScopeType)[0]||'';
  const stats=buildWeeklyStats(selectedReportWeekOffset),scope=weeklyScope(stats),range=stats.range,unique=new Set(scope.records.map(a=>a.studentId)),absent=[...scope.members.values()].filter(s=>!unique.has(s.id)),rate=pct(scope.records.length,scope.expected),rangeLabel=`${shortDate(range.start)}—${shortDate(range.end)}`;
  const weekOptions=availableReportWeekOffsets().map(offset=>`<option value="${offset}" ${offset===selectedReportWeekOffset?'selected':''}>${esc(calendarWeekLabel(offset))}</option>`).join('');
  const groupCards=[['学校到课率',stats.groups.school],['班级到课率',stats.groups.class],['老师到课率',stats.groups.teacher]].map(([title,groups])=>`<section class="panel"><div class="panel-head"><div><h2>${title}</h2><p>${esc(rangeLabel)} · ${title==='老师到课率'?'按班级管理中的任课老师汇总 · 合并所教全部班级':'按当次上课班级归属统计'}</p></div></div>${renderWeeklyGroupBars(groups)}</section>`).join('');
  return `<div class="report-toolbar"><label class="report-week-picker"><span>查看周报</span><select id="reportWeekSelect">${weekOptions}</select></label><button type="button" class="primary-btn week-calendar-open" id="openWeekCalendar"><span>${uiIcon('classes')}</span>周历到课率</button></div>${renderReportScopeControls()}<div class="report-exclusion-note"><span>${uiIcon('hours')}</span><div><b>按实际排课日和历史在读状态计算</b><small>请假单列并计入应到，不算实到、不扣课时、不催签到；长期不来生效后不再计应到，之前有效签到保留。老师归属以班级管理设置为准，人数去重，应到与实到按实际课次计算。原日期停课不计应到，新日期补上与本周常规课分别累计。${stats.fallback?'未排课班级按默认每周 '+Number(state.settings.weeklyTarget||2)+' 次、有效在读天数折算。':''}没有应到安排时显示“—”。${[...stats.groups.class.values(),...stats.groups.teacher.values()].some(reportNeedsScheduleReview)?'有签到超过应到的安排，相关到课率显示“待核对”，请在班级管理中补录实际加课日期，不会自动推算。':''}</small></div></div><section class="report-hero"><div><p>${selectedReportWeekOffset===0?'本周':selectedReportWeekOffset===-1?'上周':'所选周'}${selectedReportScopeType==='school'?'总体':esc(scope.name)}到课率</p><strong>${reportRate(scope)}</strong><small>${rangeLabel} · ${scope.records.length} 次有效签到 / ${reportCount(scope.expected)} 次应到${scope.leaves.size?` · 请假 ${scope.leaves.size} 人次（计入应到）`:``}</small></div><div class="ring" style="--p:${rate*3.6}deg"><b>${reportRate(scope)}</b></div><div class="report-summary"><div><b>${unique.size}</b><span>签到学生</span></div><div><b>${absent.length}</b><span>整周未签到</span></div><div><b>${scope.records.length}</b><span>有效签到</span></div></div></section>${selectedReportScopeType==='school'?`<div class="report-grid">${groupCards}</div>`:''}${renderWeeklyLessonSessions(scope)}<div class="grid-2 report-student-entry-grid"><button type="button" class="panel report-student-entry attended" data-report-student-detail="attended"><span class="report-entry-icon">✓</span><div><h2>该周签到学生</h2><p>按当次签到班级查看名单和次数</p></div><strong>${unique.size}</strong><small>人</small><em>查看分类详情 →</em></button><button type="button" class="panel report-student-entry absent" data-report-student-detail="absent"><span class="report-entry-icon">!</span><div><h2>该周未到学生</h2><p>整周无签到（含请假）；某次情况请看逐次名单</p></div><strong>${absent.length}</strong><small>人</small><em>查看分类详情 →</em></button></div>`;
}
function reportCard(title,groups,week,rangeLabel){ return `<section class="panel"><div class="panel-head"><div><h2>${title}</h2><p>${esc(rangeLabel)}</p></div></div>${renderGroupBars(groups,week)}</section>`; }

function openReportStudentDetail(kind){
  const stats=buildWeeklyStats(selectedReportWeekOffset),scope=weeklyScope(stats),range=stats.range,week=scope.records,attendedIds=new Set(week.map(record=>record.studentId)),isAttended=kind==='attended',students=[...scope.members.values()].filter(student=>isAttended?attendedIds.has(student.id):!attendedIds.has(student.id)),groups={},rangeLabel=`${shortDate(range.start)}—${shortDate(range.end)}`;
  if(isAttended){const seen=new Set();week.forEach(record=>{const student=studentById(record.studentId),profile={...studentProfileAtDate(student,record.date),className:record.className||studentProfileAtDate(student,record.date).className},name=normalizedGroupLabel(profile.className),key=`${profile.id}:${name}`;if(!seen.has(key)){seen.add(key);(groups[name]||(groups[name]=[])).push(profile)}})}else students.forEach(student=>(groups[normalizedGroupLabel(student.className)]||(groups[normalizedGroupLabel(student.className)]=[])).push(student));
  const groupMarkup=Object.entries(groups).sort(([a],[b])=>compareLabels(a,b)).map(([className,members])=>`<section class="report-class-group"><div class="report-class-head"><div><span>${uiIcon('classes')}</span><b>${esc(className)}</b></div><em>${members.length} 人</em></div><div class="list">${members.slice().sort((a,b)=>compareStudentNames(a,b)).map(student=>{const count=week.filter(record=>record.studentId===student.id&&normalizedGroupLabel(record.className||studentProfileAtDate(student,record.date).className)===className).length,leaveCount=[...scope.leaves.values()].filter(item=>item.profile.id===student.id&&normalizedGroupLabel(item.profile.className)===className).length;return `<article class="list-row">${avatarMarkup(student)}<div class="row-main"><b>${esc(student.name)}</b><small>${esc(student.studentNo)} · ${esc(student.school||'未填写学校')}</small></div><div class="row-end"><strong>${isAttended?`${count} 次`:'未到'}</strong><small>${isAttended?'该周签到':'该周无签到记录'}${leaveCount?` · 请假 ${leaveCount} 次`:''}</small></div></article>`}).join('')}</div></section>`).join('');
  openModal(`<div class="modal-head"><span>${isAttended?'✓':'!'}</span><div><h2>${isAttended?'该周签到学生':'该周未到学生'}</h2><p>${rangeLabel} · 已按班级分类</p></div></div><div class="report-detail-summary"><strong>${students.length}</strong><div><b>${isAttended?'签到学生':'未到学生'}</b><span>共 ${Object.keys(groups).length} 个班级</span></div></div><div class="report-class-groups">${groupMarkup||empty(isAttended?'▥':'★',isAttended?'该周还没有签到学生':'该周没有未到学生',isAttended?'完成签到后会自动归入对应班级':'该周所有学生都有签到记录')}</div><div class="form-actions report-detail-close"><button type="button" class="primary-btn" id="closeReportStudentDetail">关闭详情</button></div>`);
  $('#modal').classList.add('report-student-detail-modal');$('#closeReportStudentDetail').onclick=closeModal;
}

function openWeeklyRateCalendar(){
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>周历到课率</h2><p>选择全校、班级或老师，查看每一周的到课趋势</p></div></div><div class="week-calendar-controls"><label class="field"><span>统计范围</span><select id="calendarScopeType"><option value="school">全校</option><option value="class">某一个班级</option><option value="teacher">某一位老师</option></select></label><label class="field"><span>选择对象</span><select id="calendarScopeValue"><option value="全校学生">全校学生</option></select></label></div><div class="calendar-scope-summary" id="calendarScopeSummary"></div><div class="weekly-rate-calendar" id="weeklyRateCalendar"></div><div class="form-actions week-calendar-actions"><button type="button" class="secondary-btn" id="closeWeekCalendar">关闭</button></div>`);
  $('#modal').classList.add('weekly-calendar-modal');
  const typeSelect=$('#calendarScopeType'),valueSelect=$('#calendarScopeValue');
  const weeklyStats=availableReportWeekOffsets().map(offset=>({offset,stats:buildWeeklyStats(offset)}));
  const drawWeeks=()=>{const scopeName=typeSelect.value==='school'?'全校':valueSelect.value||'暂无数据';$('#calendarScopeSummary').innerHTML=`<div><b>${esc(scopeName)}</b><span>按历史排课日及班级设置的任课老师计算</span></div><em>请假仍计应到、不算实到；长期不来生效后不计应到；未排课按默认周次数折算。点击某周会保留当前筛选。</em>`;$('#weeklyRateCalendar').innerHTML=weeklyStats.map(({offset,stats})=>{const group=weeklyScope(stats,typeSelect.value,valueSelect.value),range=stats.range,rate=pct(group.records.length,group.expected);return `<button type="button" class="weekly-rate-cell ${offset===0?'current':''} ${offset===selectedReportWeekOffset?'selected':''}" data-calendar-week="${offset}"><span>${offset===0?'本周':offset===-1?'上周':'历史周'}</span><small>${shortDate(range.start)}—${shortDate(range.end)}</small><strong>${reportRate(group)}</strong><div class="week-mini-progress"><i style="width:${rate}%"></i></div><em>${group.records.length} / ${reportCount(group.expected)} 次</em></button>`}).join('');$$('[data-calendar-week]').forEach(button=>button.onclick=()=>{selectedReportWeekOffset=Number(button.dataset.calendarWeek);selectedReportScopeType=typeSelect.value;selectedReportScopeValue=valueSelect.value;closeModal();currentView='report';render();window.scrollTo({top:0,behavior:'smooth'});toast(`已切换到${calendarWeekLabel(selectedReportWeekOffset)}`)})};
  const refreshValues=()=>{const names=typeSelect.value==='school'?['全校']:reportScopeOptions(typeSelect.value);valueSelect.innerHTML=names.length?names.map(name=>`<option value="${esc(name)}" ${name===selectedReportScopeValue?'selected':''}>${esc(name)}</option>`).join(''):'<option value="">暂无可选数据</option>';valueSelect.disabled=typeSelect.value==='school'||!names.length;drawWeeks()};
  typeSelect.value=selectedReportScopeType;typeSelect.onchange=refreshValues;valueSelect.onchange=drawWeeks;$('#closeWeekCalendar').onclick=closeModal;refreshValues();
}

function renderSettings(){return renderLocalSettings()+webMirrorSettingsMarkup()+wechatNoticeSettingsMarkup()+(typeof deviceSyncSettingsMarkup==='function'?deviceSyncSettingsMarkup():'')}
function renderLocalSettings(){
  return `<div class="settings-grid"><section class="panel"><div class="panel-head"><div><h2>基础与后台解锁</h2><p>设置会立即保存在本机</p></div></div><div class="setting-row"><div><b>机构名称</b><small>显示在签到页与应用左上角</small></div><input id="orgNameInput" value="${esc(state.settings.orgName)}"></div><div class="setting-row"><div><b>未排课班级默认每周应到次数</b><small>已排课班级按实际排课日计算；此项仅供未排课班级折算</small></div><input id="weeklyTargetInput" type="number" min="1" max="14" value="${esc(state.settings.weeklyTarget)}"></div><div class="setting-row"><div><b>后台解锁图案</b><small>九宫格图案，仅在本机验证；修改前需验证原图案</small></div><button type="button" class="secondary-btn" id="configurePattern">${state.settings.adminPattern?'修改解锁图案':'设置解锁图案'}</button></div><div class="setting-row"><div><b>保存设置</b><small>基础设置只保存在本机</small></div><button class="primary-btn" id="saveSettings">保存</button></div></section><section class="panel"><div class="panel-head"><div><h2>Excel 与本机数据</h2><p>批量管理学生并备份完整资料</p></div></div><div class="warning-box">Excel 适合查看、打印和批量录入；JSON 完整备份还包含人脸特征与全部本机记录，建议两种都定期保存。</div><div class="setting-row"><div><b>导出 Excel</b><small>学生、班级、签到和课时记录</small></div><button class="primary-btn" id="exportExcel">导出 .xlsx</button></div><div class="setting-row"><div><b>下载学生导入模板</b><small>先按模板填写，可减少导入错误</small></div><button class="secondary-btn" id="exportTemplate">下载模板</button></div><div class="setting-row"><div><b>批量导入学生</b><small>支持 .xlsx、.xls 和 .csv 文件</small></div><button class="secondary-btn" id="batchImport">选择文件</button></div><div class="setting-row"><div><b>完整 JSON 备份</b><small>包含人脸特征，可用于整机恢复</small></div><button class="secondary-btn" id="exportBackup">导出文本</button></div><div class="setting-row"><div><b>恢复 JSON 备份</b><small>从此前的完整备份恢复资料</small></div><button class="secondary-btn" id="importBackup">导入文本</button></div>${!state.students.length?`<div class="setting-row"><div><b>添加演示数据</b><small>体验签到、课时与周报</small></div><button class="secondary-btn" id="seedDemo">添加</button></div>`:''}<div class="setting-row"><div><b>清空本机数据</b><small>此操作无法撤销</small></div><button class="danger-btn" id="resetData">全部清空</button></div></section></div>`;
}

function handleContentAction(event){
  if(handleCourseGroupAction(event))return;if(handleStudentLeaveAction(event,$('#content')))return;const wechatButton=event.target.closest('[data-wechat-student]');
  if(wechatButton&&$('#content').contains(wechatButton))return openWechatStudentSettings(Number(wechatButton.dataset.wechatStudent));
  if(handleClassSpaceAction(event))return;
  const root=$('#content'),target=event.target.closest('[data-list-prev],[data-list-next],[data-dashboard-stat],[data-hours-alert],[data-action],[data-add-class],[data-batch-promote],[data-class-promote],[data-class-edit],[data-class-extra-lessons],[data-class-delete],[data-class-absent],[data-class-students],[data-go],[data-edit],[data-hours],[data-hours-detail],[data-long-term],[data-delete],[data-undo],[data-check],[data-makeup-checkin],[data-report-student-detail],[data-report-lesson],[data-review-lesson],[data-student-class-filter]');
  if(!target||!root.contains(target)||target.disabled)return;
  const data=target.dataset;
  if(data.listPrev!==undefined)return changeListPage(data.listPrev,-1);
  if(data.listNext!==undefined)return changeListPage(data.listNext,1);
  if(data.dashboardStat!==undefined)return openDashboardStatDetail(data.dashboardStat);
  if(data.hoursAlert!==undefined)return openHoursAlertDetail(Number(data.hoursAlert));
  if(data.action==='add-student')return openStudentModal();
  if(data.addClass!==undefined)return openClassModal();
  if(data.batchPromote!==undefined)return openClassPromotionModal();
  if(data.classPromote!==undefined)return openClassPromotionModal(classById(data.classPromote));
  if(data.classEdit!==undefined)return openClassModal(classById(data.classEdit));
  if(data.classExtraLessons!==undefined)return openClassExtraLessons(classById(data.classExtraLessons));
  if(data.classDelete!==undefined)return deleteClass(Number(data.classDelete));
  if(data.classAbsent!==undefined)return openClassAbsentReminder(classById(data.classAbsent),localDate());
  if(data.classStudents!==undefined){const course=classById(data.classStudents);if(!course)return;studentClassFilter=`class:${normalizedGroupLabel(course.name)}`;studentStatusFilter='all';studentSearchQuery='';listPages.students=1;return go('students')}
  if(data.go!==undefined)return go(data.go);
  if(data.edit!==undefined)return openStudentModal(studentById(data.edit));
  if(data.hours!==undefined)return openHoursModal(studentById(data.hours));
  if(data.hoursDetail!==undefined)return openHoursDetail(studentById(data.hoursDetail));
  if(data.longTerm!==undefined)return toggleLongTermAbsent(Number(data.longTerm));
  if(data.delete!==undefined)return deleteStudent(Number(data.delete));
  if(data.undo!==undefined)return undoCheckin(Number(data.undo));
  if(data.check!==undefined)return openCheckin(data.check);
  if(data.makeupCheckin!==undefined)return openMakeupCheckin();
  if(data.reportStudentDetail!==undefined)return openReportStudentDetail(data.reportStudentDetail);
  if(data.reportLesson!==undefined)return openReportLessonDetail(data.reportLesson);
  if(data.reviewLesson!==undefined)return openReportLessonReview(data.reviewLesson);
  if(data.studentClassFilter!==undefined){
    studentClassFilter=data.studentClassFilter;listPages.students=1;
    if(studentClassFilter==='long-term')studentStatusFilter='long-term';else if(studentStatusFilter==='long-term')studentStatusFilter='all';
    render();
  }
}
function bindViewEvents(){
  bindClassSpaceViewEvents();bindCourseGroupView();
  const content=$('#content');content.onclick=handleContentAction;
  content.onkeydown=event=>{
    if(event.key!=='Enter'&&event.key!==' ')return;
    const row=event.target.closest('[data-hours-detail]');
    if(row&&event.target===row){event.preventDefault();openHoursDetail(studentById(row.dataset.hoursDetail))}
  };
  if(currentView==='settings'&&isAdminAuthenticated){mountCampusCodeSetting();mountCameraPositionSetting();mountAutoBackupSetting();mountLocalDataRepairSetting()}
  const search=$('#studentSearch');if(search){search.oninput=scheduleStudentSearch;search.oncompositionend=scheduleStudentSearch}
  if($('#studentClassFilter'))$('#studentClassFilter').onchange=event=>{studentClassFilter=event.target.value;listPages.students=1;if(studentClassFilter==='long-term')studentStatusFilter='long-term';else if(studentStatusFilter==='long-term')studentStatusFilter='all';render()};
  if($('#studentStatusFilter'))$('#studentStatusFilter').onchange=event=>{studentStatusFilter=event.target.value;listPages.students=1;if(studentStatusFilter==='long-term')studentClassFilter='long-term';else if(studentClassFilter==='long-term')studentClassFilter='all';render()};
  mountWechatStudentButtons();
  const actions={saveSettings,configurePattern:openPatternChange,openWechatNotices,openWebMirror,...(typeof openDeviceSync==='function'?{openDeviceSync}:{}),exportExcel,exportTemplate:exportStudentTemplate,batchImport:openBatchImport,exportBackup:openExport,importBackup:openImport,seedDemo,resetData,openWeekCalendar:openWeeklyRateCalendar};
  Object.keys(actions).forEach(id=>{const button=$('#'+id);if(button)button.onclick=actions[id]});
  if($('#reportWeekSelect'))$('#reportWeekSelect').onchange=event=>{selectedReportWeekOffset=Number(event.target.value);render()};
  if($('#reportScopeType'))$('#reportScopeType').onchange=event=>{selectedReportScopeType=event.target.value;selectedReportScopeValue=reportScopeOptions(selectedReportScopeType)[0]||'';render()};
  if($('#reportScopeValue'))$('#reportScopeValue').onchange=event=>{selectedReportScopeValue=event.target.value;render()};
  if($('#hoursClassFilter'))$('#hoursClassFilter').onchange=event=>{hoursClassFilter=event.target.value;listPages.hours=1;render()};
  if($('#hoursSortOrder'))$('#hoursSortOrder').onchange=event=>{hoursSortOrder=event.target.value;listPages.hours=1;render()};
}

function mountAutoBackupSetting(){
  const exportButton=$('#exportBackup');if(!exportButton||$('#createAutoBackup'))return;const info=nativeBackupInfo(),row=document.createElement('div');row.className='setting-row auto-backup-setting-row';row.innerHTML=`<div><b>每日自动完整备份</b><small id="autoBackupStatus">${info.timeLabel?`最近备份：${esc(info.timeLabel)} · ${esc(info.fileName||'')}`:'尚未生成自动备份'}</small></div><button type="button" class="primary-btn" id="createAutoBackup">立即备份</button>`;exportButton.closest('.setting-row').before(row);$('#createAutoBackup').onclick=()=>{const button=$('#createAutoBackup');button.disabled=true;button.textContent='正在备份';if(!createNativeBackup('手动备份',true)){button.disabled=false;button.textContent='立即备份';toast('当前设备无法创建自动备份，请使用完整 JSON 备份',true);return}setTimeout(()=>{button.disabled=false;button.textContent='立即备份'},1600)};
}

function mountCampusCodeSetting(){
  const orgInput=$('#orgNameInput');if(!orgInput||$('#campusCodeInput'))return;
  const row=document.createElement('div');row.className='setting-row campus-code-row';row.innerHTML=`<div><b>校区编号</b><small>一至两位数字，保存后自动补足两位</small></div><div class="campus-code-control"><input id="campusCodeInput" inputmode="numeric" maxlength="2" value="${esc(state.settings.campusCode||'01')}" aria-label="校区编号"><em id="nextStudentNoPreview">下一个新生学号：${esc(nextStudentNo())}</em></div>`;
  orgInput.closest('.setting-row').after(row);
  $('#campusCodeInput').oninput=event=>{event.target.value=event.target.value.replace(/\D/g,'').slice(0,2);const preview=event.target.value?nextStudentNo(event.target.value):'';$('#nextStudentNoPreview').textContent=preview?`下一个新生学号：${preview}`:'请输入校区编号'};
}

function deviceOrientation(){
  try{
    if(window.AndroidBridge&&typeof AndroidBridge.getScreenOrientation==='function'){
      const nativeOrientation=String(AndroidBridge.getScreenOrientation()||'');
      if(nativeOrientation==='landscape'||nativeOrientation==='portrait')return nativeOrientation;
    }
  }catch(error){}
  return window.innerWidth>=window.innerHeight?'landscape':'portrait';
}
function syncScreenOrientationClass(){
  const orientation=deviceOrientation();
  if(!document.body.classList.contains(`screen-${orientation}`)){document.body.classList.remove('screen-landscape');document.body.classList.remove('screen-portrait');document.body.classList.add(`screen-${orientation}`)}
  return orientation;
}
function validCameraEdge(value,fallback='top'){return CAMERA_EDGES.includes(value)?value:fallback}
function cameraSettingKey(orientation){return orientation==='landscape'?'cameraLandscapeEdge':'cameraPortraitEdge'}
function currentCameraEdge(){return validCameraEdge(state.settings[cameraSettingKey(deviceOrientation())])}
function cameraPositionSummary(){return `横屏：${CAMERA_EDGE_LABELS[validCameraEdge(state.settings.cameraLandscapeEdge)]} · 竖屏：${CAMERA_EDGE_LABELS[validCameraEdge(state.settings.cameraPortraitEdge)]}`}
function clearFacePopupPosition(){
  const backdrop=$('#modalBackdrop');if(!backdrop)return;
  ['face-popup-positioned','camera-orientation-landscape','camera-orientation-portrait'].concat(CAMERA_EDGES.map(edge=>`camera-edge-${edge}`)).forEach(className=>backdrop.classList.remove(className));
  backdrop.removeAttribute('data-camera-edge');backdrop.removeAttribute('data-camera-orientation');
}
function applyFacePopupPosition(){
  if(!document.body.classList.contains('face-dialog-open'))return;
  const backdrop=$('#modalBackdrop'),orientation=deviceOrientation(),edge=currentCameraEdge();clearFacePopupPosition();
  backdrop.classList.add('face-popup-positioned');backdrop.classList.add(`camera-edge-${edge}`);backdrop.classList.add(`camera-orientation-${orientation}`);
  backdrop.setAttribute('data-camera-edge',edge);backdrop.setAttribute('data-camera-orientation',orientation);
}
function playCameraPopupAnimation(){const modal=$('#modal');if(!modal||!modal.classList.contains('face-checkin-modal'))return;modal.classList.remove('camera-popup-animate');modal.classList.add('camera-popup-waiting');void modal.offsetWidth;requestAnimationFrame(()=>requestAnimationFrame(()=>{if(!modal.classList.contains('face-checkin-modal')||$('#modalBackdrop').classList.contains('hidden'))return;modal.classList.remove('camera-popup-waiting');modal.classList.add('camera-popup-animate')}))}
function mountCameraPositionSetting(){
  const saveButton=$('#saveSettings');if(!saveButton||$('#configureCameraPosition'))return;
  const row=document.createElement('div');row.className='setting-row camera-position-setting-row';row.innerHTML=`<div><b>平板摄像头与签到窗口</b><small id="cameraPositionSummary">${esc(cameraPositionSummary())}</small></div><button type="button" class="secondary-btn" id="configureCameraPosition">重新设置</button>`;
  saveButton.closest('.setting-row').before(row);$('#configureCameraPosition').onclick=()=>openCameraPositionSetup(false);
}
function openCameraPositionSetup(required=!state.settings.cameraPositionConfigured,onSaved=null){
  cameraSetupCallback=typeof onSaved==='function'?onSaved:null;
  let portrait=validCameraEdge(state.settings.cameraPortraitEdge),landscape=validCameraEdge(state.settings.cameraLandscapeEdge);
  const currentOrientation=deviceOrientation();
  const edgeButtons=orientation=>CAMERA_EDGES.map(edge=>{const selected=(orientation==='portrait'?portrait:landscape)===edge;return `<button type="button" data-camera-mode="${orientation}" data-camera-edge="${edge}" class="${selected?'selected':''}" aria-pressed="${selected?'true':'false'}">${CAMERA_EDGE_LABELS[edge]}</button>`}).join('');
  openModal(`<div class="modal-head"><span>▣</span><div><h2>设置平板摄像头位置</h2><p>分别选择正常横屏和竖屏使用时，摄像头所在的屏幕边缘</p></div></div><div class="camera-setup-guide"><b>为什么要设置？</b><span>人脸签到窗口会自动靠近摄像头弹出，学生看向窗口时也能自然地看向镜头。</span></div><div class="camera-setup-grid"><section class="camera-orientation-card ${currentOrientation==='landscape'?'current':''}"><div class="camera-device-preview landscape" data-camera-preview="landscape"><i class="camera-dot edge-${landscape}"></i><span>横屏</span><small>${currentOrientation==='landscape'?'当前方向':'旋转后方向'}</small></div><div><b>横屏时摄像头在 <em id="landscapeCameraChoice">已选：${CAMERA_EDGE_LABELS[landscape]}</em></b><div class="camera-edge-options">${edgeButtons('landscape')}</div></div></section><section class="camera-orientation-card ${currentOrientation==='portrait'?'current':''}"><div class="camera-device-preview portrait" data-camera-preview="portrait"><i class="camera-dot edge-${portrait}"></i><span>竖屏</span><small>${currentOrientation==='portrait'?'当前方向':'旋转后方向'}</small></div><div><b>竖屏时摄像头在 <em id="portraitCameraChoice">已选：${CAMERA_EDGE_LABELS[portrait]}</em></b><div class="camera-edge-options">${edgeButtons('portrait')}</div></div></section></div><p class="camera-setup-tip">提示：请选择平板正常摆放时，前置摄像头实际靠近的那一边。</p><div class="form-actions camera-setup-actions">${required?'':'<button type="button" class="secondary-btn" id="cancelCameraSetup">取消</button>'}<button type="button" class="primary-btn" id="saveCameraPosition">保存并完成设置</button></div>`);
  $('#modal').classList.add('camera-setup-modal');
  if(required){const close=$('.modal-close');if(close)close.remove();$('#modalBackdrop').onclick=null}else $('#cancelCameraSetup').onclick=closeModal;
  $$('[data-camera-mode][data-camera-edge]').forEach(button=>button.onclick=()=>{
    const orientation=button.getAttribute('data-camera-mode'),edge=button.getAttribute('data-camera-edge');
    if((orientation!=='portrait'&&orientation!=='landscape')||!CAMERA_EDGES.includes(edge))return;
    if(orientation==='portrait')portrait=edge;else landscape=edge;
    $$(`[data-camera-mode="${orientation}"]`).forEach(item=>{const selected=item===button;if(selected)item.classList.add('selected');else item.classList.remove('selected');item.setAttribute('aria-pressed',selected?'true':'false')});
    const dot=$(`[data-camera-preview="${orientation}"] .camera-dot`),choice=$(`#${orientation}CameraChoice`);if(dot)dot.className=`camera-dot edge-${edge}`;if(choice)choice.textContent=`已选：${CAMERA_EDGE_LABELS[edge]}`;
  });
  $('#saveCameraPosition').onclick=()=>{state.settings.cameraPortraitEdge=validCameraEdge(portrait);state.settings.cameraLandscapeEdge=validCameraEdge(landscape);state.settings.cameraPositionConfigured=true;if(!saveState())return;const callback=cameraSetupCallback;cameraSetupCallback=null;closeModal();render();toast(`摄像头位置已保存：${cameraPositionSummary()}`);if(callback)setTimeout(callback,180)};
}

function go(view){
  if(!Object.prototype.hasOwnProperty.call(views,view))return;
  if(!isAdminAuthenticated&&view!=='checkin')return openAdminLogin();
  if(currentView===view&&mountedPageKey===pageCacheKey(view))return;
  currentView=view;
  // Navigation must finish in the input event, without waiting for a frame or timer.
  render();window.scrollTo(0,0);
}
function openAdminLogin(){
  openPatternEntry();
}
function adminCredentialsMatch(username,password){return !state.settings.adminPattern&&state.settings.adminAuthMode!=='pattern'&&String(username||'').trim()===String(state.settings.adminUser||'admin')&&String(password||'')===String(state.settings.adminPassword||'123456')}
function exitAdmin(){invalidatePageCache();isAdminAuthenticated=false;currentView='checkin';closeModal();render();window.scrollTo({top:0});toast('已退出后台，返回学生签到')}
function openMoreMenu(){openModal(`<div class="modal-head"><span>•••</span><div><h2>更多教务功能</h2><p>课时、报表与本机设置</p></div></div><div class="more-grid"><button data-more-view="hours"><span>${uiIcon('hours')}</span><b>课时管理</b><small>余额与变化记录</small></button><button data-more-view="report"><span>${uiIcon('report')}</span><b>每周报告</b><small>到课率与统计</small></button><button data-more-view="settings"><span>⚙</span><b>设置与数据</b><small>Excel、备份与机构设置</small></button></div>`);bindImmediateNavigation($('.more-grid'),'[data-more-view]',button=>{closeModal();go(button.dataset.moreView)})}
function openModal(html){faceScanToken++;clearTimeout(checkinSuccessTimer);checkinSuccessTimer=null;document.body.classList.remove('face-dialog-open');if(activeStream){activeStream.getTracks().forEach(track=>track.stop());activeStream=null}const modal=$('#modal');clearFacePopupPosition();modal.className='modal';modal.innerHTML=`<button class="modal-close" aria-label="关闭">×</button>${html}`;$('#modalBackdrop').classList.remove('hidden');$('.modal-close').onclick=closeModal;$('#modalBackdrop').onclick=e=>{if(e.target.id==='modalBackdrop'){if(skipOpeningBackdropClick){skipOpeningBackdropClick=false;return}closeModal()}};}
function closeModal(){ faceScanToken++;cameraSetupCallback=null;clearTimeout(checkinSuccessTimer);checkinSuccessTimer=null;document.body.classList.remove('face-dialog-open');clearFacePopupPosition();if(activeStream){activeStream.getTracks().forEach(t=>t.stop());activeStream=null} $('#modalBackdrop').classList.add('hidden'); }

function openClassAbsentReminder(course,date=localDate()){
  if(!course)return toast('没有找到对应班级，请进入后台检查班级设置',true);
  if(lessonCycleWeeks(classScheduleProfile(course,date))===2&&!classHasLessonOnDate(course,date)){openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>隔周休课日</h2><p>${esc(course.name)} · ${esc(date)}</p></div></div><div class="warning-box">本日没有排课，不计应到或缺勤，也不会触发未签到提醒。如有实际加课，请在“加课 / 调课”登记。</div><div class="form-actions"><button type="button" class="secondary-btn" id="closeRestDay">关闭</button></div>`);$('#closeRestDay').onclick=closeModal;return}
  const leaveGroup=classSpaceContext(date,course.id).makeGroup(course),activeMembers=leaveGroup.members,absent=leaveGroup.absent,dateLabel=date===localDate()?'今天':date;
  openModal(`<div class="modal-head"><span>🔔</span><div><h2>${esc(course.name)} · 未签到提醒</h2><p>${esc(dateLabel)} ${esc(classScheduleLabel(course))} · ${esc(course.teacher||'未安排老师')}</p></div></div><div class="reminder-class-summary"><div><span>上课教室</span><b>${esc(course.classroom||'暂未设置教室')}</b></div><div><span>正常在读</span><b>${activeMembers.length} 人</b></div><div><span>尚未签到</span><b class="${absent.length?'warn':'ok'}">${absent.length} 人</b></div></div>${absent.length?`<div class="reminder-absent-list">${absent.map(student=>`<article>${avatarMarkup(student)}<div><b>${esc(student.name)}</b><small>${esc(student.studentNo||'系统学号')} ${student.contact?`· ${esc(student.contact)}`:''}</small></div><em>未签到</em>${leaveActionMarkup(student,course.id,date)}</article>`).join('')}</div><p class="reminder-detail-note">已自动排除长期不来学生。学生完成签到后，再次打开此页面会显示最新名单。</p>`:empty('✓',leaveGroup.leaves.length?'本班暂无待签到学生':'本班学生均已签到','已请假与长期不来学生已排除催签到名单')}${leaveRecordsMarkup(course.id,date)}<div class="form-actions reminder-detail-actions"><button type="button" class="secondary-btn" id="closeReminderDetail">关闭</button><button type="button" class="primary-btn" id="goReminderCheckin">前往签到界面</button></div>`);
  $('#modal').classList.add('attendance-reminder-modal');bindStudentLeaveButtons($('#modal'));$('#closeReminderDetail').onclick=closeModal;$('#goReminderCheckin').onclick=()=>{closeModal();currentView='checkin';render();window.scrollTo({top:0,behavior:'smooth'})};
}
window.openAttendanceReminder=(classId,date)=>setTimeout(()=>openClassAbsentReminder(classById(classId),date||localDate()),180);
function toast(message,error=false){ const el=$('#toast');el.textContent=message;el.className=`toast${error?' error':''}`;clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.add('hidden'),2600); }
function openDangerConfirmation({icon='!',title,description,subject,subjectMeta='',impactTitle,impactText,confirmLabel='确认删除',verifyText='',onConfirm}){
  openModal(`<div class="modal-head"><span>${esc(icon)}</span><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div></div><div class="danger-confirm-subject"><div><span>即将处理</span><b>${esc(subject)}</b><small>${esc(subjectMeta)}</small></div></div><div class="undo-confirm-impact"><span>${uiIcon('alert')}</span><div><b>${esc(impactTitle)}</b><small>${esc(impactText)}</small></div></div>${verifyText?`<label class="danger-confirm-verify"><span>为防止误操作，请输入“${esc(verifyText)}”</span><input id="dangerVerifyInput" autocomplete="off" placeholder="${esc(verifyText)}"></label>`:''}<div class="form-actions undo-confirm-actions"><button type="button" class="secondary-btn" id="cancelDangerAction">取消</button><button type="button" class="danger-btn" id="confirmDangerAction" ${verifyText?'disabled':''}>${esc(confirmLabel)}</button></div>`);
  $('#modal').classList.add('danger-confirm-modal');
  $('#cancelDangerAction').onclick=closeModal;
  const confirmButton=$('#confirmDangerAction'),verifyInput=$('#dangerVerifyInput');
  if(verifyInput){verifyInput.oninput=()=>{confirmButton.disabled=verifyInput.value.trim()!==verifyText};setTimeout(()=>verifyInput.focus(),80)}
  confirmButton.onclick=()=>{if(verifyInput&&verifyInput.value.trim()!==verifyText)return;confirmButton.disabled=true;try{if(onConfirm()===false)confirmButton.disabled=false}catch(error){confirmButton.disabled=false;toast(error&&error.message||'操作失败，请重试',true)}};
}

function openClassExtraLessons(course){
  if(!course)return;
  const lessons=classExtraLessons(course);
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>${esc(course.name)} · 加课日期</h2><p>补录实际加课安排，同步计算周报与周历的应到次数</p></div></div><div class="warning-box">按该日期实际在本班上课的学生计算，排除当天长期不来、未入学及已转班学生。同一天最多计一次，与常规排课同日不重复；未排课班级为默认周次数加额外课次。不自动补签、不扣课时。</div><form id="extraLessonForm" class="simple-form"><label class="field"><span>实际加课日期 *</span><input id="extraLessonDate" type="date" required value="${localDate()}"></label><label class="field"><span>加课说明（选填）</span><input id="extraLessonNote" maxlength="100" placeholder="如：国庆补课、临时加课"></label><button type="submit" class="primary-btn">保存加课日期</button></form><section class="extra-lesson-list"><h3>已录入 ${lessons.length} 个日期</h3>${lessons.map(lesson=>`<article><div><b>${esc(lesson.date)}</b><small>${esc(lesson.note||'临时加课')}</small></div><button type="button" class="mini-btn warn" data-remove-extra-lesson="${lesson.date}">移除</button></article>`).join('')||'<p>尚未录入加课日期；不会根据签到记录自动猜测。</p>'}</section><p class="extra-lesson-note">加课日期会显示在工作台课堂安排中。已开启班级提醒并设置开课时间时，加课与调课沿用该时间，原停课日期不再提醒。</p><div class="form-actions"><button type="button" class="secondary-btn" id="closeExtraLessons">关闭</button></div>`);
  $('#extraLessonForm').onsubmit=event=>{event.preventDefault();const result=saveExtraClassLesson(course,$('#extraLessonDate').value,$('#extraLessonNote').value);if(!result.ok)return toast(result.error,true);render();openClassExtraLessons(classById(course.id));toast('加课日期已保存，应到统计已更新')};
  $('#closeExtraLessons').onclick=()=>{closeModal();render()};
  $$('[data-remove-extra-lesson]').forEach(button=>button.onclick=()=>{const date=button.dataset.removeExtraLesson;openDangerConfirmation({icon:'▦',title:'移除加课日期',description:'只调整排课统计，不删除签到或课时记录',subject:`${course.name} · ${date}`,impactTitle:'该日期不再作为额外应到安排',impactText:'常规每周排课不受影响。如有真实加课，请保留该日期，避免应到次数不足。',confirmLabel:'确认移除',onConfirm:()=>{if(classLeaveConflict(course,[date])){toast('当天已有学生请假，请先核对并撤销请假再移除加课',true);return false}state.settings.extraClassLessons=extraClassLessons().filter(lesson=>!(String(lesson.classId)===String(course.id)&&lesson.date===date));if(!saveState())return false;render();openClassExtraLessons(classById(course.id));toast('已移除额外日期，签到记录保持不变')}})});
  mountClassRescheduleEditor(course);
}
function mountClassRescheduleEditor(course){
  $('#modal h2').textContent=`${course.name} · 排课与调课`;
  const form=$('#extraLessonForm'),extraList=$('.extra-lesson-list'),warning=$('#modal .warning-box'),extraNote=$('.extra-lesson-note'),tabs=document.createElement('div'),section=document.createElement('section');
  tabs.className='schedule-editor-tabs';tabs.innerHTML='<button type="button" class="active" id="showExtraLessons">额外加课</button><button type="button" id="showReschedules">调课 / 跨周补课</button>';warning.before(tabs);
  const changes=reschedulesForClass({classId:course.id}).slice().sort((a,b)=>b.fromDate.localeCompare(a.fromDate));section.className='reschedule-editor hidden';
  section.innerHTML=`<div class="warning-box">成对登记“原日期停课 → 新日期补上”。仅调课涉及的两周按下方确认的常规星期排课，再应用调课；常规课仍按每周或隔周周期保留，额外补上课单独计次。不自动改签到日期、不补签、不扣课。同班同日最多一次。</div><form id="classRescheduleForm" class="simple-form"><label class="field"><span>这两周的常规上课星期 *</span><select id="rescheduleWeekday" required><option value="">请选择该班平时上课星期</option>${CLASS_WEEKDAYS.slice(1).map((label,index)=>`<option value="${index+1}" ${Number(course.scheduleWeekday)===index+1?'selected':''}>${label}</option>`).join('')}</select></label><div class="reschedule-date-grid"><label class="field"><span>原上课日期（当天停课） *</span><input type="date" id="rescheduleFromDate" required></label><label class="field"><span>新上课日期（实际补上） *</span><input type="date" id="rescheduleToDate" required></label></div><label class="field"><span>调课说明（选填）</span><input id="rescheduleNote" maxlength="100" placeholder="如：上周末停课，顺延至本周一"></label><button type="submit" class="primary-btn">保存调课安排</button></form><div class="extra-lesson-list"><h3>已登记 ${changes.length} 次调课</h3>${changes.map(change=>`<article><div><b>${esc(change.fromDate)} → ${esc(change.toDate)}</b><small>${esc(change.note||'原日停课，新日补上')} · 常规每${CLASS_WEEKDAYS[change.regularWeekday]}</small></div><button type="button" class="mini-btn warn" data-remove-reschedule="${change.fromDate}">撤销安排</button></article>`).join('')||'<p>尚未登记调课。跨周补课请在此登记，不要仅添加加课日期。</p>'}</div>`;
  form.before(section);
  const selectMode=mode=>{[warning,form,extraList,extraNote].forEach(element=>element.classList.toggle('hidden',mode==='reschedule'));section.classList.toggle('hidden',mode!=='reschedule');$('#showExtraLessons').classList.toggle('active',mode!=='reschedule');$('#showReschedules').classList.toggle('active',mode==='reschedule')};
  $('#showExtraLessons').onclick=()=>selectMode('extra');$('#showReschedules').onclick=()=>selectMode('reschedule');
  $('#classRescheduleForm').onsubmit=event=>{event.preventDefault();const result=saveClassReschedule(course,$('#rescheduleFromDate').value,$('#rescheduleToDate').value,$('#rescheduleWeekday').value,$('#rescheduleNote').value);if(!result.ok)return toast(result.error,true);render();openClassExtraLessons(classById(course.id));$('#showReschedules').click();toast('调课已保存，原周与新周的应到和逐次周报已更新')};
  $$('[data-remove-reschedule]',section).forEach(button=>button.onclick=()=>{const fromDate=button.dataset.removeReschedule;openDangerConfirmation({icon:'▦',title:'撤销调课安排',description:'恢复原日期排课，取消本次转移的新日期',subject:`${course.name} · 原 ${fromDate}`,impactTitle:'两周应到统计会重新计算',impactText:'只撤销排课安排，不删除或移动任何已有签到、课时流水。请确认实际课程已恢复。',confirmLabel:'确认撤销安排',onConfirm:()=>{const change=reschedulesForClass({classId:course.id}).find(item=>item.fromDate===fromDate);if(change&&classLeaveConflict(course,[change.fromDate,change.toDate])){toast('相关日期已有请假，请先核对并撤销请假再撤销调课',true);return false}if(change&&personalClassScheduleConflict(course,change.fromDate,change.toDate)){toast('该安排仍有关联的个人调课，请先撤销个人安排',true);return false}state.settings.classReschedules=classReschedules().filter(change=>!(String(change.classId)===String(course.id)&&change.fromDate===fromDate));if(!saveState())return false;render();openClassExtraLessons(classById(course.id));$('#showReschedules').click();toast('已撤销调课，原签到和课时未改变')}})});
  $$('[data-remove-extra-lesson]',extraList).forEach(button=>{const previous=button.onclick;button.onclick=()=>{if(reschedulesForClass({classId:course.id}).some(change=>change.fromDate===button.dataset.removeExtraLesson)||personalClassDates(course).some(change=>change.fromDate===button.dataset.removeExtraLesson))return toast('该日期已参与整班或个人调课，请先撤销相关安排再移除',true);previous()}});
}
function openClassModal(course=null){
  const c=course||{name:'',teacher:'',schedule:'',classroom:'',scheduleWeekday:0,scheduleStart:'',reminderEnabled:false},oldName=c.name,weekdayOptions=CLASS_WEEKDAYS.slice(1).map((label,index)=>`<option value="${index+1}" ${Number(c.scheduleWeekday)===index+1?'selected':''}>${label}</option>`).join('');
  openModal(`<div class="modal-head"><span>${uiIcon('classes')}</span><div><h2>${course?'编辑班级':'新增班级'}</h2><p>设置准确的上课星期和时间后，可在开课 5 分钟后提醒未签到学生</p></div></div><form id="classForm" class="form-grid"><label class="field span-2"><span>班级名称 *</span><input name="name" required placeholder="如：Python 基础 A 班" value="${esc(c.name)}"></label><label class="field"><span>授课老师 *</span><input name="teacher" required placeholder="如：王老师" value="${esc(c.teacher)}"></label><label class="field"><span>上课教室</span><input name="classroom" placeholder="如：编程教室 1" value="${esc(c.classroom)}"></label><section class="class-reminder-editor span-2"><div class="class-reminder-editor-head"><div><b>开课 5 分钟未签到提醒</b><small>完全离线，由当前平板发出通知和提示音</small></div><label class="reminder-switch"><input id="classReminderEnabled" name="reminderEnabled" type="checkbox" value="1" ${c.reminderEnabled?'checked':''}><span></span><b>开启</b></label></div><div class="class-reminder-time-grid"><label class="field"><span>上课星期</span><select name="scheduleWeekday"><option value="0">请选择</option>${weekdayOptions}</select></label><label class="field"><span>开课时间</span><input name="scheduleStart" type="time" value="${esc(c.scheduleStart||'')}"></label></div><p>提醒会自动排除“长期不来”学生；学生签到后，未签到名单会同步更新。</p></section><label class="field span-2"><span>上课安排备注（选填）</span><input name="schedule" placeholder="如：每周六两课时，节假日另行通知" value="${esc(c.schedule||'')}"></label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="primary-btn" type="submit">${course?'保存修改':'创建班级'}</button></div></form>`);
  $('#cancelModal').onclick=closeModal;
  mountClassCourseEditor(c);mountClassCycleEditor(c);mountClassTimingEditor(c);
  $('#classForm').onsubmit=e=>{
    e.preventDefault();const form=new FormData(e.target),obj=formEntriesObject(form);
    ['name','teacher','classroom','schedule','scheduleStart'].forEach(key=>obj[key]=String(obj[key]||'').trim());obj.scheduleWeekday=Number(obj.scheduleWeekday||0);obj.reminderEnabled=form.has('reminderEnabled');
    const grouping=courseFormResult(form,course);if(grouping.error)return toast(grouping.error,true);Object.assign(obj,grouping.fields);if(!course&&obj.name&&obj.courseName&&!obj.name.startsWith(obj.courseName))obj.name=obj.courseName+' · '+obj.name;const cycle=classCycleFormResult(form,course);if(cycle.error)return toast(cycle.error,true);Object.assign(obj,cycle.fields);
    if(obj.reminderEnabled&&(!obj.scheduleWeekday||!/^\d{2}:\d{2}$/.test(obj.scheduleStart)))return toast('开启提醒前，请选择上课星期和开课时间',true);
    if(!obj.name||!obj.teacher)return toast('请填写班级名称和授课老师',true);
    const duplicate=state.classes.find(x=>normalizedGroupLabel(x.name)===normalizedGroupLabel(obj.name)&&x.id!==(course&&course.id));if(duplicate)return toast('班级名称已存在，请换一个名称',true);
    const timing=classTimingFormResult(form);if(timing.error)return toast(timing.error,true);const applyTiming=!course||course.lessonDurationMinutes!==undefined||String(course.scheduleStart||'')!==obj.scheduleStart||classLessonDuration(course)!==timing.fields.lessonDurationMinutes||classLessonDuration(course)!==defaultLessonDuration({...course,...grouping.fields});delete obj.lessonDurationMinutes;
    if(applyTiming){Object.assign(obj,timing.fields);try{obj.classTimingHistory=classTimingHistoryForSave(course,{...course,...obj},localDate())}catch(error){return toast(error.message,true)}}
    if(course){const nextCourse={...course,...obj};if(classGroupingMetadataOnly(course,nextCourse))Object.assign(course,grouping.fields,applyTiming?{...timing.fields,classTimingHistory:obj.classTimingHistory}:{});else{recordClassCycleHistory(course,nextCourse);if(classGroupingChangesEnrollment(course,nextCourse))state.students.filter(s=>s.className===oldName).forEach(s=>{updateStudentEnrollment(s,nextCourse);s.className=obj.name;s.teacher=obj.teacher});Object.assign(course,obj)}}else state.classes.push({...obj,id:uid(),createdAt:new Date().toISOString()});
    if(!saveState())return false;closeModal();render();toast(obj.reminderEnabled?'班级已保存，按实际课日开课 5 分钟后将在本机提醒':course?'班级信息已更新':'班级创建成功');
  };
}
function openClassPromotionModal(sourceCourse=null){
  const populated=state.classes.filter(course=>state.students.some(student=>student.className===course.name));
  if(state.classes.length<2)return toast('请先建立至少两个班级，再使用集体升班',true);
  if(!populated.length)return toast('目前没有包含学生的班级',true);
  const initialSource=sourceCourse&&populated.some(course=>course.id===sourceCourse.id)?sourceCourse:populated[0];
  const optionMarkup=(courses,selectedId)=>courses.map(course=>`<option value="${course.id}" ${course.id===selectedId?'selected':''}>${esc(course.name)} · ${esc(course.teacher||'未安排老师')}</option>`).join('');
  const initialTarget=state.classes.find(course=>course.id!==initialSource.id);
  openModal(`<div class="modal-head"><span>⇧</span><div><h2>班级集体升班</h2><p>一次性将原班全部学生转入目标班级</p></div></div><form id="promotionForm" class="simple-form"><div class="promotion-route"><div><small>原班级</small><b id="promotionSourceName">${esc(initialSource.name)}</b><span id="promotionStudentCount">${state.students.filter(student=>student.className===initialSource.name).length} 名学生</span></div><i>→</i><div><small>目标班级</small><b id="promotionTargetName">${esc(initialTarget.name)}</b><span id="promotionTeacherName">${esc(initialTarget.teacher||'未安排老师')}</span></div></div><div class="form-grid"><label class="field"><span>选择原班级 *</span><select id="promotionSource" name="sourceId" required>${optionMarkup(populated,initialSource.id)}</select></label><label class="field"><span>选择目标班级 *</span><select id="promotionTarget" name="targetId" required>${optionMarkup(state.classes,initialTarget.id)}</select></label></div><div class="promotion-notice"><b>升班后将自动完成</b><span>学生的班级改为目标班级，并同步更新为目标班级的授课老师；原班级资料会保留为空班。</span></div><label class="promotion-confirm"><input id="promotionConfirm" type="checkbox">我已核对原班级和目标班级，确认执行集体升班</label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="promotion-btn submit" type="submit">确认集体升班</button></div></form>`);
  $('#cancelModal').onclick=closeModal;
  const sourceSelect=$('#promotionSource'),targetSelect=$('#promotionTarget');
  const refreshPromotion=()=>{const source=classById(sourceSelect.value),target=classById(targetSelect.value),members=source?state.students.filter(student=>student.className===source.name):[];$('#promotionSourceName').textContent=source&&source.name||'未选择';$('#promotionStudentCount').textContent=`${members.length} 名学生`;$('#promotionTargetName').textContent=target&&target.name||'未选择';$('#promotionTeacherName').textContent=target&&target.teacher||'未安排老师'};
  sourceSelect.onchange=()=>{if(sourceSelect.value===targetSelect.value){const replacement=state.classes.find(course=>String(course.id)!==sourceSelect.value);if(replacement)targetSelect.value=String(replacement.id)}refreshPromotion()};targetSelect.onchange=refreshPromotion;
  $('#promotionForm').onsubmit=event=>{event.preventDefault();const source=classById(sourceSelect.value),target=classById(targetSelect.value);if(!source||!target)return toast('请选择有效的原班级和目标班级',true);if(source.id===target.id)return toast('原班级和目标班级不能相同',true);const members=state.students.filter(student=>student.className===source.name);if(!members.length)return toast('原班级中没有学生，无需升班',true);if(!$('#promotionConfirm').checked)return toast('请先勾选确认，再执行集体升班',true);members.forEach(student=>{updateStudentEnrollment(student,target);student.className=target.name;if(target.teacher)student.teacher=target.teacher});if(!saveState())return false;closeModal();render();toast(`已将 ${members.length} 名学生从 ${source.name} 升入 ${target.name}`)};
}
function deleteClass(id){
  const course=classById(id);if(!course)return;const members=state.students.filter(s=>s.className===course.name);if(members.length)return toast(`该班还有 ${members.length} 名学生，请先调整学生班级`,true);
  openDangerConfirmation({icon:'▦',title:'删除班级',description:'请确认要删除的班级信息',subject:course.name,subjectMeta:`${course.teacher||'未安排老师'} · ${course.classroom||'未填写教室'}`,impactTitle:'班级资料将被删除',impactText:'该班目前没有学生。删除前会保存完整恢复备份，不会影响其他班级和签到记录。',confirmLabel:'确认删除班级',onConfirm:()=>{if(!requireSafetyBackup('删除班级前备份'))return false;state.classes=state.classes.filter(c=>c.id!==id);if(!saveState())return false;closeModal();render();toast('班级已删除')}});
}

function bindStudentFaceEnrollment(photoInput,photoButton,sourceInput,onProfile,onRemove){
  const token=faceScanToken;let task=0,busy=false;
  const form=$('#studentForm'),alive=id=>token===faceScanToken&&task===id&&$('#studentForm')===form;
  const stop=stream=>{if(stream)stream.getTracks().forEach(track=>track.stop())};
  const showProfile=profile=>{onProfile(profile);sourceInput.value=profile.faceProfileSource;$('#facePreview').innerHTML=`<img src="${profile.faceImage}" alt="已采集的人脸预览">`;$('#facePreview').classList.remove('hidden');$('#faceCameraWrap').classList.add('hidden');$('#captureFace').classList.add('hidden');$('#removeFace').classList.remove('hidden');$('#faceEnrollState').textContent='SFace 高精度特征已生成，请保存';$('#faceEnrollState').className='face-status ok';stop(activeStream);activeStream=null};
  photoInput.onchange=async()=>{const file=photoInput.files&&photoInput.files[0];if(!file)return;const id=++task,status=$('#faceEnrollState');busy=true;photoButton.disabled=true;try{status.textContent='正在用 SFace 提取人脸…';const profile=await faceProfileFromPhoto(file);if(!alive(id))return;showProfile(profile);toast('学生照片已导入，请保存学生信息')}catch(error){if(alive(id)){status.textContent='照片导入失败';status.className='face-status warn';toast(error.message||'照片无法用于识别',true)}}finally{if(task===id){busy=false;photoButton.disabled=false}photoInput.value=''}};
  $('#openEnrollCamera').onclick=async()=>{
    if(busy)return;
    const id=++task,status=$('#faceEnrollState');busy=true;
    try{
      if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)throw new Error('当前设备无法打开摄像头');
      status.textContent='加载安卓原生 SFace 模型…';await loadFaceModels();if(!alive(id))return;
      stop(activeStream);activeStream=null;
      const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:720},height:{ideal:720}},audio:false});
      if(!alive(id)){stop(stream);return}activeStream=stream;$('#enrollCamera').srcObject=stream;$('#faceCameraWrap').classList.remove('hidden');$('#facePreview').classList.add('hidden');$('#captureFace').classList.remove('hidden');status.textContent='请正对镜头并保持不动';status.className='face-status';
    }catch(error){if(alive(id)){status.textContent='摄像头或模型启动失败';status.className='face-status warn';toast(error.message||'摄像头启动失败',true)}}finally{if(task===id)busy=false}
  };
  $('#captureFace').onclick=async()=>{
    if(busy)return;
    const video=$('#enrollCamera'),canvas=$('#faceCanvas'),button=$('#captureFace'),status=$('#faceEnrollState');if(!video.videoWidth)return toast('摄像头尚未准备好，请稍候',true);
    const id=++task;busy=true;button.disabled=true;const samples=[],prompts=['请正视镜头','请轻微向左转头','请再次正视镜头','请轻微向右转头','最后请正视镜头'];
    try{
      for(let index=0;index<5;index++){if(!alive(id))return;status.textContent=`${prompts[index]} · ${index+1}/5`;if(index)await delay(500);if(!alive(id))return;const result=await readFaceDescriptor(video);if(!alive(id))return;if(!result)throw new Error('画面中必须只有一张清晰人脸');if(!clearFaceFrame(result,video))throw new Error('请靠近镜头并保持光线充足');samples.push(Array.from(result.descriptor))}
      const side=Math.min(video.videoWidth,video.videoHeight),ctx=canvas.getContext('2d');ctx.save();ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,(video.videoWidth-side)/2,(video.videoHeight-side)/2,side,side,0,0,canvas.width,canvas.height);ctx.restore();
      showProfile({faceImage:canvas.toDataURL('image/jpeg',.68),sfaceDescriptors:samples,faceProfileSource:'camera'});toast('已完成 5 个高精度样本，请保存学生信息');
    }catch(error){if(alive(id)){status.textContent='采集失败';status.className='face-status warn';toast(error.message||'请重新拍摄',true)}}finally{if(task===id)busy=false;button.disabled=false}
  };
  $('#removeFace').onclick=()=>{task++;busy=false;onRemove();sourceInput.value='';stop(activeStream);activeStream=null;$('#faceCameraWrap').classList.add('hidden');$('#captureFace').classList.add('hidden');$('#facePreview').classList.remove('hidden');$('#facePreview').innerHTML='<span>⌾</span><small>等待录入</small>';$('#removeFace').classList.add('hidden');$('#faceEnrollState').textContent='未录入';$('#faceEnrollState').className='face-status';photoButton.disabled=false};
  return{isBusy:()=>busy};
}
function appendHoursAdjustment(student,previousHours,newHours,reason){
  const amount=Number(newHours)-Number(previousHours);if(!amount)return;
  const now=new Date();state.transactions.push({id:uid(),studentId:student.id,amount,reason:String(reason||'学生资料课时调整').trim().slice(0,200),date:localDate(now),time:now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),...attendanceSnapshotForStudent(student)});
}
function openStudentModal(student=null){
  const s=student||{name:'',age:'',gender:'',studentNo:nextStudentNo(),contact:'',school:'',grade:'',className:'',teacher:'',hours:'',faceImage:'',longTermAbsent:false};
  if(!student&&!s.studentNo){toast('当前年份和校区的学生编号已达到 9999，请更换校区编号',true);return}
  let capturedFace=s.faceImage||'';
  let capturedDescriptors=hasSFaceProfile(s)?(Array.isArray(s.sfaceDescriptors)?s.sfaceDescriptors:[]).map(unpackFaceDescriptor).filter(sample=>sample.length===128):[];
  let capturedProfileSource=hasFaceProfile(s)?(s.faceProfileSource||'camera'):'';
  let faceProfileChanged=false;
  const profileReady=capturedDescriptors.length>0;
  openModal(`<div class="modal-head"><span>${uiIcon('students')}</span><div><h2>${student?'编辑学生信息':'录入新学生'}</h2><p>仅姓名、班级、老师和课时为必填，其他资料均可稍后补充</p></div></div><form id="studentForm" class="form-grid"><label class="field"><span>学生姓名 *</span><input name="name" required value="${esc(s.name)}"></label><label class="field student-no-field"><span>学号（系统自动生成）</span><input name="studentNo" readonly value="${esc(s.studentNo)}"><small class="field-hint">${student?'学号由系统生成，为避免签到记录错位不能手动修改':`编号规则：${s.studentNo.slice(0,2)} 年 + 校区 ${s.studentNo.slice(2,4)} + 学生 ${s.studentNo.slice(4)}`}</small></label><label class="field"><span>年龄（选填）</span><input name="age" type="number" min="3" max="25" placeholder="如：10" value="${esc(s.age)}"></label><label class="field"><span>性别（选填）</span><select name="gender"><option value="" ${!s.gender?'selected':''}>未填写</option><option value="男" ${s.gender==='男'?'selected':''}>男</option><option value="女" ${s.gender==='女'?'selected':''}>女</option></select></label><label class="field span-2"><span>学生联系方式（选填）</span><input name="contact" type="tel" inputmode="tel" autocomplete="tel" placeholder="家长手机号或其他联系方式" value="${esc(s.contact||'')}"></label><label class="field"><span>所在学校（选填）</span><input name="school" placeholder="可稍后补充" value="${esc(s.school)}"></label><label class="field"><span>年级（选填）</span><input name="grade" placeholder="如：六年级" value="${esc(s.grade)}"></label><label class="field"><span>在本机构班级 *</span><select id="studentClassName" name="className" required><option value="" disabled ${s.className?'':'selected'}>请选择班级</option>${state.classes.map(c=>`<option value="${esc(c.name)}" ${c.name===s.className?'selected':''}>${esc(c.name)} · ${esc(c.teacher||'未安排老师')}</option>`).join('')}</select></label><label class="field"><span>授课老师 *</span><input id="studentTeacher" name="teacher" required placeholder="选择班级后自动填写" value="${esc(s.teacher)}"></label><label class="field span-2"><span>剩余课时（可为负数） *</span><input name="hours" type="number" step="0.5" required value="${esc(s.hours)}"></label><section class="face-enroll span-2"><div class="face-enroll-head"><div><b>高精度离线人脸录入</b><small>OpenCV SFace 五官校正模型 · 建议采集 5 个不同角度样本</small></div><span id="faceEnrollState" class="face-status ${profileReady?'ok':capturedFace?'warn':''}">${profileReady?'SFace 特征已录入':capturedFace?'签到时自动升级':'未录入'}</span></div><div class="face-enroll-body"><div class="face-preview" id="facePreview">${capturedFace?`<img src="${capturedFace}" alt="已登记人脸">`:'<span>⌾</span><small>等待录入</small>'}</div><div class="face-camera hidden" id="faceCameraWrap"><video id="enrollCamera" autoplay playsinline muted></video><div class="face-guide compact"></div></div><canvas id="faceCanvas" class="hidden" width="80" height="80"></canvas><div class="face-enroll-actions"><button type="button" class="secondary-btn" id="openEnrollCamera">打开摄像头</button><button type="button" class="primary-btn hidden" id="captureFace">采集 5 个高精度样本</button><button type="button" class="danger-btn ${capturedFace?'':'hidden'}" id="removeFace">删除人脸信息</button></div></div></section><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="primary-btn" type="submit">${student?'保存修改':'完成录入'}</button></div></form>`);
  $('.modal-head p').textContent='姓名和课时必填；正常在读需选择班级，长期不来无需分班';
  const adjustmentField=document.createElement('label');adjustmentField.className='field span-2 hidden';adjustmentField.id='hoursAdjustmentField';adjustmentField.innerHTML='<span>课时调整原因（修改余额时必填）</span><input id="hoursAdjustmentReason" placeholder="例如：核对历史余额后更正"><small class="field-hint">修改会自动写入课时变动记录，不会影响已有签到。</small>';const hoursInput=$('#studentForm [name="hours"]');hoursInput.closest('.field').after(adjustmentField);hoursInput.oninput=()=>{const changed=Boolean(student)&&Number(hoursInput.value)!==Number(student.hours);adjustmentField.classList.toggle('hidden',!changed);$('#hoursAdjustmentReason').required=changed};
  const classSelect=$('#studentClassName'),teacherInput=$('#studentTeacher'),classField=classSelect.closest('.field'),teacherField=teacherInput.closest('.field'),preferredClass=s.className||s.pausedClassName||'';
  classSelect.required=false;teacherInput.required=false;classField.id='studentClassField';teacherField.id='studentTeacherField';
  const statusSection=document.createElement('section');statusSection.className='student-enrollment-status span-2';statusSection.innerHTML=`<label class="student-status-choice"><input id="studentLongTermAbsent" type="checkbox" ${isLongTermAbsent(s)?'checked':''}><span><b>设为长期不来学生</b><small>不需要选择班级和老师，独立放入长期不来名单，也不计入签到及到课率</small></span><i></i></label>`;classField.before(statusSection);
  if(preferredClass&&state.classes.some(course=>course.name===preferredClass)){classSelect.value=preferredClass;const preferredCourse=state.classes.find(course=>course.name===preferredClass);teacherInput.value=preferredCourse&&preferredCourse.teacher||s.pausedTeacher||s.teacher||''}
  const refreshEnrollmentFields=()=>{const paused=$('#studentLongTermAbsent').checked;classSelect.disabled=paused;teacherInput.disabled=paused;classField.classList.toggle('student-field-disabled',paused);teacherField.classList.toggle('student-field-disabled',paused);classField.querySelector('span').textContent=paused?'在本机构班级（无需填写）':'在本机构班级 *';teacherField.querySelector('span').textContent=paused?'授课老师（无需填写）':'授课老师 *';if(!paused&&classSelect.value){const course=state.classes.find(item=>item.name===classSelect.value);if(course)teacherInput.value=course.teacher||teacherInput.value}};
  $('#studentLongTermAbsent').onchange=refreshEnrollmentFields;refreshEnrollmentFields();
  $('#cancelModal').onclick=closeModal;
  $('#studentClassName').onchange=()=>{const course=state.classes.find(c=>c.name===$('#studentClassName').value.trim());if(course)$('#studentTeacher').value=course.teacher||''};
  const photoInput=document.createElement('input'),photoButton=document.createElement('button'),sourceInput=document.createElement('input');photoInput.type='file';photoInput.accept='image/jpeg,image/png,image/webp';photoInput.className='hidden';photoInput.id='facePhotoFile';sourceInput.type='hidden';sourceInput.name='faceProfileSource';sourceInput.value=capturedProfileSource;photoButton.type='button';photoButton.className='secondary-btn';photoButton.textContent='从相册选择照片';photoButton.onclick=()=>photoInput.click();$('.face-enroll-actions').insertBefore(photoButton,$('#captureFace'));$('.face-enroll-actions').append(photoInput,sourceInput);$('#captureFace').addEventListener('click',()=>sourceInput.value='camera',{capture:true});$('#removeFace').addEventListener('click',()=>sourceInput.value='',{capture:true});
  const enrollmentController=bindStudentFaceEnrollment(photoInput,photoButton,sourceInput,profile=>{capturedFace=profile.faceImage;capturedDescriptors=profile.sfaceDescriptors;capturedProfileSource=profile.faceProfileSource;faceProfileChanged=true},()=>{capturedFace='';capturedDescriptors=[];capturedProfileSource='';faceProfileChanged=true});
  $('#studentForm').onsubmit=e=>{e.preventDefault();if(enrollmentController.isBusy())return toast('人脸处理尚未完成，请稍候或取消本次录入',true);const previousHours=student?Number(student.hours):0;const adjustmentReason=$('#hoursAdjustmentReason').value.trim();if(student&&Number($('#studentForm [name="hours"]').value)!==previousHours&&!adjustmentReason)return toast('请填写课时调整原因',true);const f=new FormData(e.target),obj=formEntriesObject(f),longTerm=$('#studentLongTermAbsent').checked;['name','studentNo','contact','school','grade','gender'].forEach(key=>obj[key]=String(obj[key]||'').trim());obj.className=String(classSelect.value||'').trim();obj.teacher=String(teacherInput.value||'').trim();obj.age=obj.age===''?'':Number(obj.age);obj.hours=Number(obj.hours);if(!obj.name)return toast('请填写学生姓名',true);if(!Number.isFinite(obj.hours))return toast('请填写有效的剩余课时',true);const course=longTerm?null:state.classes.find(c=>c.name===obj.className);if(!longTerm&&!course)return toast(state.classes.length?'请选择学生所在班级':'请先创建班级，或将该学生设为长期不来',true);if(!longTerm&&!(course.teacher||obj.teacher))return toast('所选班级还没有授课老师，请先完善班级资料',true);if(course){obj.className=course.name;obj.teacher=course.teacher||obj.teacher}obj.faceImage=capturedFace;obj.sfaceDescriptors=compactAnchorFaceSamples(capturedDescriptors);obj.sfaceModelVersion=obj.sfaceDescriptors.length?SFACE_MODEL_VERSION:(faceProfileChanged?0:Number(s.sfaceModelVersion||0));obj.faceSecurityVersion=obj.sfaceDescriptors.length?SFACE_MODEL_VERSION:(faceProfileChanged?0:Number(s.faceSecurityVersion||0));obj.faceRegistered=obj.sfaceDescriptors.length?true:(faceProfileChanged?false:Boolean(s.faceRegistered));obj.faceProfileSource=sourceInput.value||capturedProfileSource;if(faceProfileChanged){obj.faceDescriptors=[];obj.faceAdaptiveDescriptors=[];obj.sfaceAdaptiveDescriptors=[];obj.faceLearningCount=0;obj.lastFaceLearnedAt=''}const duplicate=state.students.find(x=>String(x.studentNo||'').trim()===obj.studentNo&&x.id!==(student&&student.id));if(duplicate)return toast('学号已存在，请检查后重试',true);if(student){const wasLongTerm=isLongTermAbsent(student);if(longTerm&&!wasLongTerm)markStudentLongTerm(student);else if(!longTerm&&wasLongTerm)restoreStudentToCourse(student,course);else if(!longTerm)updateStudentEnrollment(student,course);Object.assign(student,obj);student.longTermAbsent=longTerm;student.longTermAbsentSince=longTerm?(student.longTermAbsentSince||localDate()):'';if(longTerm){student.pausedClassName=student.pausedClassName||obj.className;student.pausedTeacher=student.pausedTeacher||obj.teacher;student.className='';student.teacher=''}appendHoursAdjustment(student,previousHours,obj.hours,adjustmentReason)}else{const created={...obj,id:uid(),createdAt:new Date().toISOString(),longTermAbsent:longTerm,longTermAbsentSince:longTerm?localDate():'',enrollmentHistory:[],longTermAbsenceHistory:longTerm?[{startDate:localDate(),endDate:''}]:[]};if(longTerm){created.pausedClassName=obj.className||'';created.pausedTeacher=obj.teacher||'';created.className='';created.teacher=''}state.students.push(created);ensureEnrollmentHistory()}if(!saveState())return;closeModal();render();toast(student?'学生信息已更新':longTerm?'长期不来学生已录入':'学生录入成功');};
}

function openHoursDetail(student,initialFilter='all'){
  if(!student)return;
  const records=state.transactions.filter(record=>record.studentId===student.id).slice().sort((a,b)=>`${b.date} ${b.time}`.localeCompare(`${a.date} ${a.time}`)),increased=records.filter(record=>Number(record.amount)>0).reduce((sum,record)=>sum+Number(record.amount),0),decreased=Math.abs(records.filter(record=>Number(record.amount)<0).reduce((sum,record)=>sum+Number(record.amount),0));
  openModal(`<div class="modal-head"><span>${uiIcon('hours')}</span><div><h2>${esc(student.name)} · 课时详情</h2><p>${esc(student.studentNo)} · ${esc(student.className)}</p></div></div><div class="hours-detail-summary"><div class="current"><span>当前剩余</span><strong>${money(student.hours)}</strong><small>课时</small></div><div><span>累计增加</span><strong>+${money(increased)}</strong><small>${records.filter(record=>Number(record.amount)>0).length} 条记录</small></div><div><span>累计减少</span><strong>−${money(decreased)}</strong><small>${records.filter(record=>Number(record.amount)<0).length} 条记录</small></div></div><div class="hours-history-head"><div><b>课时变动记录</b><span>共 ${records.length} 条</span></div><div class="hours-history-filters"><button type="button" data-hours-history-filter="all">全部</button><button type="button" data-hours-history-filter="increase">只看增加</button><button type="button" data-hours-history-filter="decrease">只看减少</button></div></div><div class="hours-history-list" id="hoursHistoryList"></div><div class="form-actions hours-detail-actions"><button type="button" class="secondary-btn" id="closeHoursDetail">关闭</button><button type="button" class="primary-btn" id="addHoursFromDetail"><span>＋</span>增加课时</button></div>`);
  $('#modal').classList.add('hours-detail-modal');
  const drawHistory=filter=>{const visible=records.filter(record=>filter==='all'||(filter==='increase'?Number(record.amount)>0:Number(record.amount)<0));$$('[data-hours-history-filter]').forEach(button=>button.classList.toggle('selected',button.dataset.hoursHistoryFilter===filter));$('#hoursHistoryList').innerHTML=visible.length?visible.map(record=>`<article class="hours-history-record"><div class="transaction-sign ${Number(record.amount)>0?'increase':'decrease'}">${Number(record.amount)>0?'+':'−'}</div><div><b>${esc(record.reason||'课时变动')}</b><span>${esc(record.date)} ${esc(record.time)}</span></div><strong class="${Number(record.amount)>0?'increase':'decrease'}">${Number(record.amount)>0?'+':''}${money(record.amount)}</strong></article>`).join(''):empty('↕',filter==='increase'?'没有增加记录':filter==='decrease'?'没有减少记录':'暂无课时变动','初始录入课时不会计入变动记录')};
  $$('[data-hours-history-filter]').forEach(button=>button.onclick=()=>drawHistory(button.dataset.hoursHistoryFilter));$('#closeHoursDetail').onclick=closeModal;$('#addHoursFromDetail').onclick=()=>openHoursModal(student,()=>openHoursDetail(student));drawHistory(['all','increase','decrease'].includes(initialFilter)?initialFilter:'all');
}
function openHoursModal(student,onSaved=null){
  if(!student)return;
  openModal(`<div class="modal-head"><span>${uiIcon('hours')}</span><div><h2>增加课时 · ${esc(student.name)}</h2><p>当前剩余 ${money(student.hours)} 课时</p></div></div><form id="hoursForm" class="simple-form"><label class="field"><span>增加课时数 *</span><input name="amount" type="number" min="0.5" step="0.5" required placeholder="如：20"></label><label class="field"><span>备注</span><input name="reason" value="续费增加课时"></label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="primary-btn" type="submit">确认增加</button></div></form>`);$('#cancelModal').onclick=closeModal;$('#hoursForm').onsubmit=e=>{e.preventDefault();const f=new FormData(e.target),amount=Number(f.get('amount'));const result=recordHoursAddition(student.id,amount,f.get('reason'));if(!result.ok)return toast(result.error,true);closeModal();render();toast(`已为 ${student.name} 增加 ${money(amount)} 课时`);if(typeof onSaved==='function')setTimeout(onSaved,120)};
}

function openCheckin(method){
  if(!state.students.length){toast('请先录入学生',true);return openStudentModal()}
  if(method==='人脸')return openFaceCheckin();
  openModal(`<div class="modal-head"><span>${method==='姓名'?'名':'#'}</span><div><h2>${method}签到</h2><p>签到成功后自动扣除 1 课时</p></div></div><form id="checkinForm" class="simple-form"><label class="field"><span>请输入学生${method}</span><input name="query" required autofocus placeholder="输入后点击确认签到"></label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="primary-btn" type="submit">确认签到</button></div></form>`);$('#cancelModal').onclick=closeModal;$('#checkinForm').onsubmit=e=>{e.preventDefault();const query=String(new FormData(e.target).get('query')).trim();const matches=state.students.filter(x=>String(method==='学号'?x.studentNo:x.name).trim()===query);if(!matches.length)return toast('没有找到这位学生',true);if(matches.length>1)return openCheckinChoices(matches,method);const s=matches[0];if(isLongTermAbsent(s))return toast('该学生当前为长期不来，请先在后台恢复正常在读',true);performCheckin(s,method);};
}
function openCheckinChoices(students,method){
  openModal(`<div class="modal-head"><span>${uiIcon('students')}</span><div><h2>请选择正确的学生</h2><p>发现 ${students.length} 条匹配档案，请核对学号和班级后签到</p></div></div><div class="simple-form">${students.map(student=>`<button type="button" class="secondary-btn checkin-choice" data-checkin-student="${student.id}" ${isLongTermAbsent(student)?'disabled':''}><b>${esc(student.name)} · ${esc(student.studentNo||'未分配学号')}</b><span>${esc(isLongTermAbsent(student)?'长期不来（不能签到）':student.className||'未分班')} · ${esc(student.teacher||'未设置老师')} · 剩余 ${money(student.hours)} 课时</span></button>`).join('')}</div><div class="form-actions"><button type="button" class="secondary-btn" id="cancelCheckinChoice">返回</button></div>`);
  $('#cancelCheckinChoice').onclick=()=>openCheckin(method);
  $$('[data-checkin-student]',$('#modal')).forEach(button=>button.onclick=()=>performCheckin(studentById(button.dataset.checkinStudent),method));
}
function openMakeupCheckin(initialDate=localDate(),initialClassKey=''){
  if(!isValidLocalDate(initialDate)||initialDate>localDate())return toast('请选择今天或更早的日期进行补签',true);
  if(initialClassKey&&!makeupClassGroupsAt(initialDate).some(group=>group.key===initialClassKey))return toast('所选班级当天没有需要补签的学生',true);
  if(!state.students.length){toast('请先录入学生',true);return openStudentModal()}
  openModal(`<div class="modal-head"><span>补</span><div><h2>老师补签</h2><p>选择上课日期和班级，只显示当天尚未签到的学生</p></div></div><form id="makeupCheckinForm" class="simple-form"><div class="makeup-filter-grid"><label class="field"><span>补签日期 *</span><input id="makeupDate" name="date" type="date" max="${localDate()}" value="${localDate()}" required></label><label class="field"><span>上课班级 *</span><select id="makeupClass" required></select></label></div><div class="makeup-class-summary" id="makeupClassSummary"></div><div class="makeup-list-head"><div><b>未签到学生</b><small id="makeupStudentCount">0 人可补签</small></div><label><input id="selectAllMakeup" type="checkbox"> 全选有课时学生</label></div><div class="makeup-student-list" id="makeupStudentList"></div><label class="field"><span>补签备注（选填）</span><input name="note" maxlength="100" placeholder="如：老师核对课堂点名后补签"></label><p class="makeup-rule-note">补签将按所选日期计入周报和到课率，每名学生扣除 1 课时，并保留“老师补签”标记。</p><div class="form-actions"><button type="button" class="secondary-btn" id="cancelMakeupCheckin">取消</button><button type="submit" class="primary-btn" id="confirmMakeupCheckin" disabled>确认补签</button></div></form>`);
  $('#modal').classList.add('makeup-checkin-modal');const dateInput=$('#makeupDate'),classSelect=$('#makeupClass'),list=$('#makeupStudentList'),summary=$('#makeupClassSummary'),count=$('#makeupStudentCount'),selectAll=$('#selectAllMakeup'),confirmButton=$('#confirmMakeupCheckin');let groups=[];
  const refreshSelection=()=>{const boxes=$$('[data-makeup-student]',list),enabled=boxes.filter(box=>!box.disabled),selected=enabled.filter(box=>box.checked).length;count.textContent=`${boxes.length} 人未签到 · 已选 ${selected} 人`;confirmButton.disabled=!selected;selectAll.checked=Boolean(enabled.length)&&enabled.every(box=>box.checked);selectAll.indeterminate=selected>0&&!selectAll.checked};
  const drawStudents=()=>{const group=groups.find(item=>item.key===classSelect.value);if(!group){summary.innerHTML='<span>—</span><div><b>没有可补签的班级</b><small>该日期没有未签到的正常在读学生</small></div>';list.innerHTML=empty('✓','没有需要补签的学生','请更换日期或班级');selectAll.disabled=true;refreshSelection();return}summary.innerHTML=`<span>${uiIcon('classes')}</span><div><b>${esc(group.className)}</b><small>${esc(group.teacher||'未安排老师')} · ${esc(group.classroom||'未设置教室')}</small></div><em>${group.members.length} 人未签到</em>`;list.innerHTML=group.members.map(item=>{const student=item.student,disabled=!Number.isFinite(Number(student.hours));return `<label class="makeup-student-option ${disabled?'disabled':''}"><input type="checkbox" data-makeup-student value="${student.id}" ${disabled?'disabled':''}>${avatarMarkup(student)}<span><b>${esc(student.name)}</b><small>${esc(student.studentNo||'系统学号')} · 剩余 ${money(student.hours)} 课时</small></span><em>${disabled?'课时无效':Number(student.hours)<=0?'可欠课补签':'未签到'}</em></label>`}).join('')||empty('✓','该班学生均已签到','请选择其他班级');selectAll.disabled=!group.members.some(item=>Number.isFinite(Number(item.student.hours)));$$('[data-makeup-student]',list).forEach(box=>box.onchange=refreshSelection);refreshSelection()};
  const drawClasses=()=>{const previous=classSelect.value;groups=makeupClassGroupsAt(dateInput.value);classSelect.innerHTML=groups.length?groups.map(group=>`<option value="${esc(group.key)}" ${group.key===previous?'selected':''}>${esc(group.className)} · ${group.members.length} 人未签到</option>`).join(''):'<option value="">没有可补签的班级</option>';classSelect.disabled=!groups.length;drawStudents()};
  dateInput.onchange=drawClasses;classSelect.onchange=drawStudents;selectAll.onchange=()=>{$$('[data-makeup-student]',list).forEach(box=>{if(!box.disabled)box.checked=selectAll.checked});refreshSelection()};$('#cancelMakeupCheckin').onclick=closeModal;
  $('#makeupCheckinForm').onsubmit=event=>{event.preventDefault();const ids=$$('[data-makeup-student]:checked',list).map(box=>Number(box.value)),data=new FormData(event.target),result=recordMakeupAttendance(ids,String(data.get('date')||''),String(data.get('note')||''));if(!result.ok)return toast(result.error,true);closeModal();currentView='checkin';render();toast(`已为 ${result.count} 名学生完成老师补签`)};
  dateInput.value=initialDate;drawClasses();
  if(initialClassKey){classSelect.value=initialClassKey;drawStudents()}
}
function openFaceCheckin(){
  if(!state.settings.cameraPositionConfigured)return openCameraPositionSetup(true,openFaceCheckin);
  const eligible=reportableStudents().filter(canPrepareSFaceProfile);
  if(!eligible.length){toast('没有可用于识别的人脸，请先编辑学生并拍照或采集人脸',true);return go('students')}
  openModal(`<div class="modal-head face-checkin-head"><span>⌾</span><div><h2 id="faceDialogTitle">高精度人脸签到</h2><p id="faceDialogSubtitle">SFace 五官校正 · 多帧安全核对</p></div></div><div class="face-checkin-shell" id="faceCheckinShell"><div class="face-scan-stage" id="faceScanStage"><div class="camera-frame recognition-camera"><video id="camera" autoplay playsinline muted></video><div class="face-guide"></div><div class="scan-line"></div><div class="recognition-badge" id="recognitionBadge">准备识别</div></div><aside class="face-scan-aside"><div class="scan-state-icon"><span>⌾</span><i></i></div><b>识别状态</b><p class="camera-note" id="cameraStatus">正在加载 SFace 高精度离线模型…</p><div class="scan-tips"><span>单人入镜</span><span>正对镜头</span><span>保持光线充足</span></div></aside></div><div class="recognition-result hidden" id="recognitionResult"></div><div class="form-actions face-checkin-footer"><button type="button" class="secondary-btn" id="cancelModal">取消识别</button></div></div>`);document.body.classList.add('face-dialog-open');$('#modal').classList.add('face-checkin-modal');applyFacePopupPosition();playCameraPopupAnimation();$('#cancelModal').onclick=closeModal;
  startAutomaticRecognition(eligible);
}

function buildFaceRecognitionIndex(students){return students.map(student=>({student,samples:studentFaceSamples(student)})).filter(entry=>entry.samples.length)}
function findBestFaceMatch(descriptor,recognitionIndex){
  const ranked=recognitionIndex.map(entry=>{const student=entry.student,samples=entry.samples,similarities=samples.map(saved=>faceCosine(descriptor,saved)).sort((a,b)=>b-a),support=similarities.slice(0,Math.min(3,similarities.length)),average=support.reduce((sum,value)=>sum+value,0)/support.length,score=similarities[0]*.65+average*.35;return{student,score,bestSimilarity:similarities[0],supportFloor:support[support.length-1],sampleCount:samples.length,supportCount:support.length}}).filter(match=>Number.isFinite(match.score)).sort((a,b)=>b.score-a.score);
  const best=ranked[0]||{student:null,score:-1},second=ranked[1]||{score:-1};
  return {...best,margin:best.score-second.score};
}
async function startAutomaticRecognition(eligible){
  const token=++faceScanToken,status=$('#cameraStatus'),badge=$('#recognitionBadge'),video=$('#camera');
  let lastStudentId=null,streak=0,streakScores=[],streakDescriptors=[],streakMargins=[];
  try{
    await loadFaceModels();
    if(token!==faceScanToken)return;
    eligible=await prepareSFaceProfiles(eligible,status);
    if(token!==faceScanToken)return;
    if(!eligible.length){badge.textContent='没有可用人脸';status.textContent='旧人脸资料无法自动升级，请在学生资料中重新拍照录入';return}
    const recognitionIndex=buildFaceRecognitionIndex(eligible);if(!recognitionIndex.length){badge.textContent='没有可用人脸';status.textContent='请在学生资料中重新录入人脸';return}
    status.textContent=`SFace 已载入 ${recognitionIndex.length} 名学生，请正对摄像头`;badge.textContent='正在寻找人脸';
    activeStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});video.srcObject=activeStream;await video.play();
    while(token===faceScanToken){
      if(video.readyState<2){await delay(250);continue}
      const result=await readFaceDescriptor(video);
      if(token!==faceScanToken)return;
      if(!result){lastStudentId=null;streak=0;streakScores=[];streakDescriptors=[];streakMargins=[];badge.textContent='只允许一人入镜';status.textContent='请确保画面中只有一张清晰人脸';await delay(80);continue}
      if(!clearFaceFrame(result,video)){lastStudentId=null;streak=0;streakScores=[];streakDescriptors=[];streakMargins=[];badge.textContent='人脸不够清晰';status.textContent='请靠近摄像头，正对镜头并保持光线充足';await delay(80);continue}
      const best=findBestFaceMatch(result.descriptor,recognitionIndex);
      const singleProfile=best.sampleCount<3,safeMatch=singleProfile?best.score>=.52&&best.bestSimilarity>=.52&&best.margin>=.1:best.score>=.48&&best.bestSimilarity>=.5&&best.supportFloor>=.4&&best.margin>=.085,requiredStreak=singleProfile?4:3;
      if(best.student&&safeMatch){
        if(lastStudentId===best.student.id){streak++;streakScores.push(best.score);streakDescriptors.push(Array.from(result.descriptor));streakMargins.push(best.margin)}else{lastStudentId=best.student.id;streak=1;streakScores=[best.score];streakDescriptors=[Array.from(result.descriptor)];streakMargins=[best.margin]}
        badge.textContent=`快速核对 ${streak}/${requiredStreak}`;status.textContent='正在进行陌生人拒识检查，请保持不动';
        const runningAverage=streakScores.reduce((sum,value)=>sum+value,0)/streakScores.length;
        if(streak>=requiredStreak&&runningAverage>=(singleProfile ? .52 : .48)){faceScanToken++;showFaceConfirmation(best.student,runningAverage,streakDescriptors,{score:runningAverage,margin:Math.min(...streakMargins)});return}
      }else{lastStudentId=null;streak=0;streakScores=[];streakDescriptors=[];streakMargins=[];badge.textContent='陌生人或无法确认';status.textContent='当前人脸未通过严格安全阈值，请重新录入本人资料或改用姓名、学号签到'}
      await delay(60);
    }
  }catch(error){if(token===faceScanToken){const message=error&&error.message||'离线人脸识别启动失败';badge.textContent='识别启动失败';status.textContent=message;toast(message,true)}}
}
function showFaceConfirmation(student,score,candidateDescriptors=[],confidence={}){
  if(activeStream){activeStream.getTracks().forEach(t=>t.stop());activeStream=null}
  const result=$('#recognitionResult'),stage=$('#faceScanStage'),already=state.attendance.some(a=>a.studentId===student.id&&a.date===localDate())||studentHasLeaveOnDate(student.id,localDate()),learningCount=Number(student.faceLearningCount||0);
  stage.classList.add('hidden');$('#faceDialogTitle').textContent='请核对学生身份';$('#faceDialogSubtitle').textContent='人脸识别已完成，摄像头已自动关闭';$('#cancelModal').textContent='关闭';
  result.innerHTML=`<div class="recognition-success-mark">✓</div><div class="recognized-person">${avatarMarkup(student)}<div><span>自动识别为</span><h3>${esc(student.name)}</h3><p>${esc(student.studentNo)} · ${esc(student.className)}</p></div><strong>${money(student.hours)}<small>剩余课时</small></strong></div><div class="face-learning-note"><span>✦</span><div><b>SFace 自适应学习已开启</b><small>只有确认本人并成功签到后才会学习新角度和光线 · 已学习 ${learningCount} 次</small></div></div><div class="recognition-match-note">已通过多帧陌生人拒识检查，请确认学生信息</div>${already?'<p class="recognition-warning">该学生今天已签到或已请假，不能直接签到；请假到课须先由老师撤销请假。</p>':''}<div class="recognition-actions"><button class="secondary-btn" id="recognizeAgain">不是本人，重新识别</button><button class="primary-btn" id="confirmRecognized" ${already?'disabled':''}>确认是本人并签到</button></div>`;
  result.classList.remove('hidden');
  $('#recognizeAgain').onclick=()=>{result.classList.add('hidden');stage.classList.remove('hidden');$('#faceDialogTitle').textContent='高精度人脸签到';$('#faceDialogSubtitle').textContent='SFace 五官校正 · 多帧安全核对';$('#cancelModal').textContent='取消识别';startAutomaticRecognition(reportableStudents().filter(hasSFaceProfile))};
  $('#confirmRecognized').onclick=()=>{const learned=learnFromConfirmedFace(student,candidateDescriptors,confidence);performCheckin(student,'人脸',learned?`，人脸档案已完成第 ${Number(student.faceLearningCount||0)} 次学习`:'')};
}
function recordStudentAttendance(studentId,method){
  if(typeof canonicalModeActive==='function'&&canonicalModeActive())return canonicalRecordStudentAttendance(studentId);
  const student=studentById(studentId),date=localDate();
  if(!student)return{ok:false,error:'学生档案已变化，请重新选择学生'};
  if(isLongTermAbsent(student))return{ok:false,error:'该学生当前为长期不来，请先在后台恢复正常在读'};
  if(studentHasLeaveOnDate(student.id,date))return{ok:false,error:'该学生当天已请假，请老师先撤销请假后再签到'};const hours=Number(student.hours);if(!Number.isFinite(hours))return{ok:false,error:'该学生课时无效，请先核对资料'};
  if(state.attendance.some(record=>record.studentId===student.id&&record.date===date))return{ok:false,error:'该学生今天已经签到'};
  const time=new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),attendanceId=uid(),snapshot=attendanceSnapshotForStudent(student,date);
  student.hours=hours-1;state.attendance.push({id:attendanceId,studentId:student.id,date,time,method,...snapshot});
  state.transactions.push({id:uid(),attendanceId,studentId:student.id,amount:-1,reason:`${method}签到扣减`,date,time,...snapshot});
  prepareWechatAttendance(state.attendance[state.attendance.length-1],student);
  if(!saveState())return{ok:false,error:'签到保存失败，课时未扣减，请重试'};
  listPages.attendance=1;return{ok:true,attendanceId,student};
}
function recordHoursAddition(studentId,amount,reason='续费增加课时'){
  const student=studentById(studentId),value=Number(amount);
  if(!student)return{ok:false,error:'没有找到学生档案'};
  if(!Number.isFinite(value)||value<=0||!Number.isFinite(Number(student.hours))||!Number.isFinite(Number(student.hours)+value))return{ok:false,error:'请填写有效的增加课时数'};
  student.hours=Number(student.hours)+value;
  state.transactions.push({id:uid(),studentId:student.id,amount:value,reason:String(reason||'增加课时').trim().slice(0,200),date:localDate(),time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}),...attendanceSnapshotForStudent(student)});
  return saveState()?{ok:true}:{ok:false,error:'课时保存失败，本次增加已撤回'};
}
function undoAttendanceRecord(attendanceId){
  const record=state.attendance.find(item=>item.id===Number(attendanceId)),student=record&&studentById(record.studentId);
  if(!record||!student)return{ok:false,error:'这条签到已撤销或学生档案已变化'};
  if(!Number.isFinite(Number(student.hours)))return{ok:false,error:'学生课时无效，请先核对资料'};
  let linked=state.transactions.findIndex(item=>item.attendanceId===record.id&&item.studentId===student.id&&Number(item.amount)===-1);
  if(linked<0){for(let index=state.transactions.length-1;index>=0;index--){const item=state.transactions[index];if(!item.attendanceId&&item.studentId===student.id&&item.date===record.date&&item.time===record.time&&Number(item.amount)===-1){linked=index;break}}}
  cancelWechatAttendance(record.id);
  state.attendance=state.attendance.filter(item=>item.id!==record.id);student.hours=Number(student.hours)+1;if(linked>=0)state.transactions.splice(linked,1);
  return saveState()?{ok:true}:{ok:false,error:'撤销保存失败，原签到和课时已保留'};
}
function performCheckin(student,method,successSuffix=''){
  const result=recordStudentAttendance(student&&student.id,method);if(!result.ok)return toast(result.error,true);
  closeModal();currentView='checkin';render();showCheckinSuccess(result.student,method,successSuffix);
}
function classroomForStudent(student){const course=state.classes.find(item=>normalizedGroupLabel(item.name)===normalizedGroupLabel(student&&student.className));return String(course&&course.classroom||'暂未设置教室').trim()||'暂未设置教室'}

function showCheckinSuccess(student,method,successSuffix=''){
  const classroom=classroomForStudent(student),course=state.classes.find(item=>normalizedGroupLabel(item.name)===normalizedGroupLabel(student&&student.className)),teacherName=studentFacingTeacherName(course&&course.teacher||student.teacher);
  openModal(`<div class="checkin-success-content"><div class="checkin-success-mark"><span>${uiIcon('check')}</span><i></i></div><div class="checkin-success-title"><small>${esc(method)}签到</small><h2>签到成功</h2><p>${esc(student.name)}，请前往以下教室上课</p></div><div class="checkin-success-student">${avatarMarkup(student)}<div><span>学生姓名</span><b>${esc(student.name)}</b><small>${esc(student.studentNo)}</small></div><div><span>所在班级</span><b>${esc(student.className||'未设置班级')}</b><small>${esc(teacherName)}</small></div></div><div class="checkin-classroom-card"><span>上课教室</span><strong>${esc(classroom)}</strong><small>${classroom==='暂未设置教室'?'请联系老师确认上课地点':'请按时前往教室上课'}</small></div><div class="checkin-success-hours"><span>签到已扣除 1 课时</span><strong>${money(student.hours)}<small>剩余课时</small></strong></div>${Number(student.hours)<0?`<p class="checkin-debt-note">已欠 ${money(Math.abs(Number(student.hours)))} 课时，本次签到已记录；续费后自动抵扣欠课。</p>`:''}${successSuffix?`<p class="checkin-success-note">${esc(successSuffix.replace(/^，/,''))}</p>`:''}<button type="button" class="primary-btn checkin-success-done" id="finishCheckinSuccess">完成</button><small class="checkin-success-auto">4 秒后自动关闭</small></div>`);
  $('#modal').classList.add('checkin-success-modal');$('#finishCheckinSuccess').onclick=closeModal;clearTimeout(checkinSuccessTimer);checkinSuccessTimer=setTimeout(()=>{if($('#modal').classList.contains('checkin-success-modal'))closeModal()},4000);
}

function undoCheckin(attendanceId){
  const record=state.attendance.find(a=>a.id===attendanceId),student=record&&studentById(record.studentId);if(!record||!student)return;
  openModal(`<div class="modal-head"><span>↶</span><div><h2>撤销签到确认</h2><p>请核对签到记录，撤销后将恢复相应课时</p></div></div><div class="undo-confirm-card">${avatarMarkup(student)}<div><span>学生姓名</span><b>${esc(student.name)}</b><small>${esc(record.className||student.className)} · ${esc(record.classroom||classroomForStudent(student))}</small></div><div><span>签到记录</span><b>${esc(record.time)} · ${esc(attendanceMethodLabel(record.method))}</b><small>${esc(record.date)}</small></div></div><div class="undo-confirm-impact"><span>＋1</span><div><b>恢复 1 课时</b><small>确认撤销后，这条签到和对应的课时扣减记录将同时删除。</small></div></div><div class="form-actions undo-confirm-actions"><button type="button" class="secondary-btn" id="cancelUndoCheckin">保留签到</button><button type="button" class="danger-btn" id="confirmUndoCheckin">确认撤销</button></div>`);
  $('#modal').classList.add('undo-confirm-modal');$('#cancelUndoCheckin').onclick=closeModal;$('#confirmUndoCheckin').onclick=()=>{const result=undoAttendanceRecord(attendanceId);if(!result.ok)return toast(result.error,true);closeModal();render();toast(`${student.name} 的签到已撤销，已恢复 1 课时`)};

}

function toggleLongTermAbsent(id){
  const student=studentById(id);if(!student)return;const marking=!isLongTermAbsent(student);
  if(!marking&&!state.classes.length){toast('恢复正常上课前，请先创建一个班级',true);return go('classes')}
  const preferred=student.pausedClassName||'',classOptions=state.classes.map(course=>`<option value="${course.id}" ${course.name===preferred?'selected':''}>${esc(course.name)} · ${esc(course.teacher||'未安排老师')}</option>`).join('');
  openModal(`<div class="modal-head"><span>${marking?'◷':'✓'}</span><div><h2>${marking?'设为长期不来':'恢复正常在读'}</h2><p>请核对学生状态，本操作不会删除学生资料</p></div></div><div class="student-status-confirm">${avatarMarkup(student)}<div><span>学生姓名</span><b>${esc(student.name)}</b><small>${marking?`${esc(student.className||'未分班')} · `:''}${esc(student.studentNo)}</small></div><em class="${marking?'pause':'restore'}">${marking?'长期不来':'恢复上课'}</em></div><div class="student-status-impact ${marking?'pause':'restore'}"><span>${marking?'—':'＋'}</span><div><b>${marking?'移出当前班级并停止统计':'选择班级后重新计入统计'}</b><small>${marking?'学生会独立放入长期不来名单，不再属于任何班级；历史签到、课时和原班级记录仍会保留。':'恢复后可正常签到，并重新计入所选班级、任课老师和全校的到课率。'}</small></div></div>${marking?'':`<section class="restore-class-panel"><label class="field"><span>恢复到班级 *</span><select id="restoreStudentClass"><option value="" disabled ${state.classes.some(course=>course.name===preferred)?'':'selected'}>请选择班级</option>${classOptions}</select></label><div><span>授课老师</span><b id="restoreStudentTeacher">—</b><small>老师根据所选班级自动填写</small></div></section>`}<div class="form-actions student-status-actions"><button type="button" class="secondary-btn" id="cancelStudentStatus">取消</button><button type="button" class="primary-btn ${marking?'pause-btn':''}" id="confirmStudentStatus">${marking?'确认设为长期不来':'确认恢复正常在读'}</button></div>`);
  $('#modal').classList.add('student-status-modal');$('#cancelStudentStatus').onclick=closeModal;
  const refreshRestoreTeacher=()=>{const select=$('#restoreStudentClass'),teacher=$('#restoreStudentTeacher');if(!select||!teacher)return;const course=classById(select.value);teacher.textContent=course&&course.teacher||'暂未安排老师'};if(!marking){$('#restoreStudentClass').onchange=refreshRestoreTeacher;refreshRestoreTeacher()}
  $('#confirmStudentStatus').onclick=()=>{if(marking)markStudentLongTerm(student);else{const course=classById($('#restoreStudentClass').value);if(!course)return toast('请选择学生恢复后所在的班级',true);restoreStudentToCourse(student,course)}if(!saveState())return false;closeModal();render();toast(marking?`${student.name} 已移入长期不来名单`:`${student.name} 已恢复到 ${student.className} 正常上课`)};
}

function deleteStudent(id){
  const s=studentById(id);if(!s)return;
  const attendanceCount=state.attendance.filter(record=>record.studentId===id).length,transactionCount=state.transactions.filter(record=>record.studentId===id).length;
  openDangerConfirmation({icon:'♙',title:'删除学生档案',description:'删除前请核对学生与关联记录',subject:`${s.name} · ${s.studentNo||'无学号'}`,subjectMeta:`${s.className||'未分班'} · 剩余 ${money(s.hours)} 课时`,impactTitle:'学生及关联记录会一并删除',impactText:`将删除 ${attendanceCount} 条签到记录、${transactionCount} 条课时变动记录和已录入的人脸信息；系统会先自动建立恢复点。`,confirmLabel:'确认删除学生',onConfirm:()=>{if(!requireSafetyBackup('删除学生前备份'))return false;removeWechatStudent(id);state.settings.studentReschedules=studentReschedules().filter(change=>change.studentId!==id);state.settings.studentLeaves=studentLeaves().filter(item=>item.studentId!==id);state.students=state.students.filter(x=>x.id!==id);state.attendance=state.attendance.filter(x=>x.studentId!==id);state.transactions=state.transactions.filter(x=>x.studentId!==id);if(!saveState())return false;closeModal();render();toast('学生档案已删除，删除前数据已自动备份')}});
}
function saveSettings(){const org=$('#orgNameInput').value.trim(),campusRaw=$('#campusCodeInput').value.trim(),target=Number($('#weeklyTargetInput').value);if(!org||!Number.isInteger(target)||target<1||target>14)return toast('请填写有效的机构名称和默认应到次数',true);if(!/^\d{1,2}$/.test(campusRaw))return toast('校区编号请填写 1 至 2 位数字',true);state.settings={...state.settings,orgName:org,campusCode:normalizeCampusCode(campusRaw),weeklyTarget:target};if(!saveState())return false;render();toast('设置与校区编号已保存');}

function workbookSheet(headers,rows,widths=[]){const sheet=XLSX.utils.aoa_to_sheet([headers,...rows]);sheet['!cols']=(widths.length?widths:headers.map(()=>14)).map(w=>({wch:w}));return sheet}
function saveWorkbook(workbook,fileName){
  if(typeof XLSX==='undefined')return toast('Excel 组件加载失败，请重新打开应用',true);
  try{const base64=XLSX.write(workbook,{bookType:'xlsx',type:'base64',compression:true});if(window.AndroidBridge&&AndroidBridge.saveBase64File){AndroidBridge.saveBase64File(fileName,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',base64);toast('Excel 正在保存到手机“下载”目录')}else XLSX.writeFile(workbook,fileName)}catch(_){toast('Excel 文件生成失败，请重试',true)}
}
function exportExcel(){
  const workbook=XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook,workbookSheet(['学生姓名','学生状态','年龄','性别','学号','学生联系方式','学校','年级','班级','授课老师','剩余课时'],state.students.map(s=>[s.name,isLongTermAbsent(s)?'长期不来':'正常在读',s.age,s.gender,s.studentNo,s.contact||'',s.school,s.grade,s.className,s.teacher,Number(s.hours||0)]),[14,12,8,8,14,18,20,12,22,12,12]),'学生信息');
  XLSX.utils.book_append_sheet(workbook,workbookSheet(['班级名称','授课老师','上课安排','上课教室','未签到提醒','学生人数','所属课程（展示）','班内显示名'],state.classes.map(c=>[c.name,c.teacher,classScheduleLabel(c),c.classroom||'',c.reminderEnabled?'开课5分钟后提醒':'未开启',state.students.filter(s=>s.className===c.name).length,classCourseName(c)||'待分类',classWithinCourseName(c)]),[24,14,24,18,20,12,22,22]),'班级信息');
  XLSX.utils.book_append_sheet(workbook,workbookSheet(['日期','时间','学生姓名','学号','班级','授课老师','上课教室','签到方式'],state.attendance.map(a=>{const s=studentById(a.studentId)||{};return[a.date,a.time,s.name||'已删除学生',s.studentNo||'',a.className||s.className||'',a.teacher||s.teacher||'',a.classroom||'',a.method]}),[14,10,14,14,22,14,18,12]),'签到记录');
  XLSX.utils.book_append_sheet(workbook,workbookSheet(['日期','时间','学生姓名','学号','课时变化','原因'],state.transactions.map(t=>{const s=studentById(t.studentId)||{};return[t.date,t.time,s.name||'已删除学生',s.studentNo||'',Number(t.amount||0),t.reason]}),[14,10,14,14,12,24]),'课时记录');
  const safeName=String(state.settings.orgName||'未来学校学生管理系统').replace(/[\\/:*?"<>|]/g,'_');saveWorkbook(workbook,`${safeName}_教务数据_${localDate()}.xlsx`);
}
function exportStudentTemplate(){
  const workbook=XLSX.utils.book_new(),headers=['学生姓名','长期不来','年龄','性别','学生联系方式','学校','年级','班级','授课老师','剩余课时'];
  XLSX.utils.book_append_sheet(workbook,workbookSheet(headers,[['张小明','否',10,'男','13800000000','示例小学','四年级','少儿编程启蒙班','王老师',20],['李小华','是','','','','','','','',10]],[14,12,8,8,18,20,12,22,12,12]),'学生导入模板');
  XLSX.utils.book_append_sheet(workbook,workbookSheet(['填写说明','内容'],[['所有学生必填','学生姓名、剩余课时'],['正常在读学生必填','班级、授课老师'],['长期不来学生','“长期不来”填写“是”时，班级和授课老师可留空；导入后独立放入长期不来名单'],['可选列','年龄、性别、学生联系方式、学校、年级，均可留空'],['长期不来标记','填写“是”会直接录入为长期不来学生；填写“否”或留空为正常在读'],['状态列兼容','也支持使用“学生状态”列，并填写“长期不来”或“正常在读”'],['学号生成','无需填写学号，导入时会根据入学年份、校区编号和四位学生编号自动连续生成'],['旧表兼容','旧模板中的学号列可以保留；已填写且不重复的学号会继续使用，空白学号自动生成'],['学生照片','照片文件请使用“学生姓名.jpg”命名，导入时可一次选择多张'],['照片要求','正面、单人、光线充足，建议 JPG 或 PNG'],['性别格式','选填；填写时使用“男”或“女”'],['年龄格式','选填；填写时应为 3–25'],['课时格式','必填；填写数字，允许负数；负数表示欠课']], [18,76]),'填写说明');
  saveWorkbook(workbook,'未来学校学生管理系统_学生批量导入模板.xlsx');
}
function readLocalFile(file){return file.arrayBuffer?file.arrayBuffer():new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsArrayBuffer(file)})}
function readLocalTextFile(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result||''));reader.onerror=reject;reader.readAsText(file,'utf-8')})}
function matchImportPhoto(student,photoMap,nameCounts){
  const byNumber=photoMap.get(String(student.studentNo||'').trim().toLowerCase());if(byNumber)return byNumber;
  const name=String(student.name||'').trim().toLowerCase();return nameCounts.get(name)===1?photoMap.get(name):null;
}
function openBatchImport(){
  let readyRows=[],selectedPhotos=[],autoGeneratedCount=0,longTermCount=0,importing=false;
  openModal(`<div class="modal-head"><span>⇧</span><div><h2>批量导入学生</h2><p>无需填写学号，系统会按当前校区自动连续生成</p></div></div><div class="simple-form"><label class="excel-drop"><span>▤</span><b>选择 Excel 文件</b><small>支持 .xlsx、.xls 和 .csv</small><input id="batchFile" type="file" accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"></label><div id="importPreview" class="import-preview">尚未选择文件</div><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button type="button" class="primary-btn" id="confirmBatchImport" disabled>确认批量导入</button></div></div>`);
  $('#cancelModal').onclick=closeModal;
  const photoPicker=document.createElement('label');photoPicker.className='excel-drop compact';photoPicker.innerHTML='<span>▧</span><b>选择学生照片（可多选）</b><small id="photoPickState">照片可用学号或姓名命名，同名学生必须使用学号</small><input id="batchPhotos" type="file" multiple accept="image/jpeg,image/png,image/webp">';$('#importPreview').before(photoPicker);$('#batchPhotos').onchange=event=>{selectedPhotos=[...(event.target.files||[])];$('#photoPickState').textContent=selectedPhotos.length?`已选择 ${selectedPhotos.length} 张照片，将优先按学号、其次按唯一姓名匹配`:'照片请用学生姓名命名，例如 张小明.jpg'};
  $('#batchFile').onchange=async event=>{
    const file=event.target.files&&event.target.files[0],preview=$('#importPreview'),confirmButton=$('#confirmBatchImport');
    readyRows=[];autoGeneratedCount=0;longTermCount=0;confirmButton.disabled=true;if(!file)return;preview.innerHTML='<b>正在读取并检查文件…</b>';
    try{
      const workbook=XLSX.read(await readLocalFile(file),{type:'array'}),sheet=workbook.Sheets[workbook.SheetNames[0]],matrix=XLSX.utils.sheet_to_json(sheet,{header:1,defval:'',raw:false});
      if(matrix.length<2)throw new Error('表格中没有学生数据');
      const headers=matrix[0].map(value=>String(value).replace(/\s/g,'')),aliases={name:['学生姓名','姓名','name'],longTermAbsent:['长期不来','学生状态','状态','longTermAbsent'],age:['年龄','age'],gender:['性别','gender'],studentNo:['学号','学生学号','studentNo'],contact:['学生联系方式','联系方式','联系电话','手机号','contact'],school:['学校','所在学校','school'],grade:['年级','grade'],className:['班级','所在班级','className'],teacher:['授课老师','老师','teacher'],hours:['剩余课时','课时','hours']},indexes={};
      Object.entries(aliases).forEach(([key,names])=>indexes[key]=headers.findIndex(header=>names.some(name=>header.toLowerCase()===name.toLowerCase())));
      const missing=Object.entries(indexes).filter(([key,index])=>['name','hours'].includes(key)&&index<0).map(([key])=>({name:'学生姓名',hours:'剩余课时'}[key]));
      if(missing.length)throw new Error(`缺少表头：${missing.join('、')}`);
      const used=new Set(state.students.map(student=>String(student.studentNo||'').trim()).filter(Boolean)),issues=[];
      matrix.slice(1).forEach((cells,rowIndex)=>{
        if(cells.every(value=>String(value).trim()===''))return;
        const row={};Object.entries(indexes).forEach(([key,index])=>row[key]=index>=0?String(cells[index]===null||cells[index]===undefined?'':cells[index]).trim():'');const longTermStatus=parseImportedLongTermStatus(row.longTermAbsent);row.gender=row.gender.startsWith('男')?'男':row.gender.startsWith('女')?'女':row.gender;row.age=row.age===''?'':Number(row.age);row.hours=row.hours===''?NaN:Number(row.hours);
        const emptyFields=[['姓名',row.name],...(!longTermStatus.value?[["班级",row.className],["授课老师",row.teacher]]:[])].filter(([,value])=>!value).map(([name])=>name);
        let reason='';
        if(!longTermStatus.valid)reason='长期不来列请填写“是/否”，或学生状态填写“长期不来/正常在读”';
        else if(emptyFields.length)reason=`缺少${emptyFields.join('、')}`;
        else if(row.age!==''&&(!Number.isFinite(row.age)||row.age<3||row.age>25))reason='年龄填写时应为 3–25';
        else if(row.gender&&!['男','女'].includes(row.gender))reason='性别填写时应为男或女';
        else if(!Number.isFinite(row.hours))reason='剩余课时应为数字，可用负数表示欠课';
        else if(row.studentNo&&used.has(row.studentNo))reason='学号重复';
        else if(!row.studentNo){row.studentNo=nextImportStudentNo(used);if(!row.studentNo)reason='本年度该校区的学生编号已用满';else autoGeneratedCount++}
        if(reason)issues.push(`第 ${rowIndex+2} 行：${reason}`);else{row.longTermAbsent=longTermStatus.value;row.longTermAbsentSince=longTermStatus.value?localDate():'';if(longTermStatus.value){row.pausedClassName=row.className||'';row.pausedTeacher=row.teacher||'';row.className='';row.teacher='';longTermCount++}used.add(row.studentNo);readyRows.push(row)}
      });
      const autoNote=autoGeneratedCount?`<p class="import-ok">将自动生成 ${autoGeneratedCount} 个学号，校区编号为 ${esc(normalizeCampusCode(state.settings.campusCode))}</p>`:'';
      const longTermNote=longTermCount?`<p class="import-status-note">已标记 ${longTermCount} 名长期不来学生，将独立归入长期不来名单，不进入任何班级</p>`:'';
      preview.innerHTML=`<div class="import-summary"><strong>${readyRows.length}</strong><span>可导入</span><strong class="${issues.length?'bad':''}">${issues.length}</strong><span>跳过</span></div>${autoNote}${longTermNote}${issues.length?`<div class="import-issues">${issues.slice(0,8).map(item=>`<p>${esc(item)}</p>`).join('')}${issues.length>8?`<p>另有 ${issues.length-8} 条未显示</p>`:''}</div>`:'<p class="import-ok">检查通过，可以批量导入</p>'}`;
      confirmButton.disabled=!readyRows.length;
    }catch(error){preview.innerHTML=`<p class="import-error">${esc(error.message||'无法读取该文件，请使用模板重新填写')}</p>`}
  };
  $('#confirmBatchImport').onclick=async()=>{
    if(!readyRows.length||importing)return;
    importing=true;const button=$('#confirmBatchImport'),preview=$('#importPreview'),photoMap=new Map(),nameCounts=new Map(),rows=readyRows.map(row=>({...row})),importToken=faceScanToken,imported=[];
    rows.forEach(row=>{const key=row.name.trim().toLowerCase();nameCounts.set(key,(nameCounts.get(key)||0)+1)});
    selectedPhotos.forEach(file=>{const key=file.name.replace(/\.[^.]+$/,'').trim().toLowerCase();photoMap.set(key,photoMap.has(key)?null:file)});
    button.disabled=true;$('#batchFile').disabled=true;$('#batchPhotos').disabled=true;
    let photoCount=0,photoFailed=0,applying=false;
    try{
      for(let index=0;index<rows.length;index++){
        if(importToken!==faceScanToken)return;
        const row=rows[index],course=state.classes.find(c=>c.name===row.className),photo=matchImportPhoto(row,photoMap,nameCounts);
        if(course)row.teacher=course.teacher||row.teacher;
        let profile={faceImage:'',faceDescriptors:[],sfaceDescriptors:[],sfaceAdaptiveDescriptors:[],sfaceModelVersion:0,faceSecurityVersion:0,faceProfileSource:'',faceRegistered:false};
        if(photo){preview.textContent=`正在用 SFace 处理照片 ${index+1}/${rows.length}`;try{profile=await faceProfileFromPhoto(photo);photoCount++}catch(_){photoFailed++}}
        else if(photoMap.has(row.studentNo.trim().toLowerCase())||photoMap.has(row.name.trim().toLowerCase()))photoFailed++;
        imported.push({...row,...profile,id:uid(),createdAt:new Date().toISOString()});
      }
      if(importToken!==faceScanToken)return;
      applying=true;imported.forEach(student=>state.students.push(student));ensureClasses();ensureEnrollmentHistory();normalizeLongTermStudentAssignments();
      if(!saveState())return;
      closeModal();currentView='students';studentClassFilter='all';studentStatusFilter='all';studentSearchQuery='';listPages.students=1;render();
      toast(`导入 ${rows.length} 名学生，自动生成 ${autoGeneratedCount} 个学号${longTermCount?`，其中 ${longTermCount} 名长期不来`:''}${photoCount?`，匹配 ${photoCount} 张高精度人脸照片`:''}${photoFailed?`，${photoFailed} 张照片未导入（不合格或同名无法确认）`:''}`);
    }catch(error){if(applying)rollbackUnsavedState();toast(error&&error.message||'导入失败，请检查文件后重试',true)}
    finally{importing=false;if(importToken===faceScanToken){button.disabled=false;const fileInput=$('#batchFile'),photoInput=$('#batchPhotos');if(fileInput)fileInput.disabled=false;if(photoInput)photoInput.disabled=false}}
  };

}
function openExport(){const text=JSON.stringify(state,null,2);openModal(`<div class="modal-head"><span>⇩</span><div><h2>导出本机备份</h2><p>长按全选复制，并保存到安全的位置</p></div></div><div class="simple-form"><label class="field"><span>备份文本</span><textarea id="backupText" readonly style="height:230px;padding:10px;border:1px solid #dfe4e1;border-radius:9px;font-size:8px;resize:none">${esc(text)}</textarea></label><div class="form-actions"><button class="secondary-btn" id="copyBackup">复制文本</button><button class="primary-btn" id="doneBackup">完成</button></div></div>`);$('#doneBackup').onclick=closeModal;$('#copyBackup').onclick=async()=>{const ta=$('#backupText');ta.select();try{await navigator.clipboard.writeText(ta.value)}catch(_){document.execCommand('copy')}toast('备份文本已复制');};}
function openImport(){openModal(`<div class="modal-head"><span>⇧</span><div><h2>恢复完整备份</h2><p>可以选择自动备份文件，也可以粘贴此前导出的备份文本</p></div></div><form id="importForm" class="simple-form"><label class="excel-drop compact"><span>▤</span><b>选择 JSON 备份文件</b><small id="backupFileState">支持每日自动备份和手动导出的完整备份</small><input id="backupJsonFile" type="file" accept=".json,application/json"></label><label class="field"><span>备份文本</span><textarea id="backupImportText" name="data" required style="height:190px;padding:10px;border:1px solid #dfe4e1;border-radius:9px;font-size:8px;resize:none" placeholder="选择文件后会自动填入，也可以在这里粘贴 JSON"></textarea></label><div class="form-actions"><button type="button" class="secondary-btn" id="cancelModal">取消</button><button class="primary-btn" type="submit">验证并恢复</button></div></form>`);$('#cancelModal').onclick=closeModal;$('#backupJsonFile').onchange=async event=>{const file=event.target.files&&event.target.files[0];if(!file)return;try{$('#backupImportText').value=await readLocalTextFile(file);$('#backupFileState').textContent=`已读取：${file.name}`}catch(_){toast('无法读取该备份文件',true)}};$('#importForm').onsubmit=e=>{e.preventDefault();try{const restored=validateBackupState(JSON.parse(new FormData(e.target).get('data')));if(!requireSafetyBackup('恢复前备份'))return;state=restored;lastGeneratedId=['students','classes','attendance','transactions'].reduce((max,key)=>state[key].reduce((highest,item)=>Math.max(highest,item.id),max),lastGeneratedId);ensureClasses();ensureEnrollmentHistory();normalizeLongTermStudentAssignments();ensureAttendanceSnapshots();if(!saveState())return false;closeModal();render();toast('备份恢复成功，恢复前数据也已另存一份')}catch(error){toast(error&&error.message||'备份文件或文本无效，请检查是否完整',true)}};}
function resetData(){
  const summary=`${state.students.length} 名学生 · ${state.classes.length} 个班级 · ${state.attendance.length} 条签到记录 · ${state.transactions.length} 条课时记录`;
  openDangerConfirmation({icon:'⌫',title:'清空本机全部数据',description:'这是最高风险操作，请仔细确认',subject:'未来学校学生管理系统本机数据',subjectMeta:summary,impactTitle:'全部资料会从应用中删除',impactText:'清空前会先在平板“下载”目录创建完整恢复备份；学生、班级、签到、课时、人脸和设置随后恢复为初始状态。',confirmLabel:'备份后清空全部数据',verifyText:'确认清空',onConfirm:()=>{if(!requireSafetyBackup('清空前备份'))return false;state=emptyState();if(!saveState())return false;isAdminAuthenticated=false;currentView='checkin';closeModal();render();toast('本机数据已清空，清空前备份正在保存')}});
}
function seedDemo(){
  const base=Date.now();state.classes=[];state.students=[
    {id:base+1,name:'林一诺',age:12,gender:'女',studentNo:'S001',contact:'13800000001',school:'育才实验学校',grade:'六年级',className:'Python 基础 A 班',teacher:'王老师',hours:22,faceRegistered:true},
    {id:base+2,name:'周子墨',age:9,gender:'男',studentNo:'S002',contact:'13800000002',school:'星河小学',grade:'三年级',className:'少儿编程启蒙班',teacher:'李老师',hours:15,faceRegistered:false},
    {id:base+3,name:'陈雨桐',age:14,gender:'女',studentNo:'S003',contact:'13800000003',school:'启明中学',grade:'八年级',className:'C++ 信息学 B 班',teacher:'张老师',hours:6,faceRegistered:true},
    {id:base+4,name:'许嘉言',age:11,gender:'男',studentNo:'S004',contact:'13800000004',school:'育才实验学校',grade:'五年级',className:'Python 基础 A 班',teacher:'王老师',hours:4,faceRegistered:true}
  ];
  ensureClasses();ensureEnrollmentHistory();state.classes.forEach((course,index)=>{course.schedule=['每周六两课时','每周日两课时','每周六两课时'][index]||'';course.scheduleWeekday=[6,7,6][index]||0;course.scheduleStart=['09:00','14:00','14:00'][index]||'';course.reminderEnabled=false;course.classroom=`编程教室 ${index+1}`});const now=new Date();state.attendance=[];[0,1,3,5].forEach((ago,i)=>{const d=new Date(now);d.setDate(d.getDate()-ago);const s=state.students[i%state.students.length],snapshot=studentProfileAtDate(s,localDate(d));state.attendance.push({id:uid(),studentId:s.id,date:localDate(d),time:`${String(10+i).padStart(2,'0')}:18`,method:['人脸','姓名','学号'][i%3],classId:snapshot.classId||'',className:snapshot.className||'',teacher:snapshot.teacher||'',classroom:snapshot.classroom||''})});state.transactions=[];if(!saveState())return false;render();toast('演示数据已添加');
}

$('#todayLabel').textContent = new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'});
function bindImmediateNavigation(nav=$('#navList'),selector='[data-view],[data-more],[data-dock-add]',activate=activateClassSpaceNavigation){
  let lastTouchButton=null,lastTouchTime=-Infinity;
  const buttonFor=event=>{const button=event.target.closest(selector);return button&&nav.contains(button)?button:null};
  const press=event=>{
    if(event.isPrimary===false||(event.pointerType&&event.pointerType!=='touch'&&event.pointerType!=='pen'))return;
    if(event.touches&&event.touches.length!==1)return;
    const button=buttonFor(event);if(!button)return;
    // The fixed menu is not a scrolling surface. Respond on contact, not on release.
    lastTouchButton=button;lastTouchTime=performance.now();
    if(event.cancelable)event.preventDefault();
    activate(button);
    // A touch release must not activate anything newly exposed beneath this finger.
    skipOpeningBackdropClick=true;
  };
  if(window.PointerEvent)nav.addEventListener('pointerdown',press,{passive:false});
  else nav.addEventListener('touchstart',press,{passive:false});
  nav.onclick=event=>{
    const button=buttonFor(event);if(!button)return;
    // Keep keyboard/mouse access, but don't repeat a touch action on its synthetic click.
    if(event.detail!==0&&button===lastTouchButton&&performance.now()-lastTouchTime<1000){event.preventDefault();return}
    activate(button);
  };
}
bindImmediateNavigation();
bindClassSpaceShell();
$('#quickAddBtn').onclick=()=>openStudentModal();
render();
window.__futureSchoolAppStarted=true;
if(typeof window.finishAppStartup==='function')window.finishAppStartup();
if(window.__pendingAdminEntry){window.__pendingAdminEntry=false;setTimeout(openAdminLogin,80)}
function refreshScreenLayout(){
  clearTimeout(window.cameraPositionResizeTimer);window.cameraPositionResizeTimer=setTimeout(()=>{syncScreenOrientationClass();if(document.body.classList.contains('face-dialog-open'))applyFacePopupPosition()},120);
}
window.addEventListener('resize',refreshScreenLayout);
window.addEventListener('orientationchange',refreshScreenLayout);
function scheduleFacePhotoMaintenance(){
  if(!state.students.some(student=>hasSFaceProfile(student)&&student.faceImage&&student.faceImage.length>MAX_FACE_THUMBNAIL_LENGTH))return;
  const run=()=>{if(!$('#modalBackdrop').classList.contains('hidden')){setTimeout(scheduleFacePhotoMaintenance,3000);return}optimizeExistingFacePhotos()};
  if(typeof window.requestIdleCallback==='function')window.requestIdleCallback(run,{timeout:10000});else setTimeout(run,3000);
}
let displayedDay = localDate();
function refreshDayContext(){
  const today=localDate();if(today===displayedDay)return;
  if(spaceSelectedDate===displayedDay)spaceSelectedDate='';
  displayedDay=today;$('#todayLabel').textContent=new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'});
  invalidatePageCache();syncNativeReminders();listPages.attendance=1;
  if(currentView!=='settings')render();
}
window.addEventListener('focus',refreshDayContext);
window.addEventListener('pageshow',refreshDayContext);
if(typeof document.addEventListener==='function')document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='hidden')refreshDayContext()});
if(typeof window.setInterval==='function')window.setInterval(()=>{if(document.visibilityState!=='hidden'){refreshDayContext();refreshClassSpaceClock()}},30000);
setTimeout(scheduleFacePhotoMaintenance,1500);
setTimeout(()=>{if(!state.settings.cameraPositionConfigured&&$('#modalBackdrop').classList.contains('hidden'))openCameraPositionSetup(true)},260);
