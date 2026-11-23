import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function scheduling(){
  const context=vm.createContext({window:{},Date});
  vm.runInContext(fs.readFileSync('static/scan-schedule.js','utf8'),context);
  return context.window.IdentiqScan;
}

test('camera follows server confirmation pacing and scans empty scenes promptly',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:3,retry_after_ms:351},true),351);
  assert.equal(scan.delay({face_count:0},true),100);
  assert.equal(scan.delay({face_count:3},true),250);
  assert.equal(scan.delay({retry_after_ms:351},false),100);
  assert.equal(scan.delay({face_count:3},false),80);
  assert.equal(scan.delay({box:[.1,.1,.3,.3]},false),80);
});

test('walk-by search counts time already spent on a frame',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:0},false,60),40);
  assert.equal(scan.delay({face_count:2},false,90),25);
  assert.equal(scan.delay({face_count:0},true,90),25);
  assert.equal(scan.delay({face_count:1,faces:[{status:'unknown'}]},true,70),30);
});

test('attendance pacing includes inference time while retaining a bounded delay',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:200},true),50);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:800},true),40);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:NaN},true),250);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:-100},true),250);
  assert.equal(scan.delay({face_count:0,retry_after_ms:600,processing_ms:800},true),100);
});

test('invalid or extreme hints cannot create a tight loop or an unbounded wait',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:1,retry_after_ms:NaN},true),250);
  assert.equal(scan.delay({face_count:1,retry_after_ms:Infinity},true),250);
  assert.equal(scan.delay({retry_after_ms:0},true),100);
  assert.equal(scan.delay({face_count:1,retry_after_ms:900000},true),1000);
});

test('scan diagnostics distinguish request time from processing without retaining identities',()=>{
  const scan=scheduling();
  assert.equal(scan.last(),null);
  scan.record({processing_ms:123.4,face_count:3,image:'private photo',faces:[{person:{id:7,name:'Private Student'}}]},180.2);
  const sample=scan.last();
  assert.equal(sample.processing_ms,123.4);
  assert.equal(sample.request_ms,180);
  assert.equal(sample.face_count,3);
  assert.ok(Number.isFinite(sample.recorded_at));
  assert.doesNotMatch(JSON.stringify(sample),/Private|photo|name|image|person/);
  sample.face_count=999;
  assert.equal(scan.last().face_count,3);
});

test('older server responses still provide usable request diagnostics',()=>{
  const scan=scheduling();
  scan.record({box:[.1,.1,.3,.3]},75);
  assert.equal(scan.last().processing_ms,null);
  assert.equal(scan.last().request_ms,75);
  assert.equal(scan.last().face_count,1);
});

test('each provisional person has four queued frames and shared scenes are sent once',()=>{
  const buffer=scheduling().faceBuffer();
  const person=id=>({status:'matched',person:{id},attendance:{status:'confirming'}});
  buffer.push({image:'first',capturedAt:0});
  assert.equal(buffer.accept([person(1),person(2)],0),true);
  for(let i=1;i<=5;i++)buffer.push({image:`frame${i}`,capturedAt:i*300});
  assert.equal(buffer.take(1500).image,'frame2');
  assert.equal(buffer.take(1500).image,'frame3');
  assert.equal(buffer.take(1500).image,'frame4');
  assert.equal(buffer.take(1500).image,'frame5');
  assert.equal(buffer.take(1500),null);
  buffer.push({image:'next',capturedAt:1800});
  // A missing or ambiguous person cannot keep a provisional queue alive.
  buffer.accept([{...person(1),status:'ambiguous'},person(2)],1800);
  assert.equal(buffer.take(1800).image,'next');
  assert.equal(buffer.take(1800),null);
  buffer.push({image:'expired',capturedAt:2100});
  assert.equal(buffer.has(3601),false);
  assert.equal(buffer.take(3601),null);
  buffer.clear();
  assert.equal(buffer.accept([],4000),false);
});

test('distant and uncertain faces are retried promptly without slowing confirmation pacing',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:1,faces:[{status:'adjust',code:'too_small'}],retry_after_ms:500},true),100);
  assert.equal(scan.delay({face_count:1,faces:[{status:'unknown'}],retry_after_ms:500},true),100);
  assert.equal(scan.delay({face_count:1,faces:[{status:'matched'}],retry_after_ms:351,processing_ms:0},true),351);
});

