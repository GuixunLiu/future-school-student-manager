const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const NativeDate = Date;
class TestDate extends NativeDate {
  constructor(...args){ super(...(args.length ? args : ['2026-08-30T12:00:00+08:00'])); }
  static now(){ return new NativeDate('2026-08-30T12:00:00+08:00').getTime(); }
}

const fixture = {
  students: [
    {id:1,name:'甲同学',school:'未来小学',className:'A班',teacher:'王老师',hours:10},
    {id:2,name:'乙同学',school:'未来小学',className:'B班',teacher:'王老师 ',hours:10}
  ],
  classes: [
    {id:11,name:'A班',teacher:'王老师'},
    {id:12,name:'B班',teacher:' 王老师 '}
  ],
  attendance: [
    {id:21,studentId:1,date:'2026-08-30',time:'10:00',method:'姓名'},
    {id:22,studentId:1,date:'2026-08-23',time:'10:00',method:'姓名'},
    {id:23,studentId:2,date:'2026-08-23',time:'10:01',method:'姓名'}
  ],
  transactions: [],
  settings: {orgName:'未来学校学生管理系统',campusCode:'01',studentNoCounters:{},weeklyTarget:2,adminUser:'admin',adminPassword:'123456',cameraPositionConfigured:true}
};
let persistedState=JSON.stringify(fixture);

const element = () => ({style:{},classList:{toggle(){},add(){},remove(){},contains(){return false}},innerHTML:'',textContent:'',onclick:null,addEventListener(){}});
const elements = new Map(['#todayLabel','#navList','#quickAddBtn','#adminAccessBtn','#viewTitle','#viewSubtitle','#brandName','#content'].map(selector=>[selector,element()]));
const context = {
  console,
  setTimeout,
  clearTimeout,
  btoa:value=>Buffer.from(value,'binary').toString('base64'),
  atob:value=>Buffer.from(value,'base64').toString('binary'),
  Date:TestDate,
  Map,
  Set,
  localStorage:{getItem:()=>persistedState,setItem:(key,value)=>{persistedState=value}},
  document:{body:element(),querySelector:selector=>elements.get(selector)||null,querySelectorAll:()=>[]},
  window:{scrollTo(){},addEventListener(){}},
  navigator:{},
  confirm:()=>true
};
vm.createContext(context);
const appSource = ['pattern-lock.js','class-space.js','lesson-review.js','wechat-notices.js','lesson-cycle.js','student-leave.js','course-groups.js','app.js'].map(file=>fs.readFileSync('app/src/main/assets/'+file,'utf8')).join('\n');
assert(!appSource.includes('?.'),'主程序不能包含旧版 Android WebView 无法解析的可选链语法');
assert(!appSource.includes('??'),'主程序不能包含旧版 Android WebView 无法解析的空值合并语法');
assert(!appSource.includes('||='),'主程序不能包含旧版 Android WebView 无法解析的逻辑赋值语法');
assert(!appSource.includes('Object.fromEntries'),'主程序不能依赖旧版 WebView 缺少的 Object.fromEntries');
vm.runInContext(appSource,context);

assert.strictEqual(vm.runInContext('cameraSettingKey("landscape")',context),'cameraLandscapeEdge');
assert.strictEqual(vm.runInContext('cameraSettingKey("portrait")',context),'cameraPortraitEdge');
assert.strictEqual(vm.runInContext('validCameraEdge("right")',context),'right');
assert.strictEqual(vm.runInContext('validCameraEdge("invalid")',context),'top');
context.AndroidBridge={getScreenOrientation:()=>"landscape"};
context.window.AndroidBridge=context.AndroidBridge;
assert.strictEqual(vm.runInContext('deviceOrientation()',context),'landscape');
assert.strictEqual(vm.runInContext('syncScreenOrientationClass()',context),'landscape');
delete context.AndroidBridge;
delete context.window.AndroidBridge;

