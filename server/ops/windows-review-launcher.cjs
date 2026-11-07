'use strict';
// Keep the CMD entry point ASCII with CRLF. Node handles UTF-8 messages and JSON.
const path=require('node:path');
const {loadPlan,upload}=require('./upload-roster-review.cjs');
const plan=path.join(__dirname,'review-plan.private.json');
async function main(){
 const args=process.argv.slice(2);
 if(args.length>1||args.length===1&&args[0]!=='--self-check')throw Error('Unsupported launcher arguments');
 const checked=loadPlan(plan);
 if(args[0]==='--self-check'){
  console.log(JSON.stringify({launcher:'ok',files:checked.payloads.length,checksum_verified:true,network_connected:false,expected:checked.plan.expected}));
  return;
 }
 console.log('将先备份，再建立独立的只读名册核对副本。原服务及平板继续正常使用。');
 console.log('请在 SSH 的 password 提示处输入服务器密码；屏幕不显示密码字符是正常的。');
 console.log('密码不会保存在文件中，不需要发给 Codex。');
 const result=await upload(plan);
 console.log('核对副本部署成功：'+result.active+'名在读、'+result.paused+'名归档；原服务继续运行。');
 console.log('部署回执已在本机保存：'+result.path);
 console.log('请将成功结果告诉 Codex，继续打开网页验收。');
}
main().catch(error=>{console.error('未完成：'+error.message);console.error('请保留窗口结果，不要反复重跑或删除服务器文件。');process.exitCode=1;});