test('confirmation pacing subtracts full time since capture and chooses the earliest provisional face',()=>{
  const scan=scheduling();
  const data={face_count:3,processing_ms:50,retry_after_ms:250,faces:[
    {status:'matched',attendance:{status:'present',retry_after_ms:250}},
    {status:'matched',attendance:{status:'confirming',retry_after_ms:350}},
    {status:'matched',attendance:{status:'confirming',retry_after_ms:400}},
  ]};
  assert.equal(scan.delay(data,true,200),160);
  assert.equal(scan.delay(data,true,450),0);
});

test('camera-paced scanning overlaps inference with the next frame and cancels a stalled camera',async()=>{
  const scan=scheduling(), controller=new AbortController();
  let next=0;
  const callbacks=new Map();
  const clock=scan.frameClock({
    requestVideoFrameCallback(fn){callbacks.set(++next,fn);return next;},
    cancelVideoFrameCallback(id){callbacks.delete(id);},
  },controller.signal);
  assert.equal(scan.delay({face_count:1},false,20,true),0);
  assert.equal(scan.delay({face_count:0},true,20,true),0);
  assert.equal(scan.delay({face_count:1,retry_after_ms:191},true,20,true),191);
  clock.captured();
  let fresh=false;
  const ready=clock.ready().then(()=>{fresh=true;});
  await Promise.resolve();
  assert.equal(fresh,false);
  callbacks.get(next)();
  callbacks.delete(next);
  await ready;
  assert.equal(clock.ready(),null);
  clock.captured();
  const stalled=clock.ready();
  controller.abort();
  await stalled;
  assert.equal(callbacks.size,0);
  clock.close();
});

test('large clear matches use compact frames with regular full-detail checks',()=>{
  const size=scheduling().captureSize(), video={videoWidth:1920,videoHeight:1080};
  const face={status:'matched',box:[.2,.2,.3,.4],quality:{brightness:100,detection_score:.98}};
  assert.equal(size.next(0),1920);
  size.observe({faces:[face]},video);
  assert.equal(size.next(200),960);
  size.observe({faces:[{...face,box:[.1,.1,.5,.6]}]},video);
  assert.equal(size.next(300),640);
  assert.equal(size.next(500),1920);
  for(const faces of [[],[face,{...face,status:'unknown'}],[{...face,status:'unknown'}],[{...face,box:[0,0,.1,.1]}],
    [{...face,quality:{brightness:25,detection_score:.99}}]]){
    size.observe({faces},video);
    assert.equal(size.next(600),1920);
  }
});

test('slow empty attendance scans alternate compact searches with full-resolution checks',()=>{
  const size=scheduling().captureSize(), video={videoWidth:1920,videoHeight:1080};
  const empty={faces:[],code:'no_face',processing_ms:100};
  assert.equal(size.next(0),1920);
  size.observe(empty,video,1920);
  assert.equal(size.next(100),960);
  size.observe({...empty,processing_ms:15},video,960);
  assert.equal(size.next(300),960);
  assert.equal(size.next(500),1920);
  size.observe({...empty,faces:[{status:'adjust',code:'too_small'}]},video,960);
  assert.equal(size.next(510),1920);
  size.observe({...empty,processing_ms:150},video,1920,3);
  assert.equal(size.next(530),1920);
});


test('group capture uses the smallest face and restores full detail if anyone needs it',()=>{
  const size=scheduling().captureSize(), video={videoWidth:1920,videoHeight:1080};
  const near={status:'matched',box:[0,0,.5,.6],quality:{brightness:100,detection_score:.98}};
  const far={...near,box:[.5,.2,.25,.3]};
  size.next(0);
  size.observe({faces:[near,far]},video);
  assert.equal(size.next(200),960);
  size.observe({faces:[near,{...far,quality:{brightness:30,detection_score:.99}}]},video);
  assert.equal(size.next(210),1920);
});

test('a distant or blurred walk-by and its provisional match request full-detail captures',()=>{
  const settings=scheduling().captureSize(), video={videoWidth:1920,videoHeight:1080};
  settings.next(0);
  for(const face of [
    {status:'adjust',code:'too_small'}, {status:'adjust',code:'blurry'},
    {status:'matched',box:[0,0,.5,.6],quality:{brightness:90,detection_score:.98},
     attendance:{status:'confirming',confirmations:1}},
  ]){
    settings.observe({faces:[face]},video);
    assert.equal(settings.next(100),1920);
    assert.equal(settings.jpegQuality(),.9);
  }
  settings.observe({faces:[],code:'no_face'},video);
  assert.equal(settings.jpegQuality(),.78);
});
