"use strict";
const $ = id => document.getElementById(id);
let token = document.querySelector('meta[name="local-token"]').content;
const state = { stream: null, mode: "attendance", scanning: false, enrolling: false, savingEnrollment: false, ready: false, people: [], activeAttendance: null, viewedAttendance: null, attendanceBusy: false, scanSessionId: null, phoneCapture: null, streak: 0, candidate: null, generation: 0, cameraGeneration: 0, cameraStarting: false, scanAbort: null, attendanceSessionCount: 0, movementMode:"automatic" };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let toastTimer;
const captureCanvas=document.createElement('canvas');
const trackingCanvas=document.createElement('canvas');
const liveTracker = new FaceTracker({video: $("video"), canvas: $("overlay"), capture: () => frame(trackingCanvas,640,state.mode==='enroll' ? .82 : .62),
  request: image => apiFrame("/api/analyze", {image,group:state.mode==='attendance' || state.mode==='verify' && !$("person-select").value}), onUpdate: updateControls, guide:()=>state.mode==="attendance" && state.activeAttendance && state.movementMode==="automatic" ? {axis:$("crossing-axis").value,direction:$("entry-direction").value} : null, pause:()=>document.hidden || state.savingEnrollment || state.scanning && (state.scanSessionId || !$("person-select").value)});
let cancelEnrollment = false;
const recentCheckIns=new Map();
const enrollmentFilters=new window.EnrollmentFilters({onChange:()=>selectEnrollment()});

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
}

const connectionIssues=new Set();
let offlineQueueCount=0,offlineSyncing=false,lastOfflineCaptureAt=0;
function renderConnectionStatus(){
  const banner=$('connection-status');banner.hidden=!connectionIssues.size&&!offlineQueueCount;
  if(offlineQueueCount){
    banner.textContent=`${navigator.onLine===false?'Network offline.':'Connection restored.'} ${offlineQueueCount} recent attendance frame${offlineQueueCount===1?' is':'s are'} saved on this tablet and will verify when scanning is paused.`;
  }else banner.textContent=navigator.onLine===false?'Network offline. Scanning will resume when the connection returns.':'Connection interrupted. Reconnecting automatically…';
}
function connectionStatus(channel,failed){
  if(failed)connectionIssues.add(channel);else connectionIssues.delete(channel);
  renderConnectionStatus();
}
const transientError=error=>window.IdentiqNetwork.transient(error);
async function refreshOfflineQueue(){
  try{offlineQueueCount=await window.IdentiqOfflineAttendance.count();renderConnectionStatus();}catch(error){}
}
async function saveOfflineAttendance(payload){
  if(!window.IdentiqOfflineAttendance || Date.now()-lastOfflineCaptureAt<1000)return false;
  lastOfflineCaptureAt=Date.now();
  try{
    offlineQueueCount=await window.IdentiqOfflineAttendance.add(payload);renderConnectionStatus();
    return true;
  }catch(error){return false;}
}
async function syncOfflineAttendance(){
  if(offlineSyncing || state.scanning || navigator.onLine===false || !window.IdentiqOfflineAttendance)return;
  offlineSyncing=true;let synced=0;
  try{
    for(const item of await window.IdentiqOfflineAttendance.entries()){
      try{
        await apiFrame('/api/recognize/group',item.payload);
        await window.IdentiqOfflineAttendance.remove([item.id]);synced++;
      }catch(error){
        if(transientError(error))break;
        // A finished session or expired sign-in must not leave biometric frames
        // on the tablet indefinitely.
        await window.IdentiqOfflineAttendance.remove([item.id]);
      }
    }
  }finally{
    offlineSyncing=false;await refreshOfflineQueue();
    if(synced){await refreshAttendance().catch(()=>{});toast(`${synced} offline attendance capture${synced===1?'':'s'} verified.`);}
  }
}
let refreshingToken=null,signingOut=false;
async function refreshClientToken(){
  if(!refreshingToken)refreshingToken=api('/api/client-context').then(context=>{token=context.token;return token;}).finally(()=>refreshingToken=null);
  return refreshingToken;
}
function expiredSession(){
  if(signingOut)return;signingOut=true;window.sharedRoom?.shutdown();stopCamera();window.location.assign('/?expired=1');
}
function compactFrameForm(data){
  const {image,...metadata}=data;
  const encoded=image.slice(image.indexOf(',')+1),raw=atob(encoded),bytes=new Uint8Array(raw.length);
  for(let index=0;index<raw.length;index++)bytes[index]=raw.charCodeAt(index);
  const form=new FormData();form.append('payload',JSON.stringify(metadata));form.append('image',new Blob([bytes],{type:'image/jpeg'}),'frame.jpg');
  return form;
}
function apiFrame(path,data,signal){return api(path,'POST',data,signal,false,true);}
async function api(path, method = "GET", data, signal, refreshed=false, frameUpload=false) {
  try{
    const body=await window.IdentiqNetwork.request(path,{method,body:frameUpload?compactFrameForm(data):data,formData:frameUpload,signal,headers:{"X-Local-Token":token},timeoutMs:path.startsWith("/api/enroll")?60000:10000});
    connectionStatus('api',false);return body;
  }catch(error){
    if(error.status===401){expiredSession();throw error;}
    if(error.status===403 && error.code==='invalid_token' && !refreshed){
      await refreshClientToken();
      if(signal?.aborted)throw Object.assign(new Error('Scan cancelled.'),{code:'cancelled'});
      return api(path,method,data,signal,true,frameUpload);
    }
    if(transientError(error))connectionStatus('api',true);
    throw error;
  }
}

