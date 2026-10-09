'use strict';
const parentElement=id=>document.getElementById(id);
let parentSignedIn=false, parentRequest=0, parentSignature='', parentRefreshing=false;
const dateTime=value=>value ? new Date(value).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:parentCalendar.zone}) : '—';
const parentCalendar=new ParentCalendar(()=>refreshParent());
const statusLabels={present:'Present',pending:'Not seen yet',absent:'Absent'};
function showParentLogin(){
  parentSignedIn=false;parentElement('parent-dashboard').hidden=true;parentElement('parent-login').hidden=false;parentElement('parent-logout').hidden=true;
  parentSignature='';parentCalendar.clear();
  for(const id of ['student-name','parent-greeting','parent-session','parent-status-title','parent-status-detail','parent-first-seen','parent-last-seen','parent-sync']) parentElement(id).textContent='';parentElement('parent-history').replaceChildren();
}
async function parentFetch(path,body){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
  const response=await fetch(path,{signal:controller.signal,...(body ? {method:'POST',headers:{'Content-Type':'application/json','X-Portal-Request':'1'},body:JSON.stringify(body)} : {})});
  const data=await response.json();
  if(!response.ok) throw Object.assign(new Error(data.error || 'Could not load attendance.'),{status:response.status});
  return data;
  }catch(error){if(error.name==='AbortError') throw new Error('The server took too long to respond. Try again.');throw error;}finally{clearTimeout(timeout);}
}
async function refreshParent(){
  const request=++parentRequest;
  try{
    const data=await parentFetch(`/api/parent/attendance?month=${encodeURIComponent(parentCalendar.month)}`);
    if(request!==parentRequest) return;
    const signature=JSON.stringify(data);
    if(signature===parentSignature){parentElement('parent-sync').textContent=`Updated ${new Date().toLocaleTimeString()}. Refreshes every 10 seconds while this page is open.`;return;}
    parentSignature=signature;parentCalendar.update(data);
    parentSignedIn=true;parentElement('parent-login').hidden=true;parentElement('parent-dashboard').hidden=false;parentElement('parent-logout').hidden=false;
    parentElement('student-name').textContent=data.student_name;
    parentElement('parent-greeting').textContent=`Welcome, ${data.parent_name}. Here's your student's attendance.`;
    const current=data.current || (!data.active_session ? data.history[0] : null);
    parentElement('parent-session').textContent=current ? `${current.session_name} · ${current.session_status==='active' ? 'In progress' : 'Latest saved session'}` : 'No active attendance for this student';
    parentElement('parent-status').className=`parent-status ${current?.status || 'none'}`;
    parentElement('parent-status-title').textContent=current ? current.status==='present' && current.method==='camera' ? 'Checked in' : statusLabels[current.status] : 'No check-in session open';
    parentElement('parent-status-detail').textContent=current ? current.status==='present' ? current.method==='manual' ? 'Staff marked your student present for this session.' : 'Your student was recognized and marked present.' : current.status==='pending' ? 'No check-in recorded yet. Attendance is still being taken.' : current.method==='manual' ? 'Staff marked your student absent for this session.' : 'No check-in was recorded during this session.' : data.active_session ? 'Your student is not on the current session roster. Check with staff.' : 'Review the latest saved attendance below.';
    parentElement('parent-first-seen').textContent=dateTime(current?.first_seen_at);
    parentElement('parent-last-seen').textContent=dateTime(current?.last_seen_at);
    const holder=parentElement('parent-history');holder.replaceChildren();
    if(!data.history.length){const empty=document.createElement('p');empty.className='directory-empty';empty.textContent='No attendance recorded yet.';holder.append(empty);}
    else{
      const table=document.createElement('table');table.className='attendance-table parent-history-table';table.setAttribute('aria-label','Student attendance history');
      const head=document.createElement('thead');head.innerHTML='<tr><th>Session</th><th>Status</th><th>Camera check-in</th></tr>';table.append(head);
      const body=document.createElement('tbody');
      for(const record of data.history){
        const row=document.createElement('tr'),name=document.createElement('td'),status=document.createElement('td'),seen=document.createElement('td');
        name.textContent=record.session_name;const when=document.createElement('span');when.className='attendance-source';when.textContent=`${dateTime(record.started_at)} · ${record.session_status==='active' ? 'In progress' : 'Ended'}`;name.append(when);
        const badge=document.createElement('span');badge.className=`status-badge ${record.status}`;badge.textContent=statusLabels[record.status];status.append(badge);
        if(record.method==='manual'){const source=document.createElement('span');source.className='attendance-source';source.textContent='Staff correction';status.append(source);}
        seen.textContent=dateTime(record.first_seen_at);row.append(name,status,seen);body.append(row);
      }table.append(body);holder.append(table);
    }
    parentElement('parent-sync').textContent=`Updated ${new Date().toLocaleTimeString()}. Refreshes every 10 seconds while this page is open.`;
  }catch(error){
    if(request!==parentRequest) return;
    if(error.status===401){showParentLogin();return;}
    if(parentSignedIn) parentElement('parent-sync').textContent='Could not refresh. Showing the last loaded attendance; try Refresh.';
    else{parentElement('parent-login-error').textContent=error.message;parentElement('parent-login-error').hidden=false;}
  }
}
parentElement('parent-login-form').onsubmit=async event=>{
  event.preventDefault();const button=parentElement('parent-sign-in');button.disabled=true;parentElement('parent-login-error').hidden=true;
  try{await parentFetch('/api/parent/login',{code:parentElement('parent-code').value});parentElement('parent-code').value='';await refreshParent();}
  catch(error){parentElement('parent-login-error').textContent=error.message;parentElement('parent-login-error').hidden=false;}
  finally{button.disabled=false;}
};
parentElement('parent-logout').onclick=async()=>{
  parentElement('parent-logout').disabled=true;
  try{await parentFetch('/api/parent/logout',{});++parentRequest;showParentLogin();}
  catch(error){parentElement('parent-sync').textContent='Could not sign out. Try again.';}
  finally{parentElement('parent-logout').disabled=false;}
};
parentElement('parent-refresh').onclick=refreshParent;
setInterval(()=>{if(parentSignedIn && !document.hidden && !parentRefreshing){parentRefreshing=true;refreshParent().finally(()=>parentRefreshing=false);}},10000);
document.addEventListener('visibilitychange',()=>{if(parentSignedIn && !document.hidden) refreshParent();});
refreshParent();
