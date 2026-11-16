'use strict';
const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const files=fs.readdirSync(path.join(root,'server/tests')).filter(name=>name.endsWith('.test.cjs')).sort().map(name=>'server/tests/'+name);
for(const args of [['--test',...files],['logic-regression-test.js']]){
 const result=spawnSync(process.execPath,args,{cwd:root,stdio:'inherit',env:process.env,windowsHide:true});
 if(result.error){console.error(result.error.message);process.exit(1);}
 if(result.status!==0)process.exit(result.status||1);
}