function updateControls() {
  $("attendance-flow").disabled=$("fullscreen-flow").disabled=state.enrolling || state.attendanceBusy;
  $("fullscreen-flow").value=$("attendance-flow").value=state.movementMode;
  $("direction-settings").hidden=state.movementMode!=="automatic";
  $("movement-instructions").textContent=state.movementMode==="automatic" ? "Keep your face clear while crossing the line. Movement requires a confirmed identity and a clear crossing." : state.movementMode==="check_out" ? "Exit camera mode: confirm your face across fresh frames to check out after a recorded arrival." : "Entry camera mode: hold still for confirmation across fresh frames before check-in.";
  document.querySelectorAll('#attendance-roster button').forEach(button=>button.disabled=state.attendanceBusy || button.dataset.unavailable==='true');
  $("delete-attendance").disabled=state.attendanceBusy || state.enrolling || !state.attendanceSessionCount;
  $("fullscreen-scan").hidden=state.mode!=="attendance" || !state.activeAttendance;
  $("fullscreen-scan").disabled=state.attendanceBusy || state.enrolling || !state.stream || !state.ready;
  $("fullscreen-scan").textContent=state.scanning && state.scanSessionId ? "Pause attendance scan" : "Start movement scan";
  document.querySelectorAll("#attendance-roster select").forEach(select=>{select.disabled=state.attendanceBusy || state.enrolling;});
  $("start-session").disabled = state.attendanceBusy || state.enrolling || !!state.activeAttendance || !state.people.length;
  $("session-name").disabled = state.attendanceBusy || !!state.activeAttendance;
  $("attendance-scan").disabled = state.attendanceBusy || state.enrolling || !state.stream || !state.ready || !state.activeAttendance;
  $("end-session").disabled = state.attendanceBusy || state.enrolling;
  $("attendance-tab").disabled = state.enrolling;
  $("recognize").disabled = !state.stream || !state.ready || !state.people.some(person=>person.compatible_samples>0) || state.enrolling;
  $("capture").disabled = !state.stream || !state.ready || state.enrolling || liveTracker.readyStreak < 2 || !$("enroll-person").value;
  $("stop-camera").disabled = !state.stream && !state.cameraStarting;
  $("stop-camera").textContent = state.cameraStarting && !state.stream ? "Cancel connection" : "Turn off camera";
  $("start-camera").disabled = !!state.stream || state.cameraStarting;
  $("person-select").disabled = state.scanning;
  $("verify-tab").disabled = state.enrolling;
  $("enroll-tab").disabled = state.enrolling;
  $("person-name").disabled = $("enroll-person").disabled = state.enrolling;
  for(const id of ["enroll-grade","enroll-section","enroll-search"])$(id).disabled=state.enrolling;
  $("consent").disabled = state.enrolling;
  $("add-person").disabled = state.enrolling;
  $("laptop-source").disabled = $("iphone-source").disabled = state.enrolling;
  $("iphone-file").disabled = $("iphone-target").disabled = $("iphone-consent").disabled = state.enrolling;
  $("import-iphone").disabled = state.enrolling || !state.ready || !state.phoneCapture || !$("iphone-target").value;
  window.sharedRoom?.renderControls();
}

function result(title, detail, kind = "", score = null) {
  for (const prefix of ["result", "attendance-result"]) {
    $(prefix).className = `result ${kind}`;
    $(`${prefix}-title`).textContent = title;
    $(`${prefix}-detail`).textContent = detail;
    $(`${prefix}-icon`).textContent = kind === "matched" ? "✓" : kind === "warning" ? "!" : "⌁";
  }
  $("score-row").hidden = score === null;
  if (score !== null) $("score-value").textContent = `${score.toFixed(3)}${Number.isFinite(state.matchThreshold) ? ` / threshold ${state.matchThreshold.toFixed(3)}` : ''}`;
}

function clearBox() {
  recentCheckIns.clear();
  $("camera-names").replaceChildren();$("camera-names").hidden=true;
  liveTracker.highlight(null, false);
}

function drawBox(box, matched, name) {
  liveTracker.highlightGroup([{box,matched,label:name}]);
  showCameraNames(name ? [{name,detail:matched ? "Verified" : "Confirming match",matched}] : []);
}

function frame(canvas=captureCanvas, maxSide=960, jpegQuality=.86) {
  const video = $("video");
  if (!state.stream || !video.videoWidth || video.readyState < 2) throw new Error("The camera is not ready yet.");
  const ratio = Math.min(1, maxSide / video.videoWidth, maxSide / video.videoHeight);
  const width=Math.round(video.videoWidth*ratio),height=Math.round(video.videoHeight*ratio);
  if(canvas.width!==width || canvas.height!==height){canvas.width=width;canvas.height=height;}
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const image=canvas.toDataURL("image/jpeg", jpegQuality);
  return image.length>1900000 ? canvas.toDataURL("image/jpeg", .70) : image;
}

async function startCamera() {
  if(state.cameraStarting || state.stream) return;
  state.cameraStarting=true;const cameraGeneration=++state.cameraGeneration;
  $("camera-label").textContent="CONNECTING";updateControls();
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera capture requires localhost or HTTPS. Open http://localhost:8080 on the laptop to share its camera.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: "user", width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 15 } } });
    if(cameraGeneration!==state.cameraGeneration){stream.getTracks().forEach(track=>track.stop());return;}
    state.stream=stream;$("video").srcObject = stream;
    await $("video").play();
    if(cameraGeneration!==state.cameraGeneration) return;
    $("video").classList.add("on");
    $("camera-empty").hidden = true;
    $("viewfinder").classList.add("live");
    $("camera-dot").classList.remove("off");
    $("camera-label").textContent = "CAMERA CONNECTED";
    $("feed-resolution").textContent = `${$("video").videoWidth} × ${$("video").videoHeight}`;
    $("enroll-message").textContent = "Select a school record, confirm permission, then capture five samples.";
    result(state.people.length ? "Camera ready" : "No enrolled faces yet", !state.people.length ? "Open Enroll to save your first face." : state.mode === "attendance" ? state.activeAttendance ? "Start attendance scan to mark people present." : "Start a session to take attendance." : "Choose a person or recognize anyone enrolled.");
    for (const track of state.stream.getVideoTracks()) track.onended = () => stopCamera();
    liveTracker.start();
  } catch (error) {
    if(cameraGeneration!==state.cameraGeneration) return;
    const messages = { NotAllowedError: "Camera access was denied. Allow camera access in your browser’s site settings, then try again.", NotFoundError: "No camera found. Connect a webcam and try again.", NotReadableError: "Your camera is busy. Close other apps using it and try again." };
    if (state.stream) stopCamera();
    const message = messages[error.name] || error.message;
    toast(message); result("Camera unavailable", message, "warning");
  } finally {if(cameraGeneration===state.cameraGeneration) state.cameraStarting=false; updateControls(); }
}

function stopScan() {
  state.scanAbort?.abort();state.scanAbort=null;
  state.scanning = false;cameraWakeLock?.release().catch(()=>{});cameraWakeLock=null;
  state.scanSessionId = null;
  state.generation++;
  state.streak = 0;
  state.candidate = null;
  $("recognize").textContent = "Start recognition";
  $("attendance-scan").textContent = "Start movement scan";
  $("feed-label").textContent = "LIVE PREVIEW";
  clearBox();updateControls();
  $("scan-face-results").replaceChildren();$("scan-face-results").hidden=true;
  window.sharedRoom?.localStopped();
  setTimeout(()=>syncOfflineAttendance(),0);
}

function stopCamera() {
  window.sharedRoom?.release();
  state.cameraGeneration++;state.cameraStarting=false;
  cancelEnrollment = true;
  liveTracker.stop();
  stopScan();
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  state.stream = null;
  $("video").srcObject = null;
  $("video").classList.remove("on");
  $("camera-empty").hidden = false;
  $("viewfinder").classList.remove("live");
  $("camera-label").textContent = "CAMERA OFF";
  $("camera-dot").classList.add("off");
  $("feed-resolution").textContent = "— × —";
  result("Ready when you are", "Enable your camera to begin.");
  updateControls();
}

