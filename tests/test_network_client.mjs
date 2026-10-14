import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
function client(fetch){
  const timers=[];
  const context=vm.createContext({window:{},fetch,AbortController,setTimeout:callback=>{timers.push(callback);return timers.length;},clearTimeout(){}});
  vm.runInContext(fs.readFileSync('static/network.js','utf8'),context);
  return {net:context.window.IdentiqNetwork,timers};
}
for(const failure of [{name:'TypeError',message:'Failed to fetch'},{name:'Error',message:'Load failed'},{name:'NetworkError',message:'NetworkError when attempting to fetch resource.'}]){
  test(`normalizes browser connection error ${failure.message} without replaying POST`,async()=>{
    let calls=0;
    const {net}=client(async()=>{calls++;throw failure;});
    await assert.rejects(net.request('/api/enroll',{method:'POST',body:{images:['dummy']}}),error=>error.code==='network_unavailable' && !/failed to fetch|load failed/i.test(error.message));
    assert.equal(calls,1);
  });
}
test('deadline and user cancellation remain distinct',async()=>{
  const h=client((path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject({name:'AbortError'}))));
  const timed=h.net.request('/api/status');h.timers[0]();
  await assert.rejects(timed,error=>error.code==='server_timeout');
  const controller=new AbortController();const cancelled=h.net.request('/api/recognize',{signal:controller.signal});controller.abort();
  await assert.rejects(cancelled,error=>error.code==='cancelled' && !h.net.transient(error));
});
test('unavailable server and incomplete responses recover but bad credentials do not retry',async()=>{
  for(const response of [{status:503},{status:200,ok:true,json:async()=>{throw new SyntaxError();}},{status:200,ok:true,json:async()=>null}]){
    const {net}=client(async()=>response);
    await assert.rejects(net.request('/api/status'),error=>net.transient(error));
  }
  let calls=0;const {net}=client(async()=>{calls++;return {status:401,ok:false,json:async()=>({code:'invalid_login',error:'Wrong password.'})};});
  await assert.rejects(net.request('/api/staff/login',{method:'POST',body:{}}),error=>error.status===401 && !net.transient(error));
  assert.equal(calls,1);
});
test('JSON reads interrupted by a timeout retain the timeout reason',async()=>{
  let options;
  const h=client(async(path,input)=>{options=input;return {status:200,ok:true,json:()=>new Promise((resolve,reject)=>input.signal.addEventListener('abort',()=>reject({name:'AbortError'})))};});
  const read=h.net.request('/api/status');await new Promise(resolve=>setImmediate(resolve));h.timers[0]();
  await assert.rejects(read,error=>error.code==='server_timeout');assert.equal(options.cache,'no-store');
});
test('diagnostic records contain neither submitted credentials nor student identifiers',async()=>{
  let ok=false;const {net}=client(async()=>{if(ok)return {status:200,ok:true,json:async()=>({saved:true})};throw {name:'TypeError',message:'Failed to fetch'};});
  await assert.rejects(net.request('/api/people/123/profile?name=Private',{method:'PATCH',body:{name:'Private Student',password:'private-password'}}));
  const failure=net.lastFailure();assert.equal(failure.request,'/api/people/:id/profile');assert.equal(failure.recovered,false);
  assert.doesNotMatch(JSON.stringify(failure),/123|Private|password|Student/);
  ok=true;await net.request('/connection-check?format=json');assert.equal(net.lastFailure().recovered,false);
  await net.request('/api/people/123/profile');assert.equal(net.lastFailure().recovered,true);
});
