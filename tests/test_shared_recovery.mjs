import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
function harness(){
  const sent=[],elements=new Map();let socket;const scans=[];
  const element=()=>({value:'x',hidden:false,disabled:false,options:[{},{}],classList:{toggle(){},add(){},remove(){}},removeAttribute(){},getContext:()=>({})});
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
  const state={stream:{},mode:'attendance',ready:true,activeAttendance:{id:7},movementMode:'check_in'};
  class Socket{static OPEN=1;constructor(){socket=this;this.readyState=1;this.bufferedAmount=0;}send(message){sent.push(JSON.parse(message));}close(){this.readyState=3;this.onclose?.();}}
  const context=vm.createContext({document:{createElement:element,addEventListener(){}},window:{addEventListener(){}},navigator:{onLine:true,mediaDevices:{getUserMedia(){}}},location:{protocol:'https:',host:'localhost'},WebSocket:Socket,Blob,Date,Math,performance:{now:()=>100},URL:{revokeObjectURL(){}},setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},$:get,state,token:'test-token',refreshClientToken:async()=>{},connectionStatus(){},updateControls(){},stopScan(){state.scanning=false;state.scanSessionId=null;},scan:async sid=>{state.scanning=true;state.scanSessionId=sid;scans.push(sid);},refreshAttendance:async()=>{},liveTracker:{render(){}},result(){},toast(){},transientError:()=>false,expiredSession(){}});
  vm.runInContext(fs.readFileSync('static/shared-camera.js','utf8'),context);
  const room=context.window.sharedRoom,originalPublish=room.publish.bind(room);room.publish=()=>{};
  const deliver=message=>socket.onmessage({data:JSON.stringify({type:'state',action:'check_in',axis:'x',direction:'positive',epoch:1,attendance_revision:0,...message})});
  return {room,state,sent,scans,deliver,context,get,originalPublish,getSocket:()=>socket,flush:()=>new Promise(resolve=>setImmediate(resolve))};
}
test('lost source reconnects with its key and starts the fresh scanner',async()=>{
  const h=harness();await h.flush();h.deliver({source_id:'old',client_id:'old',source_key:'secret',running:true,armed:true,session_id:7,ready:true});
  h.getSocket().close();assert.equal(h.state.scanning,false);assert.equal(h.room.restoreSession,7);
  await h.room.connect();h.deliver({source_id:null,client_id:'new',recovering:true});
  assert.equal(h.sent.at(-1).type,'claim');assert.equal(h.sent.at(-1).resume_key,'secret');
  h.deliver({source_id:'new',client_id:'new',source_key:'fresh',running:false,armed:true,resumed:true,session_id:7,ready:false});
  h.deliver({source_id:'new',client_id:'new',source_key:'fresh',running:true,armed:true,resumed:true,session_id:7,epoch:4,ready:true});
  assert.deepEqual(h.scans,[7,7]);assert.equal(h.sent.filter(message=>message.type==='start').length,0);
});
test('a manual pause made while disconnected survives source recovery',async()=>{
  const h=harness();await h.flush();h.deliver({source_id:'old',client_id:'old',source_key:'secret',running:true,armed:true,session_id:7,ready:true});
  h.getSocket().close();await h.room.connect();h.deliver({source_id:null,client_id:'new',recovering:true});
  h.deliver({source_id:'new',client_id:'new',source_key:'fresh',running:false,armed:false,resumed:true,session_id:7,ready:true});
  assert.equal(h.state.scanning,false);assert.equal(h.sent.filter(message=>message.type==='start').length,0);
});
test('after server restart the browser requests only its previous attendance day',async()=>{
  const h=harness();await h.flush();h.deliver({source_id:'old',client_id:'old',source_key:'secret',running:true,armed:true,session_id:7,ready:true});
  h.getSocket().close();await h.room.connect();h.deliver({source_id:null,client_id:'new',recovering:false});
  h.deliver({source_id:'new',client_id:'new',source_key:'fresh',running:false,armed:false,resumed:false,ready:true});
  assert.deepEqual(h.sent.at(-1),{type:'start',session_id:7});
  h.room.release();h.getSocket().close();assert.equal(h.room.shareIntent,false);
});

test('shared preview reduces traffic on a slow connection and recovers after stale measurements',async()=>{
  const h=harness();await h.flush();h.room.snapshot={viewers:1};
  assert.equal(h.room.previewOptions().interval,500);
  assert.equal(h.room.previewOptions().width,480);
  h.context.window.IdentiqScan={last:()=>({request_ms:1800,recorded_at:Date.now()})};
  assert.equal(h.room.previewOptions().interval,1000);
  assert.equal(h.room.previewOptions().width,360);
  h.context.window.IdentiqScan={last:()=>({request_ms:1800,recorded_at:Date.now()-20000})};
  assert.equal(h.room.previewOptions().limited,false);
  h.context.navigator.connection={saveData:true};
  assert.equal(h.room.previewOptions().limited,true);
});

test('a backed-up preview socket skips capture instead of queuing more video',async()=>{
  const h=harness();await h.flush();
  h.room.snapshot={source_id:'source',client_id:'source',viewers:1};
  h.room.connected=true;h.room.lastUpload=-2000;h.getSocket().bufferedAmount=65000;
  Object.defineProperty(h.get('video'),'videoWidth',{get(){throw new Error('A congested preview must not capture another frame.');}});
  h.originalPublish();
  assert.equal(h.room.encoding,false);
});