function setMode(mode) {
  if (state.enrolling) return;
  stopScan();state.mode = mode;liveTracker.invalidate();
  if(mode!=="attendance")window.sharedRoom?.release();
  for (const current of ["attendance", "verify", "enroll"]) {
    $(`${current}-panel`).hidden = current !== mode;
    $(`${current}-tab`).classList.toggle("active", current === mode);
    $(`${current}-tab`).setAttribute("aria-selected", String(current === mode));
  }
  $("camera-hint").textContent = mode === "enroll" ? "One person only. Look forward, then turn slightly between samples." : `Up to ${$("camera-hint").dataset.maxFaces} faces together. Keep every face clear and facing the camera.`;
  updateControls();
}

async function refreshPeople() {
  const selected = $("person-select").value;
  state.people = (await api("/api/people")).people;
  document.dispatchEvent(new CustomEvent('people-refreshed',{detail:state.people}));
  $("people-count").textContent = state.people.length;
  const chosenClass=$('session-class').value;
  $('session-class').replaceChildren(new Option('All registered classes',''));
  for(const name of [...new Set(state.people.map(p=>p.class_name).filter(Boolean))].sort()) $('session-class').add(new Option(name,name));
  $('session-class').value=chosenClass;
  enrollmentFilters.refresh(state.people);
  const year=$("session-year").value;$("session-year").replaceChildren(new Option("All years",""));
  for(const value of [...new Set(state.people.map(person=>person.academic_year).filter(Boolean))].sort())$("session-year").add(new Option(value,value));
  $("session-year").value=year;
  $("person-select").replaceChildren(new Option("Recognize anyone enrolled", ""));
  state.people.filter(person=>person.compatible_samples>0).forEach(person => $("person-select").add(new Option(person.name, person.id)));
  if (state.people.some(person => String(person.id) === selected)) $("person-select").value = selected;
  $("people-list").replaceChildren();
  if (!state.people.length) {
    const empty = document.createElement("div");empty.className = "directory-empty";
    empty.innerHTML = "<strong>No school records yet.</strong><p>Add a school record, then enroll their face.</p>";
    $("people-list").append(empty);
  }
  for (const person of state.people) {
    const row = document.createElement("div");row.className = "person-row";
    const avatar = document.createElement("div");avatar.className = "avatar";
    avatar.textContent = person.name.split(/\s+/u).slice(0,2).map(part=>Array.from(part)[0]).join("").toUpperCase();
    const info = document.createElement("div");info.className = "person-info";
    const name = document.createElement("strong");name.textContent = person.name;
    const meta = document.createElement("p");
    const enrollment=person.compatible_samples ? `${person.compatible_samples} ArcFace samples` : person.samples ? 'Re-enroll for ArcFace' : 'No face samples';
    meta.textContent = `ID ${String(person.id).padStart(3,"0")} · ${enrollment}${person.iphone_samples ? " · iPhone added" : ""} · Added ${new Date(person.created_at).toLocaleDateString(undefined,{month:"short",day:"numeric"})}`;
    info.append(name,meta);
    const school=document.createElement('p');school.textContent=[person.person_type==='staff' ? `Staff · ${person.employee_code}` : person.student_id && `Admission: ${person.student_id}`,person.class_name,person.academic_year].filter(Boolean).join(' · ');info.append(school);
    const edit=document.createElement('button');edit.className='text-button';edit.textContent='Edit details';edit.setAttribute('aria-label',`Edit details for ${person.name}`);edit.onclick=()=>openStudentProfile(person);
    const status = document.createElement("span");status.className = "person-status";status.textContent=person.compatible_samples ? 'FACE ENROLLED' : person.samples ? 'RE-ENROLL REQUIRED' : 'AWAITING FACE';
    const remove = document.createElement("button");remove.className = "text-button";remove.textContent = "Remove";remove.setAttribute("aria-label",`Remove ${person.name}`);
    remove.onclick = () => askDelete(person);
    const capture=document.createElement('button');capture.className='text-button';capture.textContent=person.compatible_samples ? 'Enroll face' : person.samples ? 'Re-enroll face' : 'Enroll face';capture.hidden=person.compatible_samples>0;capture.onclick=()=>{setMode('enroll');setSource('laptop');enrollmentFilters.select(person);$('enroll-panel').scrollIntoView({behavior:'smooth',block:'center'});};
    row.append(avatar,info,status,capture,edit,remove);$("people-list").append(row);
  }
  updateControls();
}
window.addEventListener('database-merged',()=>refreshPeople().catch(error=>toast(error.message)));

