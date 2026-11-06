'use strict';
const assert=require('node:assert/strict'),{validate}=require('../ops/check-web-entry.cjs');
const good={format:'future-school-web-entry-inventory',schema_version:1,read_only:true,ports:[{port:443,loopback_only:false}],port_inventory_available:true,tools:{nginx:true,caddy:false,certbot:false,openssl:true},review_health:null,review_backend_ready:false,review_service:{},server_modified:false,database_opened:false,private_keys_read:false,public_entry_enabled:false,pending:['certificate']};
assert.equal(validate(JSON.stringify(good)).read_only,true);
for(const change of [{server_modified:true},{public_entry_enabled:true},{extra:'private'},{ports:[{port:9999,loopback_only:true}]},{tools:{nginx:true,extra:true}}])assert.throws(()=>validate(JSON.stringify({...good,...change})));
assert.throws(()=>validate('bad JSON'));
console.log('Web entry client passed: read-only result, no claimed public readiness, bounded port/tool scope and unexpected fields rejected. No network.');
