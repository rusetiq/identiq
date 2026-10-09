import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
function harness(){
  const elements=new Map(),timers=[];
  const element=()=>({classList:{toggle(){}},textContent:'',hidden:false});
  let now=100;
  const context=vm.createContext({document:{getElementById:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);}},performance:{now:()=>now},setTimeout:fn=>timers.push(fn)});
  vm.runInContext(fs.readFileSync('static/live-face.js','utf8')+'\nthis.Tracker=FaceTracker;',context);
  const canvas={dataset:{},getContext:()=>({clearRect(){}})},video={videoWidth:0};
  const create=options=>new context.Tracker({canvas,video,onUpdate(){},capture:()=> 'frame',...options});
  return {create,timers,tick:async()=>{now+=500;timers.shift()?.();await new Promise(resolve=>setImmediate(resolve));},setTime:t=>now=t};
}
test('paused preview makes no camera request, then resumes',async()=>{
  const h=harness();let paused=true,requests=0;
  const tracker=h.create({pause:()=>paused,request:async()=>{requests++;return {status:'ready',face_count:1};}});
  tracker.start();await h.tick();assert.equal(requests,0);
  paused=false;await h.tick();assert.equal(requests,1);assert.equal(tracker.readyStreak,1);tracker.stop();
});
test('old preview responses cannot overwrite a newer recognition frame',()=>{
  const h=harness(),tracker=h.create({request:async()=>({})});tracker.running=true;
  tracker.accept({status:'ready',face_count:2},'new',100);
  tracker.accept({status:'adjust',face_count:0},'old',90);
  assert.equal(tracker.analysis.image,'new');
  h.setTime(200);tracker.invalidate();tracker.accept({status:'ready',face_count:1},'old-mode',150);
  assert.equal(tracker.analysis,null);tracker.stop();
});
test('turning camera off discards pending preview results',async()=>{
  const h=harness();let resolve;
  const tracker=h.create({request:()=>new Promise(r=>resolve=r)});
  tracker.start();tracker.stop();resolve({status:'ready',face_count:1});await new Promise(r=>setImmediate(r));
  assert.equal(tracker.analysis,null);assert.equal(tracker.running,false);assert.equal(tracker.canvas.dataset.faceCount,'0');
});