async function scan(attendanceSessionId = null, sharedExecution = false) {
  if(attendanceSessionId && !sharedExecution && window.sharedRoom)return window.sharedRoom.toggleScan();
  if (state.scanning) { const attendanceScan = !!state.scanSessionId; stopScan();result(attendanceScan ? "Attendance scan paused" : "Recognition paused", "Start again when you’re ready.");return; }
  state.scanning = true;maintainCameraAwake();
  const scanAbort=new AbortController();state.scanAbort=scanAbort;
  state.scanSessionId = attendanceSessionId;
  const scannerId = sharedExecution ? window.sharedRoom.snapshot.scanner_id : crypto.randomUUID();
  const sharedContext = sharedExecution ? window.sharedRoom.scanContext() : {};
  const group = !!attendanceSessionId || !$("person-select").value;
  const groupStreaks = new Map();
  const generation = ++state.generation;
  let failures=0;
  const attendancePayload=image=>({image,person_id:null,...sharedContext,attendance_session_id:attendanceSessionId,scanner_id:scannerId,attendance_action:state.movementMode,crossing_axis:$("crossing-axis").value,entry_direction:$("entry-direction").value});
  // Keep only the newest frame while inference is running. A deeper queue
  // makes a slow tablet process old camera images and feel increasingly late.
  // Frames are never written to disk and are discarded when scanning stops.
  const frameQueue=[];
  const queueFrame=()=>{
    if(!attendanceSessionId || !state.scanning || !state.stream || frameQueue.length)return;
    try {
      const image=frame(captureCanvas,800,.72);
      if(state.scanning && generation===state.generation && frameQueue.length<5)frameQueue.push({image,capturedAt:performance.now()});
    } catch(error) {}
  };
  const queueTimer=attendanceSessionId?setInterval(queueFrame,300):null;
  const offlineTimer=attendanceSessionId&&!sharedExecution?setInterval(()=>{
    if(!connectionIssues.has('api'))return;
    try{saveOfflineAttendance(attendancePayload(frame(captureCanvas,800,.72)));}catch(error){}
  },1000):null;
  if(attendanceSessionId)queueFrame();
  $("recognize").textContent = "Pause recognition";
  if (attendanceSessionId) $("attendance-scan").textContent = "Pause attendance scan";
  $("feed-label").textContent = attendanceSessionId ? "TAKING ATTENDANCE" : "RECOGNIZING";
  updateControls();
  try {
    while (state.scanning && generation === state.generation && state.stream) {
      let recognitionPayload=null;
      try {
      const selected = $("person-select").value;
      if(attendanceSessionId && !frameQueue.length)queueFrame();
      const queued=attendanceSessionId?frameQueue.shift():null;
      const capturedAt=queued?.capturedAt ?? performance.now(),image=queued?.image ?? frame(captureCanvas,attendanceSessionId ? 800 : 800,.72);
      const requestStarted=performance.now();
      recognitionPayload=attendanceSessionId?attendancePayload(image):{image,person_id:selected ? Number(selected) : null};
      const data = await apiFrame(group ? "/api/recognize/group" : "/api/recognize", recognitionPayload, scanAbort.signal);
      if (!state.scanning || generation !== state.generation) break;
      window.IdentiqScan?.record(data,performance.now()-requestStarted);
      state.matchThreshold=data.threshold;
      failures=0;
      if(group) {
        liveTracker.accept(data,image,capturedAt);await showGroupResults(data,groupStreaks,attendanceSessionId);
        const delay=frameQueue.length ? 0 : window.IdentiqScan?.delay(data,!!attendanceSessionId) ?? (attendanceSessionId ? (data.face_count ? 250 : 600) : 650);
        if(delay)await sleep(delay);
        continue;
      }
      if (data.status === "matched") {
        state.streak = state.candidate === data.person.id ? state.streak + 1 : 1;
        state.candidate = data.person.id;
        const confirmed = attendanceSessionId ? data.attendance?.status === "present" : state.streak >= 3;
        const provisional=attendanceSessionId && data.attendance?.status==='confirming';
        result(confirmed || provisional ? data.person.name : "Confirming match…", confirmed ? `Identity ${selected ? "verified" : "recognized"} · Database ID ${data.person.id}` : provisional ? `Provisional match · ${data.attendance.confirmations} of ${data.attendance.required_confirmations} confirmations · not marked present yet` : `${state.streak} of 3 consecutive matches`, confirmed ? "matched" : "", data.similarity);
        if (attendanceSessionId) {
          const attendance = data.attendance;
          if (attendance.status === "present") {
            result(data.person.name, "Marked present for this session.", "matched", data.similarity);
            if (attendance.newly_present) { toast(`${data.person.name} marked present.`); await refreshAttendance(attendanceSessionId); }
          } else if (attendance.status === "confirming") result(data.person.name, `Provisional match · ${attendance.confirmations} of ${attendance.required_confirmations} confirmations · not marked present yet`, "", data.similarity);
          else result("Face needs review", attendance.message, "warning", data.similarity);
        }
        drawBox(data.box, confirmed, confirmed || provisional ? data.person.name : "Confirming face…");
      } else {
        state.streak = 0;state.candidate = null;
        result({unknown:"Unknown face",ambiguous:"Uncertain match",mismatch:"Identity mismatch",empty:"No enrolled faces"}[data.status] || "Try again",data.message,"warning",data.similarity ?? null);
        drawBox(data.box,false);
      }
      } catch (error) {
      if (!state.scanning || generation !== state.generation) break;
      if(transientError(error)){
        if(attendanceSessionId && recognitionPayload && !sharedExecution){
          const saved=await saveOfflineAttendance(recognitionPayload);
          if(saved)result('Offline capture saved','It will be verified by the attendance laptop after the connection returns.');
        }
        clearBox();groupStreaks.clear();result('Reconnecting…','Scan remains enabled. Waiting for the server.');
        await sleep(Math.min(15000,500*2**Math.min(++failures,5)));continue;
      }
      if(error.code==='shared_scan_stopped'){stopScan();result("Shared scan paused","Shared controls paused or changed the scan.");break;}
      state.streak = 0;state.candidate = null;clearBox();
      groupStreaks.clear();showCameraNames([]);$("scan-face-results").replaceChildren();$("scan-face-results").hidden=true;
      result("Adjust your camera view",error.message,"warning");
      if (!["no_face","multiple_faces","blurry","too_small","uncertain_detection","quality"].includes(error.code)) { stopScan();toast(error.message);break; }
      }
      await sleep(attendanceSessionId && frameQueue.length ? 0 : attendanceSessionId ? 40 : 650);
    }
  } finally {
    if(queueTimer)clearInterval(queueTimer);
    if(offlineTimer)clearInterval(offlineTimer);
    frameQueue.length=0;
  }
}

async function showGroupResults(data,streaks,sessionId) {
  const holder=$("scan-face-results");holder.replaceChildren();holder.hidden=true;
  const seen=new Set(data.faces.filter(face=>face.status==='matched').map(face=>face.person.id));
  for(const id of streaks.keys()) if(!seen.has(id)) streaks.delete(id);
  let confirmed=0,confirming=0;const newlyPresent=[],highlights=[];
  for(const [index,face] of data.faces.entries()) {
    if(["unknown","empty"].includes(face.status)) continue;
    let name=`Face ${index+1}`,detail=face.message || 'No attendance marked.',matched=false;
    if(face.status==='matched') {
      name=face.person.name;streaks.set(face.person.id,(streaks.get(face.person.id) || 0)+1);
      matched=sessionId ? ['present','checked_out'].includes(face.attendance.status) || face.attendance.campus_state==='on_campus' && face.attendance.status==='tracking' : streaks.get(face.person.id)>=3;
      if(matched) {confirmed++;detail=sessionId ? face.attendance.status==='manual_override' ? 'Present · staff correction' : face.attendance.campus_state==='checked_out' ? 'Checked out' : 'Checked in' : 'Recognized';}
      else if(!sessionId || face.attendance.status==='confirming') {confirming++;detail=`${sessionId ? face.attendance.confirmations : streaks.get(face.person.id)} of ${sessionId ? face.attendance.required_confirmations : 3} fresh matches · hold still`;}
      else detail=face.attendance.status==='manual_override' ? `Staff marked ${face.attendance.attendance_status}. Camera status kept unchanged.` : face.attendance.message;
      if(!matched) {
        if(sessionId && face.attendance?.status==='confirming') {
          name=face.person.name;
          detail=`Provisional match · ${face.attendance.confirmations} of ${face.attendance.required_confirmations} confirmations · not marked present yet`;
        } else name=!sessionId ? 'Confirming face…' : 'Face needs review';
      }
      if(face.attendance?.newly_moved || face.attendance?.newly_present) newlyPresent.push(`${name} ${face.attendance.action==='check_out' ? 'checked out' : 'checked in'}`);
    } else if(face.status==='ambiguous') name='Uncertain match';
    const row=document.createElement('div');row.className=`scan-face-row ${matched?'confirmed':face.status==='matched'?'confirming':'warning'}`;
    const title=document.createElement('strong');title.textContent=name;const text=document.createElement('span');text.textContent=detail;
    if(face.person)row.dataset.personId=face.person.id;
    row.append(title,text);holder.append(row);highlights.push({box:face.box,matched,status:face.status,label:name});
  }
  holder.hidden=!holder.children.length;
  showCameraNames(Array.from(holder.children).map(row=>({id:row.dataset.personId,name:row.querySelector("strong").textContent,detail:row.querySelector("span").textContent,matched:row.classList.contains("confirmed")})),sessionId);
  liveTracker.highlightGroup(highlights);
  const visible=holder.children.length;
  result(visible?`${confirmed} ${sessionId?'movement records':'recognized'}`:'Ready for the next person',visible?`${confirming} confirming · ${visible-confirmed-confirming} need review`:state.movementMode==='automatic' ? 'Waiting for an enrolled person to cross the line.' : 'Waiting for an enrolled person.',confirmed?'matched':'');
  if(newlyPresent.length) {toast(newlyPresent.join(', '));refreshAttendance(sessionId).catch(error=>{if(!transientError(error))toast(error.message);});}
}