assert.strictEqual(vm.runInContext('calendarWeekRange(0).start',context),'2026-08-24');
assert.strictEqual(vm.runInContext('calendarWeekRange(0).end',context),'2026-08-30');
assert.strictEqual(vm.runInContext('calendarWeekRange(-1).start',context),'2026-08-17');
assert.strictEqual(vm.runInContext('weekStart()',context),'2026-08-24');
assert.strictEqual(vm.runInContext('calendarWeekAttendance(0).length',context),1);
assert.strictEqual(vm.runInContext('state.attendance[0].className',context),'A班');
assert.strictEqual(vm.runInContext('state.attendance[0].teacher',context),'王老师');
assert.strictEqual(vm.runInContext('calendarWeekAttendance(-1).length',context),2);
assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(Object.keys(teacherStudentGroups()))',context)),['王老师']);
assert.strictEqual(vm.runInContext('teacherStudentGroups()["王老师"].length',context),2);
assert.strictEqual(vm.runInContext('nextImportStudentNo(new Set())',context),'26010001');
assert.strictEqual(vm.runInContext('nextImportStudentNo(new Set(["26010001"]))',context),'26010002');
assert.strictEqual(vm.runInContext('studentFacingTeacherName("胡晓波老师")',context),'胡老师');
assert.strictEqual(vm.runInContext('studentFacingTeacherName("欧阳明 老师")',context),'欧阳老师');
assert.strictEqual(vm.runInContext('studentFacingTeacherName("")',context),'未设置老师');
assert.strictEqual(vm.runInContext('adminCredentialsMatch("admin","123456")',context),true);
assert.strictEqual(vm.runInContext('adminCredentialsMatch("admin","wrong")',context),false);
assert.strictEqual(vm.runInContext('hoursWarningLevel(10)',context),'critical');
assert.strictEqual(vm.runInContext('hoursWarningLabel(10)',context),'10 节课以内');
assert.strictEqual(vm.runInContext('hoursWarningLevel(15)',context),'low');
assert.strictEqual(vm.runInContext('hoursWarningLabel(15)',context),'15 节课以内');
assert.strictEqual(vm.runInContext('hoursWarningLevel(15.5)',context),'');
const studentClassItems = JSON.parse(vm.runInContext('JSON.stringify(studentClassFilterItems().map(item=>({key:item.key,count:item.count})))',context));
assert.deepStrictEqual(studentClassItems,[{key:'all',count:2},{key:'class:A班',count:1},{key:'class:B班',count:1}]);
vm.runInContext('studentClassFilter="class:B班";studentStatusFilter="all"',context);
assert.strictEqual(vm.runInContext('state.students.filter(student=>studentMatchesStatusFilter(student)&&studentMatchesClassFilter(student)).length',context),1);
assert.strictEqual(vm.runInContext('state.students.filter(student=>studentMatchesStatusFilter(student)&&studentMatchesClassFilter(student))[0].name',context),'乙同学');
assert(vm.runInContext('renderStudents()',context).includes('班级分类'));
vm.runInContext('studentClassFilter="all";studentStatusFilter="all"',context);
const enrollmentHistory = JSON.parse(vm.runInContext(`JSON.stringify((()=>{const student={id:90,name:'测试',className:'旧班',teacher:'旧老师',createdAt:'2026-01-01',enrollmentHistory:[{classId:1,className:'旧班',teacher:'旧老师',classroom:'1室',startDate:'2026-01-01',endDate:''}]};updateStudentEnrollment(student,{id:2,name:'新班',teacher:'新老师',classroom:'2室'},'2026-09-01');return{before:studentProfileAtDate(student,'2026-08-31'),after:studentProfileAtDate(student,'2026-09-01'),history:student.enrollmentHistory}})())`,context));
assert.strictEqual(enrollmentHistory.before.className,'旧班');
assert.strictEqual(enrollmentHistory.after.className,'新班');
assert.strictEqual(enrollmentHistory.history[0].endDate,'2026-08-31');
assert.strictEqual(enrollmentHistory.history.length,2);
const longTermLifecycle = JSON.parse(vm.runInContext(`JSON.stringify((()=>{const student={id:91,name:'长期测试',className:'A班',teacher:'王老师',createdAt:'2026-01-01',enrollmentHistory:[{classId:11,className:'A班',teacher:'王老师',classroom:'',startDate:'2026-01-01',endDate:''}],longTermAbsenceHistory:[]};markStudentLongTerm(student,'2026-09-01');const paused={className:student.className,teacher:student.teacher,pausedClassName:student.pausedClassName,enrollmentEnd:student.enrollmentHistory[0].endDate,longTerm:student.longTermAbsent};restoreStudentToCourse(student,state.classes[1],'2026-09-05');return{paused,restored:{className:student.className,teacher:student.teacher,longTerm:student.longTermAbsent,absenceEnd:student.longTermAbsenceHistory[0].endDate,enrollmentStart:student.enrollmentHistory[1].startDate}}})())`,context));
assert.deepStrictEqual(longTermLifecycle.paused,{className:'',teacher:'',pausedClassName:'A班',enrollmentEnd:'2026-08-31',longTerm:true});
assert.deepStrictEqual(longTermLifecycle.restored,{className:'B班',teacher:' 王老师 ',longTerm:false,absenceEnd:'2026-09-04',enrollmentStart:'2026-09-05'});
vm.runInContext('Object.assign(state.classes[0],{scheduleWeekday:6,scheduleStart:"09:00",reminderEnabled:true})',context);
assert.strictEqual(vm.runInContext('classScheduleLabel(state.classes[0])',context),'每周六 09:00');
assert.strictEqual(vm.runInContext('classReminderLabel(state.classes[0])',context),'开课 5 分钟后提醒 · 每周六 09:00');
const reminderPayload = JSON.parse(vm.runInContext('JSON.stringify(reminderSyncPayload())',context));
assert.deepStrictEqual(reminderPayload.classes[0],{id:11,name:'A班',teacher:'王老师',classroom:'',scheduleWeekday:6,scheduleIntervalWeeks:1,scheduleAnchorDate:'',scheduleStart:'09:00',reminderEnabled:true,extraLessonDates:[],cancelledLessonDates:[]});
assert.strictEqual(reminderPayload.attendance.length,1);
assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(parseImportedLongTermStatus("是"))',context)),{valid:true,value:true});
assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(parseImportedLongTermStatus("长期不来"))',context)),{valid:true,value:true});
assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(parseImportedLongTermStatus("正常在读"))',context)),{valid:true,value:false});
assert.deepStrictEqual(JSON.parse(vm.runInContext('JSON.stringify(parseImportedLongTermStatus("随便写"))',context)),{valid:false,value:false});
const learning = JSON.parse(vm.runInContext(`JSON.stringify((()=>{const unit=(x,y)=>{const out=Array(128).fill(0),norm=Math.hypot(x,y);out[0]=x/norm;out[1]=y/norm;return out};const student={sfaceModelVersion:4,sfaceDescriptors:[unit(1,0)],sfaceAdaptiveDescriptors:[],faceLearningCount:0};const added=learnFromConfirmedFace(student,[unit(.9,.1),unit(.9,.11)],{score:.8,margin:.2});return{added,count:student.faceLearningCount,samples:student.sfaceAdaptiveDescriptors.length}})())`,context));
assert.deepStrictEqual(learning,{added:1,count:1,samples:1});
const packedFace = JSON.parse(vm.runInContext(`JSON.stringify((()=>{const sample=Array(128).fill(0);sample[0]=.8;sample[1]=.6;const packed=packFaceDescriptor(sample),unpacked=unpackFaceDescriptor(packed);return{prefix:packed.slice(0,4),length:packed.length,cosine:faceCosine(normalizedVector(sample),unpacked)}})())`,context));
assert.strictEqual(packedFace.prefix,'q16:');
assert(packedFace.length<360);
assert(packedFace.cosine>.99999);
const largeFaceRoster = JSON.parse(vm.runInContext(`JSON.stringify((()=>{const base=Array(128).fill(0);base[0]=1;const packed=packFaceDescriptor(base),students=Array.from({length:500},(_,index)=>({id:index,sfaceModelVersion:4,sfaceDescriptors:[packed,packed,packed],sfaceAdaptiveDescriptors:[packed,packed]})),index=buildFaceRecognitionIndex(students);return{students:index.length,samples:index[0].samples.length,jsonSize:JSON.stringify(students).length}})())`,context));
assert.deepStrictEqual({students:largeFaceRoster.students,samples:largeFaceRoster.samples},{students:500,samples:5});
assert(largeFaceRoster.jsonSize<950000);
vm.runInContext('state.students[1].longTermAbsent=true',context);
assert.strictEqual(vm.runInContext('reportableStudents().length',context),1);
assert.strictEqual(vm.runInContext('lowHoursStudents().length',context),1);
assert.strictEqual(vm.runInContext('lowHoursStudents()[0].name',context),'甲同学');
assert.strictEqual(vm.runInContext('studentsUnderHours(10).length',context),1);
assert.strictEqual(vm.runInContext('lowHoursExportRows(15).length',context),1);
assert.strictEqual(vm.runInContext('lowHoursExportRows(15)[0][0]',context),'甲同学');
assert.strictEqual(vm.runInContext('attendanceForStudents(calendarWeekAttendance(0),reportableStudents()).length',context),1);
const hoursPage = vm.runInContext('renderHours()',context);
assert(hoursPage.includes('课时不足统计已自动排除长期不来学生'));
assert(hoursPage.includes('暂不参与课时不足提醒'));
assert(hoursPage.includes('data-hours-alert="10"'));
assert(hoursPage.includes('data-hours-alert="15"'));
assert(hoursPage.includes('查看并导出名单'));
const report = vm.runInContext('renderReport()',context);
assert(report.includes('本周 · 8月24日—8月30日'));
assert(report.includes('按班级管理中的任课老师汇总 · 合并所教全部班级'));
assert(report.includes('长期不来生效后不再计应到'));
assert(report.includes('1 次有效签到 / 2 次应到'));
assert(report.includes('周历到课率'));
assert(report.includes('data-report-student-detail="attended"'));
assert(report.includes('data-report-student-detail="absent"'));
assert(fs.readFileSync('app/src/main/assets/index.html','utf8').includes('future-school-logo.png'),'机构标识显示在共享页头');
assert(vm.runInContext('renderKioskCheckin()',context).includes('space-face-action'));
vm.runInContext('state.students[1].longTermAbsent=false;state.students[1].longTermAbsentSince=""',context);
const makeupGroups = JSON.parse(vm.runInContext('JSON.stringify(makeupClassGroupsAt("2026-08-30").map(group=>({name:group.className,students:group.members.map(item=>item.student.name)})))',context));
assert.deepStrictEqual(makeupGroups,[{name:'B班',students:['乙同学']}]);
const makeupResult = JSON.parse(vm.runInContext('JSON.stringify(recordMakeupAttendance([2],"2026-08-30","老师核对课堂点名"))',context));
assert.deepStrictEqual(makeupResult,{ok:true,count:1});
assert.strictEqual(vm.runInContext('state.students[1].hours',context),9);
assert.strictEqual(vm.runInContext('state.attendance.filter(record=>record.studentId===2&&record.date==="2026-08-30"&&record.method==="老师补签"&&record.makeup).length',context),1);
assert.strictEqual(vm.runInContext('state.transactions.filter(record=>record.studentId===2&&record.attendanceId&&record.reason.includes("老师补签扣减")).length',context),1);
assert.strictEqual(vm.runInContext('makeupClassGroupsAt("2026-08-30").length',context),0);
const checkinPage = vm.runInContext('renderCheckin()',context);
assert(checkinPage.includes('data-makeup-checkin'));
assert(checkinPage.includes('最近补签记录'));
vm.runInContext(`(()=>{const base=Array(128).fill(0);base[0]=1;const packed=packFaceDescriptor(base);state.students=Array.from({length:500},(_,index)=>({id:index+1,name:'学生'+index,studentNo:'26'+String(index+1).padStart(6,'0'),hours:20,sfaceModelVersion:4,faceSecurityVersion:4,faceRegistered:true,faceImage:'data:image/jpeg;base64,'+'A'.repeat(1800),sfaceDescriptors:[packed,packed,packed],sfaceAdaptiveDescriptors:[packed,packed]}));state.attendance=[{id:1,studentId:500,date:'2026-08-30',time:'10:00',method:'人脸'}];state.transactions=[];saveState()})()`,context);
const restoredLargeState=JSON.parse(persistedState);
assert.strictEqual(restoredLargeState.students.length,500);
assert.strictEqual(restoredLargeState.attendance.length,1);
assert.strictEqual(restoredLargeState.students[499].sfaceDescriptors.length,3);
assert(Buffer.byteLength(persistedState,'utf8')<2500000);
assert.strictEqual(vm.runInContext('state=loadState();state.students.filter(hasSFaceProfile).length',context),500);
assert.strictEqual(vm.runInContext('buildFaceRecognitionIndex(state.students).length',context),500);
assert.strictEqual(vm.runInContext('state.attendance.length',context),1);
console.log('weekly report smoke test passed');
