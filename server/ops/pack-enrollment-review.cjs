'use strict';
// Offline transport packaging only; no authentication, network or activation.
const crypto=require('node:crypto');
const PAGE_BYTES=12288,MAX_BODY_BYTES=16384;
const FIELDS={classes:'server_id local_id name teacher_id teacher_name',students:'server_id local_id external_ref name class_id class_local_id balance_units',facts:'lesson_id student_id status expected charge_units starts_at'};
const LIMITS={classes:1000,students:2000,facts:10000};
function encoded(value){
 const sorted=v=>{if(Array.isArray(v))return v.map(sorted);if(v&&typeof v==='object')return Object.fromEntries(Object.keys(v).sort().map(k=>[k,sorted(v[k])]));if(typeof v==='number'&&!Number.isSafeInteger(v)||v===undefined||typeof v==='function')throw Error('Unsupported review value');return v;};
 return JSON.stringify(sorted(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
const digest=value=>crypto.createHash('sha256').update(encoded(value)).digest('hex');
function exact(value,fields){if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join('|')!==fields.split(' ').sort().join('|'))throw Error('Unexpected review fields');}
function pack(body,commandRef){
 exact(body,'workspace_role device_id generation baseline_sha256 source_captured_at source');exact(body.source,'classes students facts');
 if(body.workspace_role!=='admin'||typeof commandRef!=='string'||!commandRef.trim()||commandRef.length>80)throw Error('Explicit administrator review reference required');
 const manifest={},pages=[];
 for(const section of Object.keys(LIMITS)){
  const rows=body.source[section];if(!Array.isArray(rows)||rows.length>LIMITS[section]||section!=='facts'&&!rows.length)throw Error('Review scope limit');
  let chunk=[],index=0;
  const flush=()=>{if(chunk.length){pages.push({workspace_role:'admin',section,page_index:index++,rows:chunk,sha256:digest(chunk)});chunk=[];}};
  for(const row of rows){exact(row,FIELDS[section]);if(Buffer.byteLength(encoded([row]))>PAGE_BYTES)throw Error('Individual review row exceeds page limit');
   if(chunk.length===100||Buffer.byteLength(encoded([...chunk,row]))>PAGE_BYTES)flush();chunk.push(row);
  }
  flush();manifest[section]={rows:rows.length,pages:index,sha256:digest(rows)};
 }
 if(pages.length>500)throw Error('Review page limit');
 const {source,...header}=body,start={...header,manifest,command_ref:commandRef};
 for(const request of [start,...pages])if(Buffer.byteLength(JSON.stringify(request))>MAX_BODY_BYTES)throw Error('Request exceeds unchanged HTTP body limit');
 return{start,pages,metadata:{segmented:true,request_limit:MAX_BODY_BYTES,page_rows_limit:100,page_bytes_limit:PAGE_BYTES,pages:pages.length,offline_only:true,server_written:false,activation_required:true}};
}
module.exports={encoded,digest,pack};