async function enroll(event) {
  event.preventDefault();
  if (state.enrolling || !state.stream) return;
  const name = $("person-name").value.trim();
  const personId=Number($("enroll-person").value);
  if (!name || !personId || !$("consent").checked) return;
  stopScan();state.enrolling = true;cancelEnrollment = false;$("cancel-capture").hidden = false;updateControls();
  const samples = [];
  const markers = [...document.querySelectorAll(".samples span")];
  markers.forEach((marker,i) => {marker.classList.remove("done");marker.textContent=i+1;});
  $("enroll-message").classList.remove("error");
  let completed = false;
  try {
    let lastAccepted = 0, lastSampleId = -1;
    let lastIssue = "Waiting for a stable face.";
    const deadline = performance.now() + 90000;
    while (samples.length < 5 && performance.now() < deadline) {
      if (cancelEnrollment || !state.stream) throw Object.assign(new Error("Capture cancelled. Your face patterns were not saved."), {code: "cancelled"});
      const current = liveTracker.analysis;
      if (!current) lastIssue = $("tracking-detail").textContent;
      if (current && performance.now() - current.time < 1400) {
        lastIssue = current.data.message;
        if (current.data.status === "ready" && liveTracker.readyStreak >= 2 && current.id !== lastSampleId && performance.now() - lastAccepted >= 800) {
          samples.push(current.image);lastSampleId = current.id;lastAccepted = performance.now();
          markers[samples.length-1].classList.add("done");markers[samples.length-1].textContent="✓";
        }
      }
      $("enroll-message").textContent = `${samples.length} / 5 samples captured. ${lastIssue} ${samples.length < 2 ? "Look straight ahead." : "Keep facing the camera; a slight turn is enough."}`;
      await sleep(100);
    }
    if (samples.length !== 5) throw new Error(`${samples.length} / 5 samples captured. ${lastIssue} Capture timed out; adjust the camera view and try again.`);
    if (cancelEnrollment || !state.stream) throw Object.assign(new Error("Capture cancelled. Your face patterns were not saved."), {code: "cancelled"});
    $("cancel-capture").hidden = true;
    state.savingEnrollment=true;
    $("enroll-message").textContent="Saving face patterns to your local database. Keep this page open…";
    const saved = await api("/api/enroll", "POST", {name,person_id:personId,consent:true,images:samples});
    await refreshPeople();
    $("person-name").value="";$("consent").checked=false;
    $("enroll-message").textContent=`${saved.person.name} is enrolled. You can now recognize their face.`;
    toast(`${saved.person.name} enrolled successfully.`);completed=true;
  } catch (error) {
    $("enroll-message").textContent=error.message+(transientError(error)?" Check this school record before capturing again; the server may have saved it.":"");$("enroll-message").classList.add("error");

  } finally {
    samples.length=0;state.enrolling=false;state.savingEnrollment=false;$("cancel-capture").hidden = true;clearBox();updateControls();
    if (completed) {setMode("verify");result("Enrollment complete","Start recognition to retrieve the saved name.");}
  }
}

let deletePerson=null;
function askDelete(person) {
  if (state.enrolling) return;
  stopScan();deletePerson=person;
  $("delete-description").textContent=`This removes ${person.name} and all their saved face patterns from this device. Saved attendance keeps their name and status.`;
  $("delete-dialog").showModal();
}
$("cancel-delete").onclick=()=>$("delete-dialog").close();
$("confirm-delete").onclick=async()=>{
  if (!deletePerson) return;
  $("confirm-delete").disabled=true;
  try {await api(`/api/people/${deletePerson.id}`,"DELETE");await refreshPeople();$("delete-dialog").close();result("Directory updated","Start recognition when you’re ready.");toast("Person and face patterns removed.");}
  catch(error){toast(error.message);}finally{$("confirm-delete").disabled=false;}
};
function showCameraNames(people,sessionId=null) {
  if(sessionId){
    const now=performance.now();
    for(const [id,item] of recentCheckIns) if(now-item.time>3000)recentCheckIns.delete(id);
    for(const person of people)if(person.matched)recentCheckIns.set(person.id,{...person,time:now});
    const current=new Set(people.map(person=>person.id));
    people=[...people,...Array.from(recentCheckIns.values()).filter(person=>!current.has(person.id)).map(person=>({...person,detail:person.detail}))];
  }
  const holder=$("camera-names");holder.replaceChildren();holder.hidden=!people.length;
  for(const person of people){const badge=document.createElement('div');badge.className=`camera-name ${person.matched?'confirmed':'review'}`;const name=document.createElement('strong'),detail=document.createElement('span');name.textContent=person.name;detail.textContent=person.detail;badge.append(name,detail);holder.append(badge);}
}
let nativeCameraFullscreen=false;
function cameraFullscreenElement(){return document.fullscreenElement || document.webkitFullscreenElement;}
function setCameraFullscreen(expanded){
  document.querySelectorAll('main > :not(.workspace), .workspace > .controls').forEach(element=>element.inert=expanded);
  $('camera-card').classList.toggle('camera-expanded',expanded);document.body.classList.toggle('camera-expanded-open',expanded);
  $('camera-fullscreen').textContent=expanded?'Exit fullscreen':'Fullscreen camera';
  $('camera-fullscreen').setAttribute('aria-pressed',String(expanded));
  requestAnimationFrame(()=>liveTracker.render());
  if(!expanded)$('camera-fullscreen').focus();
}
async function closeCameraFullscreen(){
  if(cameraFullscreenElement()===$('camera-card')){
    const exit=document.exitFullscreen || document.webkitExitFullscreen;
    try{if(exit)await exit.call(document);}catch(error){/* The browser may already have exited. */}
  }
  nativeCameraFullscreen=false;setCameraFullscreen(false);
}
$('camera-fullscreen').onclick=async()=>{
  const card=$('camera-card');
  if(card.classList.contains('camera-expanded')){await closeCameraFullscreen();return;}
  setCameraFullscreen(true);
  try{
    if(card.requestFullscreen){await card.requestFullscreen({navigationUI:'hide'});nativeCameraFullscreen=true;}
    else if(card.webkitRequestFullscreen){await card.webkitRequestFullscreen();nativeCameraFullscreen=true;}
    else toast('This browser supports an expanded camera view only.');
  }catch(error){toast('Browser fullscreen was unavailable. The camera view is expanded.');}
  requestAnimationFrame(()=>liveTracker.render());
};
for(const event of ['fullscreenchange','webkitfullscreenchange'])document.addEventListener(event,()=>{
  if(cameraFullscreenElement()===$('camera-card')){nativeCameraFullscreen=true;setCameraFullscreen(true);}
  else if(nativeCameraFullscreen){nativeCameraFullscreen=false;setCameraFullscreen(false);}
});
document.addEventListener('keydown',event=>{if(event.key==='Escape' && $('camera-card').classList.contains('camera-expanded'))closeCameraFullscreen();});
new ResizeObserver(()=>liveTracker.render()).observe($("viewfinder"));
$("fullscreen-scan").onclick=()=>{if(state.activeAttendance)scan(state.activeAttendance.id);};
$("start-camera").onclick=startCamera;
$("stop-camera").onclick=stopCamera;
$("recognize").onclick=()=>scan();
$("attendance-scan").onclick=()=>{if(state.activeAttendance) scan(state.activeAttendance.id);};
$("attendance-tab").onclick=()=>setMode("attendance");
$("enroll-form").onsubmit=enroll;
$("verify-tab").onclick=()=>setMode("verify");
$("enroll-tab").onclick=()=>setMode("enroll");
for (const mode of ["attendance", "verify", "enroll"]) {
  $(`${mode}-tab`).addEventListener("keydown", event => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const modes = ["attendance", "verify", "enroll"];
      const target = event.key === "Home" ? "attendance" : event.key === "End" ? "enroll" : modes[(modes.indexOf(mode) + (event.key === "ArrowLeft" ? 2 : 1)) % 3];
      setMode(target);$(`${target}-tab`).focus();
    }
  });
}
$("person-select").onchange=()=>{stopScan();result("Ready to verify","Start recognition to check the selected identity.");};
$("add-person").onclick=()=>openStudentProfile();
function selectEnrollment(){const person=state.people.find(person=>String(person.id)===$("enroll-person").value);$("person-name").value=person?.name || "";updateControls();}
$("enroll-person").onchange=selectEnrollment;

