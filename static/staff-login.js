'use strict';
document.getElementById('staff-login-form').onsubmit=async event=>{
  event.preventDefault();
  const button=document.getElementById('staff-sign-in'), error=document.getElementById('staff-login-error'), input=document.getElementById('staff-password');
  button.disabled=true;error.hidden=true;
  try{
    await window.IdentiqNetwork.request('/api/staff/login',{method:'POST',headers:{'X-Portal-Request':'1'},body:{login_id:document.getElementById('staff-login-id').value.trim(),password:input.value}});
    input.value='';window.location.assign('/');
  }catch(issue){error.textContent=issue.message || 'Could not connect. Try again.';error.hidden=false;}
  finally{button.disabled=false;}
};
