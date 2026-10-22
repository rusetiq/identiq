'use strict';
(()=>{
  const element=id=>document.getElementById(id),pageBuild=document.querySelector('meta[name="app-build"]')?.content;
  let activeCheck=null;
  function row(label,value){const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=String(value);element('connection-check-results').append(term,description);}
  async function check(){
    const button=element('run-connection-check');if(button.disabled)return;button.disabled=true;
    const controller=new AbortController();activeCheck=controller;
    element('connection-check-results').replaceChildren();element('connection-check-summary').textContent='Checking this device…';element('connection-check-help').textContent='';
    row('Page version',pageBuild || 'Older page');row('Address',location.origin+location.pathname);
    row('Wi-Fi / network',navigator.onLine===false?'Device reports offline':'Device reports online');
    row('Camera access',window.isSecureContext && navigator.mediaDevices?.getUserMedia?'Available in this browser':'Requires HTTPS on the tablet, or localhost on the laptop');
    const failed=window.IdentiqNetwork.lastFailure();
    if(failed){row('Last failed request',failed.request);row('Failure',`${failed.code} · ${failed.status?'HTTP '+failed.status:'No HTTP response'}${failed.recovered?' · Recovered':''}`);}
    const started=performance.now();
    try{
      const server=await window.IdentiqNetwork.request('/connection-check?format=json',{timeoutMs:6000,signal:controller.signal});
      if(!server.running_build || !server.installed_build)throw Object.assign(new Error('Older server'),{code:'old_server'});
      const firstMs=Math.round(performance.now()-started),samples=[firstMs];let failedChecks=0;
      row('Server version',server.running_build);row('Installed version',server.installed_build);row('Server response',`${firstMs} ms`);
      const scan=window.IdentiqScan?.last();
      if(scan){
        row('Last camera scan',new Date(scan.recorded_at).toLocaleTimeString());
        if(scan.request_ms!==null)row('Scan request round trip',`${scan.request_ms} ms`);
        if(scan.processing_ms!==null)row('Scan server processing',`${scan.processing_ms} ms`);
        row('Faces in last scan',scan.face_count);
      }
      const source=document.getElementById('shared-status');if(source)row('Shared camera',source.textContent);
      element('connection-check-summary').textContent='Server reached. Checking whether the connection stays available…';
      for(let index=1;index<5;index++){
        await new Promise(resolve=>setTimeout(resolve,500));
        if(controller.signal.aborted)return;
        const probeStarted=performance.now();
        try{
          await window.IdentiqNetwork.request('/connection-check?format=json',{timeoutMs:2500,signal:controller.signal});
          samples.push(Math.round(performance.now()-probeStarted));
        }catch(error){if(controller.signal.aborted)return;failedChecks++;}
      }
      row('Connection stability',`${samples.length} of 5 requests succeeded (2.5-second follow-up deadline)`);
      row('Slowest successful check',`${Math.max(...samples)} ms`);
      if(server.needs_restart){
        element('connection-check-summary').textContent='Updated files are installed, but the server is still running older code.';
        element('connection-check-help').textContent='On the attendance laptop, stop the running server with Ctrl+C and start it again. Then refresh this tablet page.';
      }else if(pageBuild!==server.running_build){
        element('connection-check-summary').textContent='This tablet page is using an older version.';
        element('connection-check-help').textContent='Refresh this tablet page to load the current code.';
      }else if(failedChecks || Math.max(...samples)>1500){
        element('connection-check-summary').textContent='The connection to the attendance laptop is intermittent or slow.';
        element('connection-check-help').textContent='Run this check downstairs and upstairs. Ask school IT to verify upstairs Wi-Fi coverage and access from that floor to '+location.host+'. Keep the attendance laptop awake. These checks cannot distinguish Wi-Fi, routing, firewall or server problems on their own.';
      }else if(failed && !failed.recovered && ['server_unavailable','invalid_response'].includes(failed.code)){
        element('connection-check-summary').textContent='The server is reachable, but the last app request failed.';
        element('connection-check-help').textContent='Show the failed request and HTTP status above to the administrator, together with the server terminal error. The connection check itself reached the server.';
      }else{
        element('connection-check-summary').textContent='This page and the server are up to date. All five connection checks succeeded.';
        element('connection-check-help').textContent=window.isSecureContext?'If the problem returns, open this check while it happens and show these results to the administrator.':'On a tablet, camera access needs the HTTPS address. If HTTPS does not open, the tablet’s user must trust the local certificate first.';
      }
    }catch(error){
      if(controller.signal.aborted)return;
      row('Server response','Unavailable');
      element('connection-check-summary').textContent=error.code==='old_server' || error.code==='invalid_response'?'The running server does not support this connection check.':'This device cannot reach the attendance server.';
      element('connection-check-help').textContent=error.code==='old_server' || error.code==='invalid_response'?'Stop and restart the server after git pull, then refresh this tablet.':'Check that the attendance laptop is awake, the server is running, and both devices are on the same Wi-Fi. Verify the tablet’s address and trusted HTTPS certificate.';
    }finally{button.disabled=false;if(activeCheck===controller)activeCheck=null;}
  }
  const dialog=element('connection-check-dialog'),home=dialog.parentElement,next=dialog.nextSibling;
  const open=()=>{
    const card=document.getElementById('camera-card');
    if(card?.classList.contains('camera-expanded')){card.append(dialog);dialog.inert=false;}
    dialog.showModal();check();
  };
  dialog.addEventListener('close',()=>{activeCheck?.abort();home.insertBefore(dialog,next);});
  element('open-connection-check').onclick=open;document.querySelectorAll('[data-connection-check]').forEach(button=>button.onclick=open);
  element('close-connection-check').onclick=()=>element('connection-check-dialog').close();element('run-connection-check').onclick=check;
})();
