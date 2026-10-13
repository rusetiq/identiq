'use strict';
class SharedRoom {
  constructor(){
    this.socket=null;this.snapshot=null;this.applying=false;this.connected=false;this.previewUrl=null;
    this.lastAttendance=-1;this.lastEpoch=-1;this.uploadTimer=null;this.canvas=document.createElement('canvas');
    this.retry=null;this.closed=false;this.connect();
    $('share-camera').onclick=()=>this.send({type:'claim',action:state.movementMode,axis:$('crossing-axis').value,direction:$('entry-direction').value});
    $('stop-sharing').onclick=()=>this.release();
    window.addEventListener('pagehide',()=>{this.closed=true;clearTimeout(this.retry);clearInterval(this.uploadTimer);this.socket?.close();this.clearPreview();});
  }
  get source(){return !!this.snapshot?.source_id && this.snapshot.source_id===this.snapshot.client_id;}
  connect(){
    const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/shared/ws`);this.socket=socket;
    socket.binaryType='blob';
    socket.onopen=()=>{this.connected=true;socket.send(JSON.stringify({type:'hello',token}));};
    socket.onmessage=event=>{
      if(event.data instanceof Blob){this.showPreview(event.data);return;}
      try{const message=JSON.parse(event.data);if(message.type==='error')toast(message.message);else if(message.type==='state')this.apply(message);}catch(error){toast('Could not read shared camera state.');}
    };
    socket.onclose=()=>{
      if(this.socket!==socket)return;
      this.connected=false;this.applying=true;if(this.source)stopScan();this.applying=false;
      this.snapshot=null;clearInterval(this.uploadTimer);this.uploadTimer=null;this.clearPreview();updateControls();
      if(!this.closed)this.retry=setTimeout(()=>this.connect(),2000);
    };
  }
  send(message){if(this.connected && this.socket.readyState===WebSocket.OPEN){this.socket.send(JSON.stringify(message));return true;}toast('Shared controls disconnected. Reconnecting…');return false;}
  toggleScan(){this.send({type:this.snapshot?.running?'pause':'start'});}
  configure(action,axis,direction){this.send({type:'configure',action,axis,direction});}
  scanContext(){return {shared_key:this.snapshot?.source_key,shared_epoch:this.snapshot?.epoch};}
  localStopped(){if(this.source && this.snapshot?.running && !this.applying)this.send({type:'pause'});}
  release(){if(this.source){clearInterval(this.uploadTimer);this.uploadTimer=null;this.send({type:'release'});}}
  apply(message){
    const wasSource=this.source;this.snapshot=message;this.applying=true;
    try{
      state.movementMode=message.action;$('crossing-axis').value=message.axis;$('entry-direction').value=message.direction;
      const vertical=message.axis==='y';$('entry-direction').options[0].textContent=vertical?'Top → bottom':'Left → right';$('entry-direction').options[1].textContent=vertical?'Bottom → top':'Right → left';
      if(wasSource && !this.source && state.scanSessionId)stopScan();
      if(this.source){
        this.clearPreview();
        if(!state.stream || state.mode!=='attendance'){this.send({type:'release'});return;}
        if(!this.uploadTimer){this.uploadTimer=setInterval(()=>this.publish(),200);this.publish();}
        if(message.running && (this.lastEpoch!==message.epoch || !state.scanning)){
          if(state.scanning)stopScan();this.lastEpoch=message.epoch;
          scan(message.session_id,true).catch(error=>{toast(error.message);this.localStopped();});
        }else if(!message.running && state.scanSessionId)stopScan();
      }else{
        clearInterval(this.uploadTimer);this.uploadTimer=null;
        if(!message.source_id || !message.ready)this.clearPreview();
      }
      if(this.lastAttendance!==message.attendance_revision){this.lastAttendance=message.attendance_revision;refreshAttendance().catch(error=>toast(error.message));}
      updateControls();liveTracker.render();
    }finally{this.applying=false;}
  }
  publish(){
    if(!this.source || !state.stream || !this.connected || state.mode!=='attendance')return;
    if(this.socket.bufferedAmount>350000)return;
    const video=$('video');if(!video.videoWidth || video.readyState<2)return;
    const canvas=this.canvas;canvas.width=640;canvas.height=Math.max(1,Math.min(720,Math.round(640*$('viewfinder').clientHeight/$('viewfinder').clientWidth)));
    const ctx=canvas.getContext('2d'),scale=Math.min(canvas.width/video.videoWidth,canvas.height/video.videoHeight),w=video.videoWidth*scale,h=video.videoHeight*scale;
    ctx.fillStyle='#24282e';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.save();ctx.translate(canvas.width,0);ctx.scale(-1,1);ctx.drawImage(video,(canvas.width-w)/2,(canvas.height-h)/2,w,h);ctx.restore();
    const overlay=$('overlay');if(overlay.width && overlay.height)ctx.drawImage(overlay,0,0,canvas.width,canvas.height);
    canvas.toBlob(blob=>{if(blob && this.source && this.connected && this.socket.bufferedAmount<350000)this.socket.send(blob);},'image/jpeg',.72);
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
    $('shared-status').textContent=!this.connected?'Shared controls disconnected. Reconnecting…':!snap?.source_id?'Enable this device’s camera, then choose Share this camera.':this.source?`Sharing this camera · ${snap.staff_tabs} staff tabs · ${snap.running?'Scanning':'Paused'}`:`Viewing shared camera · ${snap.staff_tabs} staff tabs · ${snap.ready?(snap.running?'Scanning':'Paused'):'Waiting for the camera'}`;
    $('share-camera').hidden=!!snap?.source_id;$('share-camera').disabled=!this.connected || !state.stream || !state.ready || state.mode!=='attendance' || state.enrolling;
    $('stop-sharing').hidden=!this.source;
    $('attendance-scan').disabled=$('fullscreen-scan').disabled=state.attendanceBusy || state.enrolling || !available || !state.activeAttendance;
    $('attendance-scan').textContent=$('fullscreen-scan').textContent=snap?.running?'Pause shared scan':'Start shared scan';
    if(!this.source && !state.stream){$('camera-label').textContent=available?'SHARED CAMERA':'CAMERA OFF';$('camera-dot').classList.toggle('off',!available);$('tracking-title').textContent=available?'Shared camera feed':'Camera off';$('tracking-detail').textContent=available?'Viewing the designated camera source. This device’s camera is off.':'Enable and share the laptop camera to begin.';if(snap?.source_id)result(snap.running?'Shared scan running':'Shared scan paused','Recognition runs on the camera source. Attendance updates appear below.');}
    if(snap?.source_id && !this.source){$('start-camera').disabled=true;$('attendance-flow').disabled=$('fullscreen-flow').disabled=state.enrolling || state.attendanceBusy;}
    if(!navigator.mediaDevices?.getUserMedia && !snap?.source_id){$('start-camera').disabled=true;$('shared-status').textContent='This HTTP page can view and control the shared feed. On the laptop, open http://localhost:8080, enable its camera and share it.';}
  }
}
window.sharedRoom=new SharedRoom();
