import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const people=[
  {id:1,name:'Alice',student_id:'ADM-01',grade:'Grade 7',section:'A',person_type:'student',samples:0},
  {id:2,name:'Bob',student_id:'ADM-02',grade:'Grade 7',section:'B',person_type:'student',samples:0},
  {id:3,name:'Cara',student_id:'ADM-03',grade:'Grade 8',section:'A',person_type:'student',samples:5},
  {id:4,name:'Sam',employee_code:'EMP-01',person_type:'staff',samples:0},
];
function harness(){
  const elements=new Map();
  class Select{
    constructor(){this.options=[];this.selected='';}
    get value(){return this.options.some(option=>String(option.value)===this.selected)?this.selected:'';}
    set value(value){this.selected=String(value);}
    replaceChildren(...options){this.options=options;}
    add(option){this.options.push(option);}
  }
  const get=id=>{if(!elements.has(id))elements.set(id,id==='enroll-search'?{value:''}:id==='enroll-filter-summary'?{textContent:''}:new Select());return elements.get(id);};
  const context=vm.createContext({window:{},document:{getElementById:get},Option:class{constructor(text,value){Object.assign(this,{text,value});}}});
  vm.runInContext(fs.readFileSync('static/enrollment-filters.js','utf8'),context);
  const Filters=context.window.EnrollmentFilters;const changes=[];
  const filter=new Filters({onChange:()=>changes.push(get('enroll-person').value)});filter.refresh(people);
  return {Filters,filter,get,changes,ids:select=>get(select).options.map(option=>String(option.value)).filter(Boolean)};
}
test('enrollment waits for a grade or search rather than loading every student',()=>{
  const h=harness();assert.deepEqual(h.ids('enroll-person'),[]);assert.deepEqual(h.ids('iphone-target'),[]);
  h.get('enroll-grade').value='grade:grade 7';h.get('enroll-grade').onchange();
  assert.deepEqual(h.ids('enroll-person'),['1','2']);assert.deepEqual(h.ids('enroll-section'),['section:a','section:b']);
});
test('section changes clear a hidden selection before another capture',()=>{
  const h=harness();h.filter.select(people[0]);assert.equal(h.get('enroll-person').value,'1');
  h.get('enroll-section').value='section:b';h.get('enroll-section').onchange();
  assert.deepEqual(h.ids('enroll-person'),['2']);assert.equal(h.get('enroll-person').value,'');assert.equal(h.changes.at(-1),'');
});
test('grade changes reset section choices and keep enrolled people only in phone import',()=>{
  const h=harness();h.filter.select(people[1]);h.get('enroll-grade').value='grade:grade 8';h.get('enroll-grade').onchange();
  assert.equal(h.get('enroll-section').value,'');assert.deepEqual(h.ids('enroll-section'),['section:a']);
  assert.deepEqual(h.ids('enroll-person'),[]);assert.deepEqual(h.ids('iphone-target'),['3']);
});
test('admission search works without a grade and directory shortcuts select the right staff record',()=>{
  const h=harness();h.get('enroll-search').value='adm-03';h.get('enroll-search').oninput();
  assert.deepEqual(h.ids('iphone-target'),['3']);
  h.filter.select(people[3]);assert.equal(h.get('enroll-grade').value,'ungrouped');assert.equal(h.get('enroll-person').value,'4');assert.equal(h.get('enroll-search').value,'');
});
test('legacy records without grades remain enrollable and refresh removes a newly enrolled face',()=>{
  const h=harness();h.filter.refresh([{id:9,name:'Legacy',samples:0}]);assert.equal(h.get('enroll-grade').value,'ungrouped');assert.deepEqual(h.ids('enroll-person'),['9']);
  h.get('enroll-person').value='9';h.filter.refresh([{id:9,name:'Legacy',samples:5}]);assert.deepEqual(h.ids('enroll-person'),[]);assert.equal(h.get('enroll-person').value,'');
});
