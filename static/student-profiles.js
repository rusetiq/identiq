"use strict";
let editingStudent=null;
function profileFields(){
  const student=$('profile-type').value==='student';
  $('profile-student-fields').hidden=!student;$('profile-staff-fields').hidden=student;
  for(const id of ['student-id','profile-grade','profile-section','profile-year'])$(id).required=student;
  $('profile-employee').required=!student;
}
function openStudentProfile(person=null){
  editingStudent=person?.id || null;$('student-profile-heading').textContent=person?'Edit school record':'Add school record';
  $('student-profile-name').textContent=person?.name || 'Register details before enrolling a face.';
  $('profile-name').value=person?.name || '';$('profile-type').value=person?.person_type || 'student';
  for(const [id,field] of [['student-id','student_id'],['profile-grade','grade'],['profile-section','section'],['profile-year','academic_year'],['profile-employee','employee_code']])$(id).value=person?.[field] || '';
  profileFields();$('student-profile-error').hidden=true;$('student-profile-dialog').showModal();$('profile-name').focus();
}
$('profile-type').onchange=profileFields;
$('cancel-student-profile').onclick=()=>$('student-profile-dialog').close();
$('student-profile-form').onsubmit=async event=>{
  event.preventDefault();$('save-student-profile').disabled=true;$('cancel-student-profile').disabled=true;
  const data={name:$('profile-name').value.trim(),person_type:$('profile-type').value};
  for(const [id,field] of [['student-id','admission_number'],['profile-grade','grade'],['profile-section','section'],['profile-year','academic_year'],['profile-employee','employee_code']])data[field]=$(id).value.trim();
  try{const saved=await api(editingStudent?`/api/people/${editingStudent}/profile`:'/api/people',editingStudent?'PATCH':'POST',data);
    await refreshPeople();enrollmentFilters.select(saved.person);$('student-profile-dialog').close();toast('School record saved. Select Enroll when ready to capture their face.');}
  catch(error){$('student-profile-error').hidden=false;$('student-profile-error').textContent=error.message;}
  finally{$('save-student-profile').disabled=false;$('cancel-student-profile').disabled=false;}
};
