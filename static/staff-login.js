'use strict';
document.getElementById('staff-login-form').onsubmit=async event=>{
  event.preventDefault();
  const button=document.getElementById('staff-sign-in'), error=document.getElementById('staff-login-error'), input=document.getElementById('staff-code');
  button.disabled=true;error.hidden=true;
  try{
    const response=await fetch('/api/staff/login',{method:'POST',headers:{'Content-Type':'application/json','X-Portal-Request':'1'},body:JSON.stringify({code:input.value})});
    const data=await response.json();
    if(!response.ok) throw new Error(data.error);
    input.value='';window.location.assign('/');
  }catch(issue){error.textContent=issue.message || 'Could not connect. Try again.';error.hidden=false;}
  finally{button.disabled=false;}
};
