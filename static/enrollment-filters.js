'use strict';
window.EnrollmentFilters=(()=>{
  const key=value=>String(value || '').trim().toLocaleLowerCase();
  const student=person=>person.person_type!=='staff';
  const sort=(a,b)=>a.localeCompare(b,undefined,{numeric:true,sensitivity:'base'});
  function inGrade(person,grade){
    if(!grade || grade==='all')return true;
    if(grade==='ungrouped')return !student(person) || !key(person.grade);
    return student(person) && 'grade:'+key(person.grade)===grade;
  }
  function matching(people,{grade='',section='',query=''}={}){
    query=key(query);
    if(!grade && !query)return [];
    return people.filter(person=>inGrade(person,grade) && (!section || student(person) && 'section:'+key(person.section)===section) && (!query || key([person.name,person.student_id,person.employee_code].join(' ')).includes(query)));
  }
  function choices(people,field){
    const values=new Map();
    for(const person of people)if(student(person)){
      const value=String(person[field] || '').trim();if(value || field==='section')values.set(key(value),value || 'Not assigned');
    }
    return [...values.entries()].sort((a,b)=>sort(a[1],b[1]));
  }
  class Filters{
    constructor({onChange}){
      this.onChange=onChange;this.people=[];
      const element=id=>document.getElementById(id);this.grade=element('enroll-grade');this.section=element('enroll-section');this.search=element('enroll-search');
      this.target=element('enroll-person');this.phone=element('iphone-target');this.summary=element('enroll-filter-summary');
      this.grade.onchange=()=>{this.section.value='';this.render();};this.section.onchange=()=>this.render();this.search.oninput=()=>this.render();
    }
    refresh(people){this.people=people;this.render();}
    select(person){
      this.search.value='';this.grade.value=student(person) && key(person.grade)?'grade:'+key(person.grade):'ungrouped';
      this.render();this.section.value=student(person)?'section:'+key(person.section):'';this.render();
      if(!person.compatible_samples)this.target.value=String(person.id);
      this.phone.value=String(person.id);this.onChange();
    }
    render(){
      const grade=this.grade.value,section=this.section.value,target=this.target.value,phone=this.phone.value;
      const grades=choices(this.people,'grade');
      this.grade.replaceChildren(new Option('Select a grade',''),new Option('All grades and staff','all'));
      for(const [value,label] of grades)this.grade.add(new Option(label,'grade:'+value));
      if(this.people.some(person=>!student(person) || !key(person.grade)))this.grade.add(new Option('Staff / no grade assigned','ungrouped'));
      this.grade.value=grade;
      if(!grades.length && this.people.length && !this.grade.value)this.grade.value='ungrouped';
      const group=this.people.filter(person=>inGrade(person,this.grade.value));
      this.section.replaceChildren(new Option('All sections',''));
      for(const [value,label] of choices(group,'section'))this.section.add(new Option(label,'section:'+value));
      this.section.value=section;if(!this.section.value)this.section.value='';
      const visible=matching(this.people,{grade:this.grade.value,section:this.section.value,query:this.search.value});
      const pending=visible.filter(person=>!person.compatible_samples);
      this.target.replaceChildren(new Option(!this.grade.value && !key(this.search.value)?'Select a grade above':pending.length?'Select a registered person':'No people awaiting enrollment',''));
      this.phone.replaceChildren(new Option(visible.length?'Select a school record':'No matching school records',''));
      for(const person of visible){
        const label=`${person.name} · ${person.student_id || person.employee_code || 'Record '+person.id}`;
        this.phone.add(new Option(label,person.id));if(!person.compatible_samples)this.target.add(new Option(label,person.id));
      }
      this.target.value=target;this.phone.value=phone;
      this.summary.textContent=!this.grade.value && !key(this.search.value)?'Select a grade and section, or search by name or admission number.':`${visible.length} records match · ${pending.length} awaiting face enrollment`;
      this.onChange();
    }
  }
  return Object.assign(Filters,{matching,choices});
})();
