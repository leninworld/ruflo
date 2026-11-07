// NODE_OPTIONS preload for offline test lanes. Actual Docker network none is primary.
// Loopback is needed for real HTTP/MCP/WebSocket integration tests.
'use strict';
const net=require('node:net'),dns=require('node:dns'),http=require('node:http'),https=require('node:https');
const fs=require('node:fs');
function allowed(host){return host==='localhost'||host==='127.0.0.1'||host==='::1'||host==='[::1]'||host===undefined;}
function check(host){if(allowed(host))return;const row={state:'BLOCKED_NON_LOOPBACK',host:String(host)};if(process.env.E1_NETWORK_LOG)fs.appendFileSync(process.env.E1_NETWORK_LOG,JSON.stringify(row)+'\n');throw Object.assign(new Error('E1 offline network guard: '+host),{code:'E1_NETWORK_BLOCKED'});}
const connect=net.Socket.prototype.connect;
net.Socket.prototype.connect=function(...args){let o=args[0];if(Array.isArray(o))o=o[0];if(typeof o==='object'&&o!==null){if(!o.path)check(o.host||o.hostname);}else if(typeof o==='number')check(typeof args[1]==='string'?args[1]:undefined);else if(typeof o==='string'&&!o.startsWith('/'))check(o);return connect.apply(this,args);};
for(const mod of [http,https])for(const name of ['request','get']){const original=mod[name];mod[name]=function(...args){const o=args[0];if(typeof o==='string'||o instanceof URL)check(new URL(o).hostname);else if(o)check(o.hostname||o.host);return original.apply(this,args);};}
const lookup=dns.lookup;dns.lookup=function(host,...args){check(host);return lookup.call(this,host,...args);};
if(globalThis.fetch){const original=globalThis.fetch;globalThis.fetch=function(input,...args){check(new URL(typeof input==='string'||input instanceof URL?input:input.url).hostname);return original.call(this,input,...args);};}