window.addEventListener("pagehide",stopCamera);
let cameraWakeLock=null;
async function maintainCameraAwake(){
  if(!navigator.wakeLock || document.hidden || !state.scanning || cameraWakeLock)return;
  try{cameraWakeLock=await navigator.wakeLock.request('screen');cameraWakeLock.addEventListener('release',()=>cameraWakeLock=null);}catch(error){}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)maintainCameraAwake();});
async function initializeApp(){
  $("camera-dot").classList.add("off");
  await refreshOfflineQueue();
  try {
    const status=await api("/api/status");state.ready=status.models_ready;
    await refreshPeople();
    await refreshAttendance();
    if(!state.ready){toast("Face models are missing. Run python scripts/download_models.py.");result("Setup needed","Download the face models, then reload this page.","warning");}
    else if(status.reenrollment_required){result("Re-enrollment required",`${status.reenrollment_required} profile${status.reenrollment_required===1?'':'s'} still use the previous model. Select each profile and enroll new face samples before scanning.`,"warning");}
    else if(state.activeAttendance) result("Session in progress","Enable the camera and start attendance scan to continue.");
    else if(state.people.length) result("Ready to take attendance","Name your session, then click Start session.");
  }catch(error){
    if(transientError(error)){result('Reconnecting…','Waiting for the attendance laptop.');setTimeout(initializeApp,5000);}
    else{toast(error.message);result('Setup needs attention',error.message,'warning');}
  }
  updateControls();
}
initializeApp();
window.addEventListener('online',()=>{renderConnectionStatus();setTimeout(()=>syncOfflineAttendance(),500);});
window.addEventListener('offline',renderConnectionStatus);

function setSource(source) {
  if (state.enrolling) return;
  stopScan();
  $("laptop-enroll").hidden = source !== "laptop";
  $("iphone-enroll").hidden = source !== "iphone";
  for (const current of ["laptop", "iphone"]) {
    $(`${current}-source`).classList.toggle("selected", current === source);
    $(`${current}-source`).setAttribute("aria-pressed", String(current === source));
  }
}
$("laptop-source").onclick = () => setSource("laptop");
$("iphone-source").onclick = () => setSource("iphone");
$("iphone-file").onchange = async () => {
  state.phoneCapture = null;
  $("iphone-message").classList.remove("error");
  try {
    const file = $("iphone-file").files[0];
    if (!file) { $("iphone-file-detail").textContent = "Uses the front TrueDepth camera."; return; }
    if (file.size > 11 * 1024 * 1024) throw new Error("Capture file is too large (maximum 11 MB).");
    const capture = JSON.parse(await file.text());
    if (capture?.format !== "identiq.capture" || capture.version !== 1 || typeof capture.name !== "string") throw new Error("Choose a capture file exported by the Identiq iPhone app.");
    state.phoneCapture = capture;
    $("iphone-file-detail").textContent = `${capture.name.slice(0,80)} · ${Array.isArray(capture.samples) ? capture.samples.length : 0} samples`;
    $("iphone-message").textContent = "Ready to import. Select their school record to save phone samples.";
  } catch (error) {
    $("iphone-file-detail").textContent = "No valid file selected.";
    $("iphone-message").textContent = error.message;
    $("iphone-message").classList.add("error");
  } finally { updateControls(); }
};
$("iphone-form").onsubmit = async event => {
  event.preventDefault();
  if (!state.phoneCapture || !$("iphone-target").value || !$("iphone-consent").checked || state.enrolling) return;
  stopScan(); state.enrolling = true; state.savingEnrollment=true; updateControls();
  $("iphone-message").classList.remove("error");
  $("iphone-message").textContent = "Checking face samples and saving patterns…";
  let completed = false;
  try {
    const selected = $("iphone-target").value;
    const saved = await api("/api/enroll/iphone", "POST", {capture: state.phoneCapture, consent: true, person_id: selected ? Number(selected) : null});
    await refreshPeople();
    state.phoneCapture = null; $("iphone-file").value = ""; $("iphone-consent").checked = false;
    $("iphone-file-detail").textContent = "Uses the front TrueDepth camera.";
    $("iphone-message").textContent = "Phone samples saved.";
    toast(`Phone samples saved for ${saved.person.name}.`); completed = true;
  } catch (error) {
    $("iphone-message").textContent = error.message; $("iphone-message").classList.add("error");
  } finally {
    state.enrolling = false; state.savingEnrollment=false; updateControls();
    if (completed) {setMode("verify"); result("Phone enrollment saved", "Enable the laptop camera and start recognition.");}
  }
};

