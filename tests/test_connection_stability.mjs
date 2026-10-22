import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function harness(request){
  const elements=new Map();let time=0;
  const create=()=>({textContent:'',disabled:false,children:[],listeners:{},
    append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},
    addEventListener(name,callback){this.listeners[name]=callback;},parentElement:{insertBefore(){}},
    showModal(){},close(){this.listeners.close?.();}});
  const get=id=>{if(!elements.has(id))elements.set(id,create());return elements.get(id);};
  const context=vm.createContext({window:{isSecureContext:true,IdentiqNetwork:{request,lastFailure:()=>null}},
    document:{getElementById:get,createElement:create,querySelector:()=>({content:'build'}),querySelectorAll:()=>[]},
    location:{origin:'https://10.10.40.40:8443',host:'10.10.40.40:8443',pathname:'/'},
    navigator:{onLine:true,mediaDevices:{getUserMedia(){}}},AbortController,Date,
    performance:{now:()=>time+=100},setTimeout:callback=>{queueMicrotask(callback);return 1;}});
  vm.runInContext(fs.readFileSync('static/connection-check.js','utf8'),context);
  const rows=()=>get('connection-check-results').children.map(item=>item.textContent);
  return {get,rows,run:()=>get('run-connection-check').onclick()};
}

const reachable={running_build:'build',installed_build:'build',needs_restart:false};

test('several probes detect intermittent access even when the first check succeeds',async()=>{
  let calls=0;
  const h=harness(async(path,options)=>{
    calls++;
    assert.ok(options.signal);
    assert.equal(options.timeoutMs,calls===1?6000:2500);
    if(calls===2 || calls===4)throw {code:'network_unavailable'};
    return reachable;
  });
  await h.run();
  assert.equal(calls,5);
  assert.match(h.rows().join(' '),/3 of 5 requests succeeded/);
  assert.match(h.get('connection-check-summary').textContent,/intermittent or slow/);
  assert.match(h.get('connection-check-help').textContent,/10\.10\.40\.40:8443/);
  assert.equal(h.get('run-connection-check').disabled,false);
});

test('a successful check reports the sample without guaranteeing continuous coverage',async()=>{
  let calls=0;const h=harness(async()=>{calls++;return reachable;});
  await h.run();
  assert.equal(calls,5);
  assert.match(h.get('connection-check-summary').textContent,/All five connection checks succeeded/);
  assert.match(h.rows().join(' '),/Slowest successful check/);
});

test('closing diagnostics cancels a pending request instead of continuing background probes',async()=>{
  let calls=0;
  const h=harness(async(path,{signal})=>{
    calls++;
    return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject({code:'cancelled'})));
  });
  const check=h.run();h.get('connection-check-dialog').close();await check;
  assert.equal(calls,1);
  assert.equal(h.get('run-connection-check').disabled,false);
});
