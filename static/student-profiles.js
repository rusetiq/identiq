let editingStudent=null;
function openStudentProfile(person){
  editingStudent=person.id;$('student-profile-name').textContent=person.name;
  $('student-id').value=person.student_id || '';$('student-class').value=person.class_name || '';
  $('student-profile-error').hidden=true;$('student-profile-dialog').showModal();
}
$('cancel-student-profile').onclick=()=>$('student-profile-dialog').close();
$('student-profile-form').onsubmit=async event=>{
  event.preventDefault();$('save-student-profile').disabled=true;$('cancel-student-profile').disabled=true;
  try{await api(`/api/people/${editingStudent}/profile`,'PATCH',{student_id:$('student-id').value.trim(),class_name:$('student-class').value.trim()});await refreshPeople();$('student-profile-dialog').close();toast('Student details saved.');}
  catch(error){$('student-profile-error').hidden=false;$('student-profile-error').textContent=error.message;}
  finally{$('save-student-profile').disabled=false;$('cancel-student-profile').disabled=false;}
};
