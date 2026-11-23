import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('recognition captures available camera detail without upscaling',()=>{
  const video={videoWidth:1920,videoHeight:1080,readyState:4};
  const context=vm.createContext({state:{stream:{}},$:()=>video,captureCanvas:{}});
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('function frame('),source.indexOf('async function startCamera(')),context);
  const canvas={width:0,height:0,getContext:()=>({drawImage(){}}),toDataURL:(type,quality)=>`${type}:${quality}`};
  assert.equal(context.frame(canvas,1920,.78),'image/jpeg:0.78');
  assert.deepEqual([canvas.width,canvas.height],[1920,1080]);
  video.videoWidth=640;video.videoHeight=480;
  context.frame(canvas,1920,.78);
  assert.deepEqual([canvas.width,canvas.height],[640,480]);
  video.videoWidth=1080;video.videoHeight=1920;
  context.frame(canvas,1920,.78);
  assert.deepEqual([canvas.width,canvas.height],[1080,1920]);
});

test('walking scan sends up to three buffered frames together and renders them in order',async()=>{
  let time=0,nextTimer=0,active=0;
  const timers=new Map(),requests=[],captures=[],rendered=[],elements=new Map();
  const advance=ms=>{
    const target=time+ms;
    while(true){
      const due=[...timers.values()].filter(timer=>timer.at<=target).sort((a,b)=>a.at-b.at)[0];
      if(!due)break;
      time=due.at;due.at+=due.interval;due.callback();
    }
    time=target;
  };
  const state={scanning:false,stream:{},generation:0,movementMode:'check_in',attendanceCaptureIntervalMs:300};
  const context=vm.createContext({window:{},state,AbortController,
    crypto:{randomUUID:()=> 'scanner'},performance:{now:()=>time},captureCanvas:{},
    $:id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:''});return elements.get(id);},
    connectionIssues:new Set(),maintainCameraAwake(){},updateControls(){},
    frame(canvas,maxSide,quality){captures.push({time,maxSide,quality});return `frame at ${time}`;},
    setInterval(callback,interval){const id=++nextTimer;timers.set(id,{callback,interval,at:time+interval});return id;},
    clearInterval:id=>timers.delete(id),sleep:async ms=>advance(ms),
    liveTracker:{accept(data,image){rendered.push(image);}},walkbyLog:{start(){}},showGroupResults:async()=>{},
    apiFrame:async(path,payload)=>{
      assert.equal(active++,0,'Recognition requests must not overlap');
      requests.push({time,images:payload.images||[payload.image],ages:payload.capture_ages_ms||[payload.capture_age_ms]});
      advance(1000);active--;
      if(requests.length===3)state.scanning=false;
      const data = {face_count:2,ready_count:2,processing_ms:900,retry_after_ms:250,threshold:.65,
        faces:[{status:'matched',person:{id:1},attendance:{status:'confirming'}}]};
      return payload.images ? {scans:payload.images.map(()=>data)} : data;
    },
  });
  vm.runInContext(fs.readFileSync('static/scan-schedule.js','utf8'),context);
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('async function scan('),source.indexOf('async function showGroupResults(')),context);
  await context.scan(1);
  assert.equal(requests.length,3);
  assert.ok(captures.length>=4,'Capture continues during the first request');
  for(const capture of captures){assert.equal(capture.maxSide,1920);assert.equal(capture.quality,capture.time < 1000 ? .78 : .9);}
  assert.deepEqual(requests[0],{time:0,images:['frame at 0'],ages:[0]});
  assert.equal(requests[1].time,1000);
  assert.equal(JSON.stringify(requests[1].images),JSON.stringify(['frame at 300','frame at 600','frame at 900']));
  assert.equal(JSON.stringify(requests[1].ages),JSON.stringify([700,400,100]));
  assert.deepEqual(rendered,['frame at 0','frame at 300','frame at 600','frame at 900']);
  assert.equal(timers.size,0,'Stopping scanning must clear its timers');
});