$("cancel-capture").onclick=()=>{cancelEnrollment=true;};
$("show-mesh").onchange=()=>liveTracker.render();

let attendanceRequest = 0, attendanceSignature='', attendancePolling=false;
async function refreshAttendance(preferredId = null) {
  const requestId = ++attendanceRequest;
  const viewedId=preferredId ?? state.viewedAttendance?.id;
  const {sessions,session:detail}=await api('/api/attendance/overview'+(viewedId ? `?session_id=${viewedId}` : ''));
  if(requestId!==attendanceRequest) return;
  state.attendanceSessionCount=sessions.length;
  state.activeAttendance=sessions.find(session=>session.status==='active') || null;
  const signature=JSON.stringify({sessions,detail});
  if(signature===attendanceSignature){updateControls();return;}
  attendanceSignature=signature;
  state.viewedAttendance = detail;
  $('session-history').replaceChildren();
  if (!sessions.length) $('session-history').add(new Option('No sessions yet',''));
  for (const session of sessions) {
    const date = new Date(session.started_at).toLocaleDateString(undefined,{month:'short',day:'numeric'});
    $('session-history').add(new Option(`${session.name} · ${date} · ${session.status === 'active' ? 'In progress' : 'Ended'}`,session.id));
  }
  if (detail) $('session-history').value = String(detail.id);
  $('session-form').hidden = !!state.activeAttendance;
  $('active-session').hidden = !state.activeAttendance;
  $('active-session-name').textContent = state.activeAttendance?.name || '';
  if (!$('session-name').value) $('session-name').value = `Attendance ${new Date().toLocaleDateString()}`;
  $('attendance-counts').hidden = $('roster-note').hidden = $('export-attendance').hidden = !detail;
  $('attendance-session-state').textContent = detail ? detail.status === 'active' ? 'IN PROGRESS' : 'ENDED' : '';
  $('roster-tools').hidden=$('campus-counts').hidden=$('movement-log').hidden=!detail;
  if(detail){$('count-on-campus').textContent=detail.movement_counts.on_campus;$('count-checked-out').textContent=detail.movement_counts.checked_out;$('count-location-unknown').textContent=detail.movement_counts.unknown;}
  renderMovementLog(detail);
  renderRoster(detail);
  updateControls();
}

function renderRoster(detail=state.viewedAttendance){
  $('attendance-roster').replaceChildren();
  if (!detail) {
    const empty = document.createElement('p');empty.className='directory-empty';empty.textContent='Start a session to see the attendance roster.';$('attendance-roster').append(empty);
  } else {
    for (const status of ['present','pending','absent']) $(`count-${status}`).textContent = detail.counts[status];
    $('export-attendance').href = `/api/attendance/sessions/${detail.id}/export.csv`;
    const table = document.createElement('table');table.className='attendance-table';table.setAttribute('aria-label','Attendance roster');
    const head = document.createElement('thead');head.innerHTML='<tr><th>Person</th><th>Status</th><th>Check-in</th><th>Check-out</th><th>Campus record</th><th>Staff action</th></tr>';table.append(head);
    const body = document.createElement('tbody');
    const query=$('roster-search').value.trim().toLocaleLowerCase(),filter=$('roster-filter').value;
    const visible=detail.roster.filter(person=>(!query || [person.name,person.student_id,person.employee_code,person.class_name,person.academic_year].join(' ').toLocaleLowerCase().includes(query)) && (!filter || person.status===filter));
    $('roster-matches').textContent=`${visible.length} of ${detail.roster.length} people`;
    for (const person of visible) {
      const row = document.createElement('tr');row.dataset.recordId = person.id;
      const name = document.createElement('td');name.textContent = person.name;
      const source = document.createElement('span');source.className='attendance-source';source.textContent=person.method === 'manual' ? 'Staff recorded' : person.method === 'camera' ? 'Camera check-in' : person.person_id === null ? 'Enrollment removed' : '';
      const school=document.createElement('span');school.className='attendance-source';school.textContent=[person.student_id || person.employee_code,person.class_name,person.academic_year].filter(Boolean).join(' · ');name.append(school,source);
      const status = document.createElement('td');const select = document.createElement('select');select.className=person.status;select.setAttribute('aria-label',`Attendance for ${person.name}`);
      for (const [value,label] of [['present','Present'],['absent','Absent'],...(detail.status === 'active' ? [['pending','Not seen yet']] : [])]) select.add(new Option(label,value));
      select.value=person.status;select.onchange=()=>correctAttendance(detail.id,person.id,select.value);
      status.append(select);
      const seen = timestamp=>timestamp ? new Date(timestamp).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
      const first=document.createElement('td');first.className='attendance-time';first.textContent=seen(person.checked_in_at);
      const last=document.createElement('td');last.className='attendance-time';last.textContent=seen(person.checked_out_at);
      const campus=document.createElement('td');campus.textContent=({on_campus:'Checked in',checked_out:'Checked out',unknown:'Not recorded',not_arrived:'Not arrived'})[person.campus_state];
      const actions=document.createElement('td');actions.className='movement-actions';
      for(const [action,label] of [['check_in','Check in'],['check_out','Check out']]){const button=document.createElement('button');button.className='text-button';button.textContent=label;button.setAttribute('aria-label',`${label} ${person.name}`);button.dataset.unavailable=String(detail.status!=='active' || (action==='check_in'?person.campus_state==='on_campus':person.campus_state!=='on_campus'));button.disabled=button.dataset.unavailable==='true';button.onclick=()=>askStaffMovement(detail,person,action);actions.append(button);}
      row.append(name,status,first,last,campus,actions);body.append(row);
    }
    if(!visible.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.textContent='No people match your filters.';row.append(cell);body.append(row);}
    table.append(body);$('attendance-roster').append(table);
  }
  updateControls();
}
$('roster-search').oninput=()=>renderRoster();
$('roster-filter').onchange=()=>renderRoster();

$('session-form').onsubmit = async event => {
  event.preventDefault();
  if (state.attendanceBusy || state.activeAttendance) return;
  state.attendanceBusy=true;updateControls();
  try {
    const started=(await api('/api/attendance/sessions','POST',{name:$('session-name').value.trim(),class_name:$('session-class').value,person_type:$('session-type').value,academic_year:$('session-year').value})).session;
    await refreshAttendance(started.id);
    result('Attendance started',state.stream ? 'Start attendance scan to mark people present.' : 'Enable the camera, then start attendance scan.');
    toast(`${started.name} started.`);
  } catch(error) {toast(error.message);await refreshAttendance().catch(()=>{});}
  finally {state.attendanceBusy=false;updateControls();}
};

$('session-history').onchange = async()=> {
  stopScan();
  try {await refreshAttendance(Number($('session-history').value));}
  catch(error){toast(error.message);}
};

