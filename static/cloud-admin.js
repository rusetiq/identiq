'use strict';
(()=>{
  const status=document.getElementById('cloud-status'),form=document.getElementById('cloud-key-form'),secret=document.getElementById('cloud-key-secret'),list=document.getElementById('cloud-key-list');
  let busy=false;
  async function load(){
    const cloud=await api('/api/staff/cloud');
    status.textContent=cloud.configured?`${cloud.url} · ${cloud.pending} pending updates · ${cloud.scanner_ready?'Server connected':'Create a server connection key to start sync'}${cloud.error?' · '+cloud.error:''}`:'Cloud API has not been configured on this server.';
    form.hidden=!cloud.configured;
    if(!cloud.configured)return;
    const data=await api('/api/staff/cloud/keys');list.replaceChildren();
    for(const key of data.keys){
      const row=document.createElement('p'),label=document.createElement('span');label.textContent=`${key.name} · ${key.scope} · ${key.source}${key.revoked_at?' · Revoked':''} `;row.append(label);
      if(!key.revoked_at){const button=document.createElement('button');button.className='text-button';button.textContent='Revoke';button.onclick=async()=>{if(!confirm(`Revoke ${key.name}? Connected services using it will stop syncing.`))return;button.disabled=true;try{await api('/api/staff/cloud/keys/'+key.id,'DELETE');await load();}catch(error){status.textContent=error.message;button.disabled=false;}};row.append(button);}
      list.append(row);
    }
  }
  form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;busy=true;const button=form.querySelector('button');button.disabled=true;secret.hidden=true;
    try{
      const key=await api('/api/staff/cloud/keys','POST',{name:document.getElementById('cloud-key-name').value,source:document.getElementById('cloud-key-source').value,scope:document.getElementById('cloud-key-scope').value});
      secret.textContent=`Copy this key now. It is shown once: ${key.key}`;secret.hidden=false;await load();
    }catch(error){status.textContent=error.message;}finally{busy=false;button.disabled=false;}
  };
  load().catch(error=>status.textContent=error.message);
})();
