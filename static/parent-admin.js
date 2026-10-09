'use strict';
const guardianElement=id=>document.getElementById(id);
let guardianBusy=false;
async function guardianApi(path,method='GET',body){
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Local-Token':document.querySelector('meta[name="local-token"]').content},...(body ? {body:JSON.stringify(body)} : {})});
  const data=await response.json();if(!response.ok) throw new Error(data.error || 'Request failed.');return data;
}
function guardianError(error){guardianElement('parent-admin-error').textContent=error.message;guardianElement('parent-admin-error').hidden=false;}
function setGuardianBusy(busy){guardianBusy=busy;guardianElement('create-parent-link').disabled=busy;guardianElement('guardian-name').disabled=busy;guardianElement('guardian-student').disabled=busy;document.querySelectorAll('#parent-link-list button').forEach(button=>button.disabled=busy);}
document.addEventListener('people-refreshed',event=>{
  const select=guardianElement('guardian-student'),selected=select.value;select.replaceChildren(new Option('Choose a student',''));
  event.detail.forEach(person=>select.add(new Option(`${person.name} · ID ${person.id}`,person.id)));
  if(event.detail.some(person=>String(person.id)===selected)) select.value=selected;
  loadGuardianLinks().catch(guardianError);
});
async function loadGuardianLinks(){
  const parents=(await guardianApi('/api/parents')).parents,holder=guardianElement('parent-link-list');holder.replaceChildren();
  if(!parents.length){const empty=document.createElement('p');empty.className='directory-empty';empty.textContent='No parent access created yet.';holder.append(empty);return;}
  for(const parent of parents){
    const row=document.createElement('div');row.className='person-row';const info=document.createElement('div');info.className='person-info';
    const name=document.createElement('strong');name.textContent=parent.name;const student=document.createElement('p');student.textContent=`Linked student: ${parent.student_name} · Read-only access`;info.append(name,student);
    const revoke=document.createElement('button');revoke.className='text-button';revoke.textContent='Revoke access';revoke.setAttribute('aria-label',`Revoke access for ${parent.name}`);revoke.disabled=guardianBusy;
    revoke.onclick=async()=>{
      if(guardianBusy) return;setGuardianBusy(true);guardianElement('parent-admin-error').hidden=true;
      try{await guardianApi(`/api/parents/${parent.id}/revoke`,'POST',{});guardianElement('new-parent-code').value='';guardianElement('parent-code-result').hidden=true;await loadGuardianLinks();}
      catch(error){guardianError(error);}finally{setGuardianBusy(false);}
    };
    row.append(info,revoke);holder.append(row);
  }
}
guardianElement('parent-link-form').onsubmit=async event=>{
  event.preventDefault();if(guardianBusy) return;setGuardianBusy(true);guardianElement('parent-admin-error').hidden=true;
  try{
    const parent=(await guardianApi('/api/parents','POST',{name:guardianElement('guardian-name').value.trim(),person_id:Number(guardianElement('guardian-student').value)})).parent;
    guardianElement('parent-code-heading').textContent=`${parent.name} → ${parent.student_name}`;guardianElement('new-parent-code').value=parent.access_code;
    guardianElement('parent-code-result').hidden=false;guardianElement('guardian-name').value='';await loadGuardianLinks();
  }catch(error){guardianError(error);}finally{setGuardianBusy(false);}
};
guardianElement('dismiss-parent-code').onclick=()=>{guardianElement('new-parent-code').value='';guardianElement('parent-code-result').hidden=true;};
guardianElement('staff-logout').onclick=async()=>{
  guardianElement('staff-logout').disabled=true;
  try{await guardianApi('/api/staff/logout','POST',{});window.location.assign('/');}
  catch(error){guardianError(error);guardianElement('staff-logout').disabled=false;}
};
