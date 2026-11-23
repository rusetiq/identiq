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

test('attendance refresh replaces changed records and preserves controls for other people',()=>{
  const body={rows:[],dataset:{sessionId:'4'},insertBefore(row,next){
    row.remove();
    this.rows.splice(next ? this.rows.indexOf(next) : this.rows.length,0,row);
  }};
  const controls=new Map();
  const get=id=>{
    if(id==='attendance-roster')return {querySelector:()=>body};
    if(!controls.has(id))controls.set(id,{value:''});
    return controls.get(id);
  };
  let builds=0;
  function rosterRow(session,person){
    builds++;
    return {dataset:{recordId:String(person.id)}, remove(){
      const index=body.rows.indexOf(this);
      if(index!==-1)body.rows.splice(index,1);
    },replaceWith(other){body.rows.splice(body.rows.indexOf(this),1,other);}};
  }
  const context=vm.createContext({$:get,rosterRow,updateControls(){}});
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('function renderRoster('),source.indexOf("$('roster-search').oninput")),context);
  const detail={id:4,name:'Morning',status:'active',counts:{},roster:
    Array.from({length:1000},(_,i)=>({id:i+1,name:String(i),status:'pending'}))};
  context.renderRoster(detail);
  const first=body.rows[0],second=body.rows[1];
  detail.roster[1].status='present';
  context.renderRoster(detail);
  assert.equal(builds,1001);
  assert.equal(body.rows[0],first);
  assert.notEqual(body.rows[1],second);
  get('roster-filter').value='present';
  context.renderRoster(detail);
  assert.deepEqual(body.rows.map(r=>r.dataset.recordId),['2']);
});

test('walk-by summary distinguishes one provisional frame from an existing check-in',async()=>{
  function element(){return {children:[],append(...items){this.children.push(...items);},replaceChildren(){this.children=[];}};}
  const holder=element(), results=[], events=[];
  const context=vm.createContext({$:()=>holder,document:{createElement:element},performance:{now:()=>500},
    walkbyLog:{record:(_,lines)=>events.push([...lines])},state:{movementMode:'check_in'},showCameraNames(){},liveTracker:{highlightGroup(){}},
    result:(...args)=>results.push(args)});
  const source=fs.readFileSync('static/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('async function showGroupResults('),source.indexOf('async function enroll(')),context);
  const streaks=new Map(), scan=faces=>context.showGroupResults({faces},streaks,4);
  await scan([{status:'adjust',code:'too_small'}]);
  await scan([{status:'adjust',code:'blurry'}]);
  const face={status:'matched',person:{name:'Sample',id:1}};
  await scan([{...face,attendance:{status:'confirming',confirmations:1,required_confirmations:3,attendance_status:'present'}}]);
  await scan([]);
  assert.deepEqual(events.slice(0,4),[['Face too far away'],['Blurry face'],['Sample · Provisional match · 1 of 3 confirmations · already marked present in this session'],[]]);
  assert.deepEqual(results.at(-1),['Sample','Last pass had 1 of 3 confirming frames. Already marked present in this session.','warning']);
  await scan([{...face,attendance:{status:'present',campus_state:'on_campus',newly_moved:false}}]);
  await scan([]);
  assert.deepEqual(results.at(-1),['Sample','Already checked in for this session.','matched']);
});

test('walk-by log separates visits and ignores repeated frames and idle time', () => {
  const rows = [];
  const list = {
    children: rows, prepend: row => rows.unshift(row), replaceChildren: () => { rows.length = 0; },
    get lastElementChild() { return {remove: () => rows.pop()}; },
  };
  const context = vm.createContext({$: () => list, document: {createElement: () => ({})}, performance: {timeOrigin: 1000}});
  const source = fs.readFileSync('static/app.js', 'utf8');
  vm.runInContext(source.slice(source.indexOf('function createWalkbyLog('), source.indexOf('async function scan(')), context);
  const log = vm.runInContext('walkbyLog', context);
  log.start(1);
  for (const [index, text] of ['', 'Blurry face', 'Blurry face', '', '', 'Check-in saved'].entries())
    log.record(index, text ? [text] : []);
  assert.deepEqual(rows.map(row => row.textContent.slice(row.textContent.indexOf('Pass'))),
    ['Pass 2 · Check-in saved', 'Pass 1 · No face in frame', 'Pass 1 · Blurry face']);
  for (let i = 0; i < 120; i++) log.record(i, [String(i)]);
  assert.equal(rows.length, 100);
  log.start(2);
  assert.equal(rows.length, 0);
});
