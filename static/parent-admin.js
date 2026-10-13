'use strict';
const guardianElement=id=>document.getElementById(id);
let guardianBusy=false;
async function guardianApi(path,method='GET',body){
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Local-Token':document.querySelector('meta[name="local-token"]').content},...(body ? {body:JSON.stringify(body)} : {})});
  const data=await response.json();if(!response.ok) throw new Error(data.error || 'Request failed.');return data;
}
function guardianError(error){guardianElement('parent-admin-error').textContent=error.message;guardianElement('parent-admin-error').hidden=false;}
function setGuardianBusy(busy){guardianBusy=busy;guardianElement('create-parent-link').disabled=busy;guardianElement('guardian-name').disabled=busy;guardianElement('guardian-student').disabled=busy;guardianElement('guardian-login-id').disabled=busy;guardianElement('guardian-password').disabled=busy;document.querySelectorAll('#parent-link-list button').forEach(button=>button.disabled=busy);}
document.addEventListener('people-refreshed',event=>{
  const select=guardianElement('guardian-student'),selected=select.value;select.replaceChildren(new Option('Choose a student',''));
  event.detail.forEach(person=>select.add(new Option(`${person.name} · ID ${person.id}`,person.id)));
  if(event.detail.some(person=>String(person.id)===selected)) select.value=selected;
  loadGuardianLinks().catch(guardianError);
});
async function loadGuardianLinks(){
  const parents=(await guardianApi('/api/parents')).parents,holder=guardianElement('parent-link-list');holder.replaceChildren();
  if(!parents.length){const empty=document.createElement('p');empty.className='directory-empty';empty.textContent='No parent accounts created yet.';holder.append(empty);return;}
  for(const parent of parents){
    const row=document.createElement('div');row.className='person-row';const info=document.createElement('div');info.className='person-info';
    const name=document.createElement('strong');name.textContent=parent.name;const student=document.createElement('p');student.textContent=`Linked student: ${parent.student_name} · Login ID: ${parent.login_id} · ${parent.needs_password ? "Password required" : "Read-only access"}`;info.append(name,student);
    const revoke=document.createElement('button');revoke.className='text-button';revoke.textContent='Revoke access';revoke.setAttribute('aria-label',`Revoke access for ${parent.name}`);revoke.disabled=guardianBusy;
    revoke.onclick=async()=>{
      if(guardianBusy) return;setGuardianBusy(true);guardianElement('parent-admin-error').hidden=true;
      try{await guardianApi(`/api/parents/${parent.id}/revoke`,'POST',{});guardianElement('parent-account-result').hidden=true;await loadGuardianLinks();}
      catch(error){guardianError(error);}finally{setGuardianBusy(false);}
    };
    const reset=document.createElement('button');reset.className='text-button';reset.textContent=parent.needs_password?'Set password':'Reset login';reset.setAttribute('aria-label',`Reset login for ${parent.name}`);reset.onclick=()=>openParentCredentials(parent);reset.disabled=guardianBusy;
    row.append(info,reset,revoke);holder.append(row);
  }
}
guardianElement('parent-link-form').onsubmit=async event=>{
  event.preventDefault();if(guardianBusy) return;setGuardianBusy(true);guardianElement('parent-admin-error').hidden=true;
  try{
    const parent=(await guardianApi('/api/parents','POST',{name:guardianElement('guardian-name').value.trim(),person_id:Number(guardianElement('guardian-student').value),login_id:guardianElement('guardian-login-id').value.trim(),password:guardianElement('guardian-password').value})).parent;
    guardianElement('parent-account-result').textContent=`Account created for ${parent.name} (${parent.login_id}), linked to ${parent.student_name}. Share the password privately.`;guardianElement('parent-account-result').hidden=false;guardianElement('guardian-name').value='';guardianElement('guardian-login-id').value='';guardianElement('guardian-password').value='';await loadGuardianLinks();
  }catch(error){guardianError(error);}finally{setGuardianBusy(false);}
};
guardianElement('staff-logout').onclick=async()=>{
  guardianElement('staff-logout').disabled=true;
  try{await guardianApi('/api/staff/logout','POST',{});window.location.assign('/');}
  catch(error){guardianError(error);guardianElement('staff-logout').disabled=false;}
};

let resetParent=null;
function openParentCredentials(parent){
  resetParent=parent.id;guardianElement('parent-credentials-description').textContent=`${parent.name} → ${parent.student_name}`;
  guardianElement('reset-parent-login-id').value=parent.login_id;
  guardianElement('reset-parent-password').value='';guardianElement('reset-parent-confirm').value='';
  guardianElement('parent-credentials-error').hidden=true;guardianElement('parent-credentials-dialog').showModal();
}
guardianElement('cancel-parent-credentials').onclick=()=>{guardianElement('parent-credentials-form').reset();guardianElement('parent-credentials-dialog').close();};
guardianElement('parent-credentials-form').onsubmit=async event=>{
  event.preventDefault();if(guardianBusy)return;
  const password=guardianElement('reset-parent-password').value,error=guardianElement('parent-credentials-error');error.hidden=true;
  if(password!==guardianElement('reset-parent-confirm').value){error.textContent='Passwords must match.';error.hidden=false;return;}
  setGuardianBusy(true);guardianElement('save-parent-credentials').disabled=true;
  try{await guardianApi(`/api/parents/${resetParent}/credentials`,'PATCH',{login_id:guardianElement('reset-parent-login-id').value.trim(),password});guardianElement('parent-credentials-form').reset();guardianElement('parent-credentials-dialog').close();await loadGuardianLinks();}
  catch(issue){error.textContent=issue.message;error.hidden=false;}
  finally{setGuardianBusy(false);guardianElement('save-parent-credentials').disabled=false;}
};
guardianElement('staff-account-settings').onclick=async()=>{
  try{const account=await guardianApi('/api/staff/account');guardianElement('staff-account-form').reset();guardianElement('staff-account-login-id').value=account.login_id;guardianElement('staff-account-error').hidden=true;guardianElement('staff-account-dialog').showModal();}catch(issue){guardianError(issue);}
};
guardianElement('cancel-staff-account').onclick=()=>{guardianElement('staff-account-form').reset();guardianElement('staff-account-dialog').close();};
guardianElement('staff-account-form').onsubmit=async event=>{
  event.preventDefault();const error=guardianElement('staff-account-error'),password=guardianElement('staff-new-password').value;error.hidden=true;
  if(password!==guardianElement('staff-confirm-password').value){error.textContent='Passwords must match.';error.hidden=false;return;}
  guardianElement('save-staff-account').disabled=true;
  try{await guardianApi('/api/staff/account','PATCH',{current_password:guardianElement('staff-current-password').value,login_id:guardianElement('staff-account-login-id').value.trim(),password});guardianElement('staff-account-form').reset();window.location.assign('/');}
  catch(issue){error.textContent=issue.message;error.hidden=false;}
  finally{guardianElement('save-staff-account').disabled=false;}
};