async function correctAttendance(sessionId,recordId,status) {
  if (state.attendanceBusy) return;
  state.attendanceBusy=true;updateControls();
  try {
    await api(`/api/attendance/sessions/${sessionId}/records/${recordId}`,'PATCH',{status});
    await refreshAttendance(sessionId);toast('Attendance corrected.');
  }catch(error){toast(error.message);await refreshAttendance(sessionId).catch(()=>{});}
  finally{state.attendanceBusy=false;updateControls();}
}

$('end-session').onclick=async()=>{
  if (!state.activeAttendance || state.attendanceBusy) return;
  stopScan();
  try {
    await refreshAttendance(state.activeAttendance.id);
    const current=state.viewedAttendance;
    if (!current || current.status !== 'active') return;
    $('end-session-description').textContent=`End “${current.name}”? ${current.counts.present} present. ${current.counts.pending} not seen yet will be marked Absent. You can correct the saved record afterwards. ${current.movement_counts.on_campus} check-ins have no departure recorded. Ending the session will not create check-outs.`;
    $('end-session-dialog').showModal();
  }catch(error){toast(error.message);}
};
$('cancel-end-session').onclick=()=>$('end-session-dialog').close();
$('confirm-end-session').onclick=async()=>{
  if (!state.activeAttendance || state.attendanceBusy) return;
  const sessionId=state.activeAttendance.id;state.attendanceBusy=true;$('confirm-end-session').disabled=true;updateControls();
  try{
    const saved=(await api(`/api/attendance/sessions/${sessionId}/finish`,'POST',{})).session;
    await refreshAttendance(sessionId);$('session-name').value='';$('end-session-dialog').close();
    result('Attendance saved',`${saved.counts.present} present · ${saved.counts.absent} absent. Review the roster or export CSV.`);
    toast('Session ended and attendance saved.');
  }catch(error){toast(error.message);}
  finally{state.attendanceBusy=false;$('confirm-end-session').disabled=false;updateControls();}
};
setInterval(()=>{
  if (!window.sharedRoom?.connected && !document.hidden && !attendancePolling && !state.attendanceBusy && !state.enrolling && !document.activeElement?.matches('#attendance-roster select, #session-history')) {attendancePolling=true;refreshAttendance().catch(()=>{}).finally(()=>attendancePolling=false);}
},15000);

$('delete-attendance').onclick=()=>{
  if(state.attendanceBusy || state.enrolling)return;
  stopScan();$('delete-attendance-error').hidden=true;$('delete-attendance-dialog').showModal();
};
$('cancel-delete-attendance').onclick=()=>$('delete-attendance-dialog').close();
$('confirm-delete-attendance').onclick=async()=>{
  if(state.attendanceBusy)return;
  state.attendanceBusy=true;$('confirm-delete-attendance').disabled=true;$('cancel-delete-attendance').disabled=true;updateControls();
  try{
    await api('/api/attendance/sessions','DELETE',{confirmed:true});
    ++attendanceRequest;attendanceSignature='';state.viewedAttendance=null;state.activeAttendance=null;state.attendanceSessionCount=0;
    $('roster-search').value='';$('roster-filter').value='';$('session-name').value='';
    $('delete-attendance-dialog').close();
    await refreshAttendance();result('Attendance cleared','Start a new session when you are ready.');toast('All attendance deleted.');
  }catch(error){$('delete-attendance-error').textContent=error.message;$('delete-attendance-error').hidden=false;}
  finally{state.attendanceBusy=false;$('confirm-delete-attendance').disabled=false;$('cancel-delete-attendance').disabled=false;updateControls();}
};
$('delete-attendance-dialog').addEventListener('cancel',event=>{if(state.attendanceBusy)event.preventDefault();});

function setMovementMode(mode){if(window.sharedRoom?.snapshot?.source_id){window.sharedRoom.configure(mode,$('crossing-axis').value,$('entry-direction').value);return;}stopScan();recentCheckIns.clear();state.movementMode=mode;updateControls();liveTracker.render();result('Movement mode changed','Start the scan when the entry/exit direction is correct.');}
$('attendance-flow').onchange=event=>setMovementMode(event.target.value);
$('fullscreen-flow').onchange=event=>setMovementMode(event.target.value);
for(const id of ['crossing-axis','entry-direction'])$(id).onchange=()=>{
  if(window.sharedRoom?.snapshot?.source_id){window.sharedRoom.configure(state.movementMode,$('crossing-axis').value,$('entry-direction').value);return;}
  stopScan();if(id==='crossing-axis'){const vertical=$('crossing-axis').value==='y';$('entry-direction').options[0].textContent=vertical?'Top → bottom':'Left → right';$('entry-direction').options[1].textContent=vertical?'Bottom → top':'Right → left';}liveTracker.render();
};
function renderMovementLog(detail){
  const holder=$('movement-log');holder.replaceChildren();if(!detail)return;
  const heading=document.createElement('h3');heading.textContent='Movement history';holder.append(heading);
  if(!detail.movements.length){const empty=document.createElement('p');empty.className='message';empty.textContent='No arrivals or departures recorded yet.';holder.append(empty);return;}
  for(const event of detail.movements){const row=document.createElement('p');row.className='movement-log-row';const name=document.createElement('strong');name.textContent=event.name;const text=document.createElement('span');text.textContent=`${event.action.replaceAll('_',' ')} · ${event.source} · ${new Date(event.occurred_at).toLocaleString()}${event.note?' · '+event.note:''}`;row.append(name,text);holder.append(row);}
}
let staffMovement=null;
function askStaffMovement(session,person,action){stopScan();staffMovement={session:session.id,record:person.id,action};$('staff-movement-heading').textContent=action==='check_out'?'Record check-out':'Record check-in';$('staff-movement-description').textContent=`${person.name} · ${session.name}. Verify the movement before saving.`;$('staff-movement-note').value='';$('staff-movement-error').hidden=true;$('staff-movement-dialog').showModal();}
$('cancel-staff-movement').onclick=()=>$('staff-movement-dialog').close();
$('staff-movement-form').onsubmit=async event=>{
  event.preventDefault();if(!staffMovement || state.attendanceBusy)return;state.attendanceBusy=true;$('confirm-staff-movement').disabled=true;updateControls();
  try{await api(`/api/attendance/sessions/${staffMovement.session}/records/${staffMovement.record}/movement`,'POST',{action:staffMovement.action,note:$('staff-movement-note').value.trim()});await refreshAttendance(staffMovement.session);$('staff-movement-dialog').close();toast('Student movement recorded.');}
  catch(error){$('staff-movement-error').textContent=error.message;$('staff-movement-error').hidden=false;}
  finally{state.attendanceBusy=false;$('confirm-staff-movement').disabled=false;updateControls();}
};

$('iphone-target').onchange=updateControls;
$('session-type').onchange=()=>{const staff=$('session-type').value==='staff';$('session-class').disabled=$('session-year').disabled=staff;if(staff){$('session-class').value='';$('session-year').value='';}};