test('an empty result preserves frames captured while its inference ran',async()=>{
  let time=0,nextTimer=0;const timers=new Map(),requests=[],elements=new Map();
  const advance=ms=>{
    const target=time+ms;
    while(true){
      const due=[...timers.values()].filter(timer=>timer.at<=target).sort((a,b)=>a.at-b.at)[0];
      if(!due)break;
      time=due.at;due.at+=due.interval;due.callback();
    }
    time=target;
  };
  const state={scanning:false,stream:{},generation:0,movementMode:'check_in'};
  const context=vm.createContext({window:{},state,AbortController,crypto:{randomUUID:()=> 'scanner'},
    performance:{now:()=>time},captureCanvas:{},
    $:id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:''});return elements.get(id);},
    connectionIssues:new Set(),maintainCameraAwake(){},updateControls(){},
    frame(){return `frame at ${time}`;},
    setInterval(callback,interval){const id=++nextTimer;timers.set(id,{callback,interval,at:time+interval});return id;},
    clearInterval:id=>timers.delete(id),sleep:async ms=>advance(ms),
    liveTracker:{accept(){}},walkbyLog:{start(){}},showGroupResults:async()=>{},
    apiFrame:async(path,payload)=>{
      requests.push({time,image:payload.images?.[0]||payload.image,age:payload.capture_ages_ms?.[0]??payload.capture_age_ms});
      advance(1000);
      if(requests.length===2)state.scanning=false;
      return {face_count:0,ready_count:0,faces:[],processing_ms:900,retry_after_ms:600,threshold:.65};
    },
  });
  vm.runInContext(fs.readFileSync('static/scan-schedule.js','utf8'),context);
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('async function scan('),source.indexOf('async function showGroupResults(')),context);
  await context.scan(1);
  assert.equal(requests.length,2);
  assert.equal(requests[1].image,'frame at 205');
  assert.equal(requests[1].age,795);
  assert.equal(timers.size,0);
});

test('offline recovery discards expired and legacy frames instead of replaying them as fresh',async()=>{
  const removed=[],sent=[];
  const items=[
    {id:1,queued_at:1000,payload:{image:'old',capture_age_ms:100}},
    {id:2,queued_at:2400,payload:{image:'legacy'}},
    {id:3,queued_at:2400,payload:{image:'recent',capture_age_ms:200}},
  ];
  class Clock extends Date {static now(){return 2500;}}
  const context=vm.createContext({Date:Clock,state:{scanning:false},navigator:{onLine:true},offlineSyncing:false,
    window:{IdentiqOfflineAttendance:{entries:async()=>items,remove:async ids=>removed.push(...ids)}},
    apiFrame:async(path,payload)=>sent.push(payload),refreshOfflineQueue:async()=>{},refreshAttendance:async()=>{},toast(){},transientError:()=>false});
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('async function syncOfflineAttendance('),source.indexOf('let refreshingToken')),context);
  await context.syncOfflineAttendance();
  assert.deepEqual(removed,[1,2,3]);
  assert.equal(sent.length,1);
  assert.equal(sent[0].image,'recent');
  assert.equal(sent[0].capture_age_ms,300);
});

test('attendance JPEG blobs stay binary from capture through multipart upload', async()=>{
  const video={videoWidth:1920,videoHeight:1080,readyState:4};
  const jpeg=new Blob([Uint8Array.of(255,216,255,217)],{type:'image/jpeg'});
  const canvas={
    width:0,height:0,getContext:()=>({drawImage(){}}),
    toDataURL(){throw new Error('Attendance should skip base64');},
    toBlob(done,type,quality){
      assert.equal(type,'image/jpeg');
      assert.equal(quality,.78);
      queueMicrotask(()=>done(jpeg));
    },
  };
  const context=vm.createContext({state:{stream:{}},$:()=>video,captureCanvas:canvas,Blob,FormData});
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('function frame('),source.indexOf('async function startCamera(')),context);
  vm.runInContext(source.slice(source.indexOf('function compactFrameForm('),source.indexOf('function apiFrame(')),context);
  const captured=await context.frame(canvas,1920,.78,true);
  assert.equal(captured,jpeg);
  const form=context.compactFrameForm({images:[captured,captured],capture_ages_ms:[300,0]});
  assert.equal(form.getAll('image').length,2);
  for(const image of form.getAll('image')){
    assert.equal(image.type,'image/jpeg');
    assert.deepEqual(await image.arrayBuffer(),await jpeg.arrayBuffer());
  }
  assert.deepEqual(JSON.parse(form.get('payload')),{capture_ages_ms:[300,0]});
});
