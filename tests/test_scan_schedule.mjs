import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function scheduling(){
  const context=vm.createContext({window:{},Date});
  vm.runInContext(fs.readFileSync('static/scan-schedule.js','utf8'),context);
  return context.window.IdentiqScan;
}

test('camera follows server confirmation pacing and slows empty scenes',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:3,retry_after_ms:351},true),351);
  assert.equal(scan.delay({face_count:0},true),600);
  assert.equal(scan.delay({face_count:3},true),250);
  assert.equal(scan.delay({retry_after_ms:351},false),650);
  assert.equal(scan.delay({face_count:3},false),150);
  assert.equal(scan.delay({box:[.1,.1,.3,.3]},false),150);
});

test('attendance pacing includes inference time while retaining a bounded delay',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:200},true),50);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:800},true),40);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:NaN},true),250);
  assert.equal(scan.delay({face_count:3,retry_after_ms:250,processing_ms:-100},true),250);
  assert.equal(scan.delay({face_count:0,retry_after_ms:600,processing_ms:800},true),600);
});

test('invalid or extreme hints cannot create a tight loop or an unbounded wait',()=>{
  const scan=scheduling();
  assert.equal(scan.delay({face_count:1,retry_after_ms:NaN},true),250);
  assert.equal(scan.delay({face_count:1,retry_after_ms:Infinity},true),250);
  assert.equal(scan.delay({retry_after_ms:0},true),40);
  assert.equal(scan.delay({retry_after_ms:900000},true),1000);
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
