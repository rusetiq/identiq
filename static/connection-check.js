'use strict';
(()=>{
  const element=id=>document.getElementById(id),pageBuild=document.querySelector('meta[name="app-build"]')?.content;
  function row(label,value){const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=String(value);element('connection-check-results').append(term,description);}
  async function check(){
    const button=element('run-connection-check');if(button.disabled)return;button.disabled=true;
    element('connection-check-results').replaceChildren();element('connection-check-summary').textContent='Checking this device…';element('connection-check-help').textContent='';
    row('Page version',pageBuild || 'Older page');row('Address',location.origin+location.pathname);
    row('Wi-Fi / network',navigator.onLine===false?'Device reports offline':'Device reports online');
    row('Camera access',window.isSecureContext && navigator.mediaDevices?.getUserMedia?'Available in this browser':'Requires HTTPS on the tablet, or localhost on the laptop');
    const failed=window.IdentiqNetwork.lastFailure();
    if(failed){row('Last failed request',failed.request);row('Failure',`${failed.code} · ${failed.status?'HTTP '+failed.status:'No HTTP response'}${failed.recovered?' · Recovered':''}`);}
    const started=performance.now();
    try{
      const server=await window.IdentiqNetwork.request('/connection-check?format=json',{timeoutMs:6000});
      if(!server.running_build || !server.installed_build)throw Object.assign(new Error('Older server'),{code:'old_server'});
      row('Server version',server.running_build);row('Installed version',server.installed_build);row('Server response',`${Math.round(performance.now()-started)} ms`);
      const source=document.getElementById('shared-status');if(source)row('Shared camera',source.textContent);
      if(server.needs_restart){
        element('connection-check-summary').textContent='Updated files are installed, but the server is still running older code.';
        element('connection-check-help').textContent='On the attendance laptop, stop the running server with Ctrl+C and start it again. Then refresh this tablet page.';
      }else if(pageBuild!==server.running_build){
        element('connection-check-summary').textContent='This tablet page is using an older version.';
        element('connection-check-help').textContent='Refresh this tablet page to load the current code.';
      }else if(failed && !failed.recovered && ['server_unavailable','invalid_response'].includes(failed.code)){
        element('connection-check-summary').textContent='The server is reachable, but the last app request failed.';
        element('connection-check-help').textContent='Show the failed request and HTTP status above to the administrator, together with the server terminal error. The connection check itself reached the server.';
      }else{
        element('connection-check-summary').textContent='This page and the server are up to date and reachable.';
        element('connection-check-help').textContent=window.isSecureContext?'If the problem returns, open this check while it happens and show these results to the administrator.':'On a tablet, camera access needs the HTTPS address. If HTTPS does not open, the tablet’s user must trust the local certificate first.';
      }
    }catch(error){
      row('Server response','Unavailable');
      element('connection-check-summary').textContent=error.code==='old_server' || error.code==='invalid_response'?'The running server does not support this connection check.':'This device cannot reach the attendance server.';
      element('connection-check-help').textContent=error.code==='old_server' || error.code==='invalid_response'?'Stop and restart the server after git pull, then refresh this tablet.':'Check that the attendance laptop is awake, the server is running, and both devices are on the same Wi-Fi. Verify the tablet’s address and trusted HTTPS certificate.';
    }finally{button.disabled=false;}
  }
  const dialog=element('connection-check-dialog'),home=dialog.parentElement,next=dialog.nextSibling;
  const open=()=>{
    const card=document.getElementById('camera-card');
    if(card?.classList.contains('camera-expanded')){card.append(dialog);dialog.inert=false;}
    dialog.showModal();check();
  };
  dialog.addEventListener('close',()=>home.insertBefore(dialog,next));
  element('open-connection-check').onclick=open;document.querySelectorAll('[data-connection-check]').forEach(button=>button.onclick=open);
  element('close-connection-check').onclick=()=>element('connection-check-dialog').close();element('run-connection-check').onclick=check;
})();
