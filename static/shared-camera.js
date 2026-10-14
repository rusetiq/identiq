'use strict';
class SharedRoom {
  constructor(){
    this.socket=null;this.snapshot=null;this.applying=false;this.connected=false;this.previewUrl=null;
    this.lastAttendance=-1;this.lastEpoch=-1;this.uploadTimer=null;this.canvas=document.createElement('canvas');
    this.retry=null;this.closed=false;this.failures=0;this.shareIntent=false;this.resumeKey=null;this.restoreSession=null;
    this.recovering=false;this.connecting=false;this.claimPending=false;this.encoding=false;this.lastUpload=0;
    $('share-camera').onclick=()=>{if(this.send({type:'claim',action:state.movementMode,axis:$('crossing-axis').value,direction:$('entry-direction').value}))this.shareIntent=true;};
    $('stop-sharing').onclick=()=>this.release();
    window.addEventListener('offline',()=>{connectionStatus('socket',true);clearTimeout(this.retry);this.socket?.close();});
    window.addEventListener('online',()=>{clearTimeout(this.retry);this.connect();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden && !this.connected){clearTimeout(this.retry);this.connect();}});
    window.addEventListener('pagehide',()=>this.shutdown());
    this.connect();
  }
  get source(){return !!this.snapshot?.source_id && this.snapshot.source_id===this.snapshot.client_id;}
  shutdown(){this.closed=true;clearTimeout(this.retry);clearInterval(this.uploadTimer);clearInterval(this.heartbeat);this.socket?.close();this.clearPreview();}
  schedule(){
    if(this.closed || this.retry || navigator.onLine===false)return;
    const delay=Math.min(15000,500*2**Math.min(this.failures++,5))*(.85+Math.random()*.3);
    this.retry=setTimeout(()=>{this.retry=null;this.connect();},delay);
  }
  async connect(){
    if(this.closed || this.connecting || this.socket && this.socket.readyState<2 || navigator.onLine===false)return;
    this.connecting=true;clearTimeout(this.retry);this.retry=null;
    try{await refreshClientToken();}catch(error){this.connecting=false;if(error.code!=='staff_required')this.schedule();return;}
    if(this.closed){this.connecting=false;return;}
    let socket;
    try{socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/shared/ws`);}catch(error){this.connecting=false;this.schedule();return;}
    this.socket=socket;socket.binaryType='blob';
    const deadline=setTimeout(()=>{if(!this.connected)socket.close();},8000);
    socket.onopen=()=>socket.send(JSON.stringify({type:'hello',token}));
    socket.onmessage=event=>{
      if(this.socket!==socket)return;this.lastHeard=Date.now();
      if(event.data instanceof Blob){this.showPreview(event.data);return;}
      try{
        const message=JSON.parse(event.data);
        if(message.type==='auth_required'){expiredSession();return;}
        if(message.type==='error'){
          if(['source_busy','source_reconnecting'].includes(message.code)){this.claimPending=false;return;}
          this.restoreSession=null;toast(message.message);
        }else if(message.type==='state'){
          clearTimeout(deadline);this.connected=true;this.connecting=false;this.failures=0;connectionStatus('socket',false);
          if(!this.heartbeat)this.heartbeat=setInterval(()=>{
            if(Date.now()-this.lastHeard>15000){socket.close();return;}
            this.send({type:'heartbeat'});
          },5000);
          this.apply(message);
        }
      }catch(error){socket.close();}
    };
    socket.onerror=()=>socket.close();
    socket.onclose=()=>{
      clearTimeout(deadline);if(this.socket!==socket)return;
      if(this.source && this.shareIntent){this.shareIntent=!!state.stream;this.resumeKey=this.snapshot.source_key;this.restoreSession=this.snapshot.armed?this.snapshot.session_id:null;}
      this.recovering=this.shareIntent;this.claimPending=false;this.connected=false;this.connecting=false;
      this.applying=true;if(this.source)stopScan();this.applying=false;
      this.snapshot=null;clearInterval(this.uploadTimer);this.uploadTimer=null;clearInterval(this.heartbeat);this.heartbeat=null;
      this.clearPreview();connectionStatus('socket',true);updateControls();this.schedule();
    };
  }
  send(message){
    if(this.connected && this.socket.readyState===WebSocket.OPEN){
      try{this.socket.send(message instanceof Blob?message:JSON.stringify(message));return true;}
      catch(error){this.socket.close();}
    }
    connectionStatus('socket',true);return false;
  }
  toggleScan(){
    const pause=this.snapshot?.running || this.snapshot?.armed || this.snapshot?.recovering;
    if(pause)this.restoreSession=null;
    this.send({type:pause?'pause':'start',session_id:state.activeAttendance?.id});
  }
  configure(action,axis,direction){this.restoreSession=null;this.send({type:'configure',action,axis,direction});}
  scanContext(){return {shared_key:this.snapshot?.source_key,shared_epoch:this.snapshot?.epoch};}
  localStopped(){
    if(this.applying)return;
    this.restoreSession=null;
    if(this.source && this.snapshot?.armed)this.send({type:'pause'});
  }
  release(){this.releasing=true;this.shareIntent=false;this.recovering=false;this.restoreSession=null;this.resumeKey=null;if(this.source){clearInterval(this.uploadTimer);this.uploadTimer=null;this.send({type:'release'});}}
  apply(message){
    const wasSource=this.source;this.snapshot=message;this.applying=true;
    if(!message.source_id)this.releasing=false;
    try{
      state.movementMode=message.action;$('crossing-axis').value=message.axis;$('entry-direction').value=message.direction;
      const vertical=message.axis==='y';$('entry-direction').options[0].textContent=vertical?'Top → bottom':'Left → right';$('entry-direction').options[1].textContent=vertical?'Bottom → top':'Right → left';
      if(wasSource && !this.source && state.scanSessionId)stopScan();
      if(!message.source_id && this.shareIntent && state.stream && state.mode==='attendance' && !this.claimPending){
        this.claimPending=this.send({type:'claim',resume_key:this.resumeKey,action:message.action,axis:message.axis,direction:message.direction});
      }
      if(this.source){
        if(this.releasing){this.send({type:'release'});return;}
        this.claimPending=false;this.shareIntent=true;this.resumeKey=message.source_key;this.clearPreview();
        if(!state.stream || state.mode!=='attendance'){this.release();return;}
        if(!this.uploadTimer){this.uploadTimer=setInterval(()=>this.publish(),200);this.publish();}
        if(this.recovering && message.ready){
          const restore=this.restoreSession;this.recovering=false;this.restoreSession=null;
          // A surviving server owns the pause state. After a restart, restore only the same day.
          if(!message.resumed && restore && !message.armed)this.send({type:'start',session_id:restore});
        }
        if(message.running && (this.lastEpoch!==message.epoch || !state.scanning)){
          if(state.scanning)stopScan();this.lastEpoch=message.epoch;
          scan(message.session_id,true).catch(error=>{if(!transientError(error))toast(error.message);this.localStopped();});
        }else if(!message.running && state.scanSessionId)stopScan();
      }else{
        clearInterval(this.uploadTimer);this.uploadTimer=null;
        if(!message.source_id || !message.ready)this.clearPreview();
      }
      if(this.lastAttendance!==message.attendance_revision){this.lastAttendance=message.attendance_revision;refreshAttendance().catch(error=>{if(!transientError(error))toast(error.message);});}
      updateControls();liveTracker.render();
    }finally{this.applying=false;}
  }
  publish(){
    if(!this.source || !state.stream || !this.connected || state.mode!=='attendance' || this.encoding)return;
    const now=performance.now();if(now-this.lastUpload<(this.snapshot.viewers ? 250 : 1000) || this.socket.bufferedAmount>350000)return;
    const video=$('video');if(!video.videoWidth || video.readyState<2)return;
    this.lastUpload=now;
    const canvas=this.canvas,height=Math.max(1,Math.min(720,Math.round(640*$('viewfinder').clientHeight/$('viewfinder').clientWidth)));
    if(canvas.width!==640 || canvas.height!==height){canvas.width=640;canvas.height=height;}
    const ctx=canvas.getContext('2d'),scale=Math.min(canvas.width/video.videoWidth,canvas.height/video.videoHeight),w=video.videoWidth*scale,h=video.videoHeight*scale;
    ctx.fillStyle='#24282e';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.save();ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,(canvas.width-w)/2,(canvas.height-h)/2,w,h);ctx.restore();
    const overlay=$('overlay');if(overlay.width && overlay.height)ctx.drawImage(overlay,0,0,canvas.width,canvas.height);
    this.encoding=true;const socket=this.socket,key=this.resumeKey;
    canvas.toBlob(blob=>{this.encoding=false;if(blob && this.source && this.connected && this.socket===socket && this.resumeKey===key && socket.bufferedAmount<350000)this.send(blob);},'image/jpeg',.72);
  }
  showPreview(blob){
    if(this.source || !this.snapshot?.ready || state.mode!=='attendance')return;
    this.clearPreview(false);this.previewUrl=URL.createObjectURL(blob);
    $('shared-feed').onload=()=>{$('feed-resolution').textContent=`${$('shared-feed').naturalWidth} × ${$('shared-feed').naturalHeight}`;};$('shared-feed').src=this.previewUrl;$('shared-feed').hidden=false;
    $('camera-empty').hidden=true;$('overlay').hidden=true;$('video').hidden=true;
    $('viewfinder').classList.add('live');$('feed-label').textContent=this.snapshot.running?'SHARED ATTENDANCE SCAN':'SHARED LIVE PREVIEW';
  }
  clearPreview(reset=true){
    if(this.previewUrl){URL.revokeObjectURL(this.previewUrl);this.previewUrl=null;}
    if(reset){$('shared-feed').hidden=true;$('shared-feed').removeAttribute('src');$('overlay').hidden=false;$('video').hidden=false;$('camera-empty').hidden=!!state.stream;if(!state.stream)$('viewfinder').classList.remove('live');}
  }
  renderControls(){
    const snap=this.snapshot,available=this.connected && !!snap?.source_id && snap.ready;
    $('shared-status').textContent=!this.connected?'Connection interrupted. Reconnecting automatically…':snap?.recovering?'Camera connection interrupted. Waiting for it to reconnect…':!snap?.source_id?'Enable this device’s camera, then choose Share this camera.':this.source?`Sharing this camera · ${snap.staff_tabs} staff tabs · ${snap.running?'Scanning':snap.armed?'Recovering camera':'Paused'}`:`Viewing shared camera · ${snap.staff_tabs} staff tabs · ${snap.ready?(snap.running?'Scanning':'Paused'):'Waiting for the camera'}`;
    $('share-camera').hidden=!!snap?.source_id || !!snap?.recovering;$('share-camera').disabled=!this.connected || !state.stream || !state.ready || state.mode!=='attendance' || state.enrolling;
    $('stop-sharing').hidden=!this.source && !this.shareIntent;
    $('attendance-scan').disabled=$('fullscreen-scan').disabled=state.attendanceBusy || state.enrolling || !available && !snap?.recovering || !state.activeAttendance;
    $('attendance-scan').textContent=$('fullscreen-scan').textContent=snap?.running || snap?.armed || snap?.recovering?'Pause shared scan':'Start shared scan';
    if(!this.source && !state.stream){$('camera-label').textContent=available?'SHARED CAMERA':'CAMERA OFF';$('camera-dot').classList.toggle('off',!available);$('tracking-title').textContent=available?'Shared camera feed':'Camera off';$('tracking-detail').textContent=available?'Viewing the designated camera.':'Enable a camera to begin.';if(snap?.source_id)result(snap.running?'Shared scan running':'Shared scan paused','Recognition runs on the camera source. Attendance updates appear below.');}
    if(snap?.source_id && !this.source){$('start-camera').disabled=true;$('attendance-flow').disabled=$('fullscreen-flow').disabled=state.enrolling || state.attendanceBusy;}
    if(!navigator.mediaDevices?.getUserMedia && !snap?.source_id){$('start-camera').disabled=true;$('shared-status').textContent='Use the HTTPS address on a tablet to enable its camera. HTTP supports viewing and shared controls.';}
  }
}
window.sharedRoom=new SharedRoom();
