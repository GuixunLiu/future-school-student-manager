'use strict';
async function adminFaceDashboard(){
 if(!webFaceEnabled())return notify('等待开放');
 const data=await api('/v1/admin/students'),students=data.items;$('identity').replaceChildren(node('span',identity.display_name+' · 管理员'),button('退出',logout,'secondary'));
 const heading=node('div',undefined,'runtime-summary'),copy=node('div');copy.append(node('h1','学生人脸档案'),node('p','人脸仅用于本人签到。录入前须核验监护人授权；原始照片不上传，服务器只保存加密后的三帧特征。'));
 const actions=node('div',undefined,'toolbar');actions.append(button('返回管理工作台',dashboard,'secondary'),button('刷新',adminFaceDashboard,'secondary'));heading.append(copy,actions);
 const filter=node('section',undefined,'panel teacher-filter'),label=node('label','搜索学生'),search=node('input');search.type='search';search.placeholder='姓名、学号或班级';search.setAttribute('aria-label','搜索人脸档案');label.append(search);filter.append(label);
 const list=node('section',undefined,'grid face-student-grid');
 function render(){const query=search.value.trim().toLowerCase(),visible=students.filter(student=>student.active&&(!query||[student.name,student.external_ref,...(student.class_names||[])].some(value=>String(value||'').toLowerCase().includes(query))));list.replaceChildren();if(!visible.length)list.append(node('div','没有符合条件的在读学生','empty'));for(const student of visible){const card=node('article',undefined,'panel face-student-card'),status=node('span',student.face_registered?'已录入':'未录入','badge '+(student.face_registered?'arrived':'late'));const title=node('div',undefined,'item-head');title.append(node('h2',student.name),status);card.append(title,node('p',student.external_ref+' · '+((student.class_names||[]).join('、')||'未分班')),node('p',student.face_registered?'SFace 加密档案可用':'尚未建立服务器人脸档案','hint'),button(student.face_registered?'重新录入人脸':'录入人脸',()=>openFaceEnrollment(student)));list.append(card);}}
 search.addEventListener('input',render);render();$('main').replaceChildren(heading,filter,list);
}
function openFaceEnrollment(student){
 if(!webFaceEnabled())return notify('等待开放');
 openStudentFaceCapture({
  title:(student.face_registered?'重新录入':'录入')+'人脸 · '+student.name,
  message:student.external_ref+' · '+((student.class_names||[]).join('、')||'未分班'),
  enrollment:true,submitLabel:'采集并加密保存',
  save:({ref,model_version,descriptors,consent_ref})=>api('/v1/admin/students/'+encodeURIComponent(student.id)+'/face-profile',{
   command_ref:ref,model_version,descriptors,consent_ref
  },'PUT'),
  complete:async result=>{
   $('device-dialog').close();
   notify(result.duplicate?'人脸档案已保存':student.name+' 的人脸档案已加密保存');
   await adminFaceDashboard().catch(showError);
  },
 });
}
