'use strict';

function openStudentFaceCapture({title, message, enrollment=false, submitLabel, save, complete, handleError}){
 const dialog=$('device-dialog'),content=$('dialog-content');
 FaceEngine.stop();
 content.replaceChildren();
 const capture=node('section',undefined,'student-face-capture');
 const heading=node('h2',title);heading.id='dialog-title';
 const stage=node('div',undefined,'face-camera-stage'),video=node('video');
 stage.hidden=true;
 video.autoplay=true;video.muted=true;video.playsInline=true;
 video.setAttribute('aria-label',enrollment?'学生人脸采集画面':'学生人脸签到画面');
 stage.append(video,node('div','学生本人正对镜头，保持光线充足','face-camera-guide'));
 const status=node('p','尚未打开摄像头','face-status');
 status.setAttribute('role','status');
 const form=node('form'),submit=node('button',submitLabel);
 submit.type='submit';submit.disabled=true;
 const consent=enrollment?field(form,'监护人授权凭证'):null;
 let confirmed=null;
 if(consent){
  consent.maxLength=100;consent.placeholder='填写监护人授权登记编号';
  const label=node('label',undefined,'check');
  confirmed=node('input');confirmed.type='checkbox';confirmed.required=true;
  label.append(confirmed,node('span','已核验学生本人及监护人授权'));
  form.append(label);
 }
 form.append(submit);
 let ready=false,payload=null;
 const current=()=>dialog.open&&content.contains(video);
 const showStatus=text=>{if(current())status.textContent=text;};
 const open=button('打开摄像头',async()=>{
  ready=false;payload=null;open.disabled=true;submit.disabled=true;
  try{
   await FaceEngine.prepare(showStatus);
   if(!current())return;
   stage.hidden=false;
   await FaceEngine.open(video);
   if(!current()){FaceEngine.stop(video);return;}
   ready=true;showStatus('摄像头已就绪，请学生本人入镜');
  }catch(error){if(current())stage.hidden=true;showStatus(error.message);}
  finally{if(current()){open.disabled=false;submit.disabled=!ready;}}
 },'secondary');
 if(consent)consent.addEventListener('input',()=>{
  consent.setCustomValidity('');
  if(payload){payload=null;submit.disabled=!ready;}
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(submit.disabled)return;
  if(consent&&!consent.value.trim()){
   consent.setCustomValidity('请填写监护人授权凭证');consent.reportValidity();return;
  }
  submit.disabled=true;open.disabled=true;
  if(consent){consent.readOnly=true;confirmed.disabled=true;}
  try{
   if(!payload){
    const frames=await FaceEngine.capture(video,showStatus);
    if(!current())return;
    FaceEngine.stop(video);ready=false;
    payload={ref:runtimeRequestRef(),model_version:FaceEngine.MODEL,descriptors:frames};
    if(consent)Object.assign(payload,{consent_ref:consent.value.trim(),identity_confirmed:confirmed.checked});
   }
   showStatus(enrollment?'正在加密保存人脸档案…':'正在核验所选学生…');
   const result=await save(payload);
   if(current())await complete(result);
  }catch(error){
   if(!current())return;
   if(await handleError?.(error))return;
   if(error.status>=400&&error.status<500)payload=null;
   showStatus(error.message||'操作未完成，请重试');
  }finally{
   if(current()){
    submit.disabled=!payload&&!ready;open.disabled=false;
    if(consent){consent.readOnly=false;confirmed.disabled=false;}
   }
  }
 });
 capture.append(heading,node('p',message,'hint'),stage,status,open,form);
 content.append(capture);
 if(!dialog.open)dialog.showModal();
 heading.tabIndex=-1;heading.focus();
}

function openTeacherFaceCheckin(student,lesson=currentLesson?.lesson){
 if(!webFaceEnabled())return notify('等待开放');
 if(!lesson)throw Error('课堂资料已失效，请刷新后重试');
 const endpoint='/v1/teacher/lessons/'+encodeURIComponent(lesson.id);
 const refreshOnConflict=async error=>{
  if(!['attendance_conflict','attendance_changed','lesson_unavailable'].includes(error.code))return false;
  $('device-dialog').close();
  notify(error.message);
  await teacherLesson(lesson.id).catch(showError);
  return true;
 };
 const checkin=(prompt='请这位学生本人入镜，核验通过后完成签到并扣除本节课课时。')=>openStudentFaceCapture({
  title:'人脸签到 · '+student.name,message:prompt,submitLabel:'核验并签到',
  save:({ref,model_version,descriptors})=>api(endpoint+'/face-checkin',{
   event_ref:ref,student_id:student.student_id,model_version,descriptors
  }),
  complete:async result=>{
   $('device-dialog').close();
   notify('人脸签到成功：'+result.student_name+'，剩余 '+teacherLessonHours(result.balance_units));
   await teacherLesson(lesson.id).catch(()=>notify('签到已完成，请刷新名单查看结果'));
  },
  handleError:async error=>{
   if(error.code==='face_profile_missing'){enroll();return true;}
   return refreshOnConflict(error);
  },
 });
 const enroll=()=>openStudentFaceCapture({
  title:'录入人脸 · '+student.name,
  message:'这位学生尚未录入人脸。请先核验学生本人和监护人授权，录入后再为这位学生签到。',
  enrollment:true,submitLabel:'采集并保存',
  save:({ref,...payload})=>api(endpoint+'/face-profile',{
   ...payload,student_id:student.student_id,command_ref:ref
  },'PUT'),
  complete:()=>{
   student.face_registered=true;
   checkin('人脸档案已保存。请再次采集这位学生的人脸以完成签到。');
  },
  handleError:async error=>{
   if(error.code==='face_profile_exists'){
    student.face_registered=true;
    checkin('这位学生已经有人脸档案，请重新采集以完成签到。');
    return true;
   }
   return refreshOnConflict(error);
  },
 });
 if(student.face_registered===false)enroll();
 else checkin();
}
