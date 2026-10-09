"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const state = { stream: null, mode: "attendance", scanning: false, enrolling: false, ready: false, people: [], activeAttendance: null, viewedAttendance: null, attendanceBusy: false, scanSessionId: null, phoneCapture: null, streak: 0, candidate: null, generation: 0, cameraGeneration: 0, cameraStarting: false, scanAbort: null, attendanceSessionCount: 0 };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let toastTimer;
const captureCanvas=document.createElement('canvas');
const liveTracker = new FaceTracker({video: $("video"), canvas: $("overlay"), capture: frame,
  request: image => api("/api/analyze", "POST", {image,group:state.mode==='attendance' || state.mode==='verify' && !$("person-select").value}), onUpdate: updateControls, pause:()=>document.hidden || state.scanning && (state.scanSessionId || !$("person-select").value)});
let cancelEnrollment = false;
const recentCheckIns=new Map();

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
}

async function api(path, method = "GET", data, signal) {
  const controller=new AbortController(), cancel=()=>controller.abort();
  if(signal?.aborted) controller.abort(); else signal?.addEventListener('abort',cancel,{once:true});
  const timeout=setTimeout(cancel,15000);
  try {
    const response=await fetch(path,{method,signal:controller.signal,headers:{"Content-Type":"application/json","X-Local-Token":token},...(data?{body:JSON.stringify(data)}:{})});
    const body=await response.json();
    if(response.status===401){stopCamera();window.location.assign('/?expired=1');}
    if(!response.ok) throw Object.assign(new Error(body.error || 'Request failed. Try again.'),{code:body.code});
    return body;
  }catch(error){
    if(error.name==='AbortError') throw Object.assign(new Error(signal?.aborted?'Scan cancelled.':'The local server took too long. Check it is running and try again.'),{code:signal?.aborted?'cancelled':'server_timeout'});
    throw error;
  }finally{clearTimeout(timeout);signal?.removeEventListener('abort',cancel);}
}

function updateControls() {
  $("delete-attendance").disabled=state.attendanceBusy || state.enrolling || !state.attendanceSessionCount;
  $("fullscreen-scan").hidden=state.mode!=="attendance" || !state.activeAttendance;
  $("fullscreen-scan").disabled=state.attendanceBusy || state.enrolling || !state.stream || !state.ready;
  $("fullscreen-scan").textContent=state.scanning && state.scanSessionId ? "Pause attendance scan" : "Start attendance scan";
  document.querySelectorAll("#attendance-roster select").forEach(select=>{select.disabled=state.attendanceBusy || state.enrolling;});
  $("start-session").disabled = state.attendanceBusy || state.enrolling || !!state.activeAttendance || !state.people.length;
  $("session-name").disabled = state.attendanceBusy || !!state.activeAttendance;
  $("attendance-scan").disabled = state.attendanceBusy || state.enrolling || !state.stream || !state.ready || !state.activeAttendance;
  $("end-session").disabled = state.attendanceBusy || state.enrolling;
  $("attendance-tab").disabled = state.enrolling;
  $("recognize").disabled = !state.stream || !state.ready || state.people.length === 0 || state.enrolling;
  $("capture").disabled = !state.stream || !state.ready || state.enrolling || liveTracker.readyStreak < 2;
  $("stop-camera").disabled = !state.stream && !state.cameraStarting;
  $("stop-camera").textContent = state.cameraStarting && !state.stream ? "Cancel connection" : "Turn off camera";
  $("start-camera").disabled = !!state.stream || state.cameraStarting;
  $("person-select").disabled = state.scanning;
  $("verify-tab").disabled = state.enrolling;
  $("enroll-tab").disabled = state.enrolling;
  $("person-name").disabled = state.enrolling;
  $("consent").disabled = state.enrolling;
  $("add-person").disabled = state.enrolling;
  $("laptop-source").disabled = $("iphone-source").disabled = state.enrolling;
  $("iphone-file").disabled = $("iphone-target").disabled = $("iphone-consent").disabled = state.enrolling;
  $("import-iphone").disabled = state.enrolling || !state.ready || !state.phoneCapture;
}

function result(title, detail, kind = "", score = null) {
  for (const prefix of ["result", "attendance-result"]) {
    $(prefix).className = `result ${kind}`;
    $(`${prefix}-title`).textContent = title;
    $(`${prefix}-detail`).textContent = detail;
    $(`${prefix}-icon`).textContent = kind === "matched" ? "✓" : kind === "warning" ? "!" : "⌁";
  }
  $("score-row").hidden = score === null;
  if (score !== null) $("score-value").textContent = `${score.toFixed(3)} / threshold 0.450`;
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

function frame() {
  const video = $("video");
  if (!state.stream || !video.videoWidth || video.readyState < 2) throw new Error("The camera is not ready yet.");
  const canvas = captureCanvas;
  const ratio = Math.min(1, 960 / video.videoWidth, 960 / video.videoHeight);
  const width=Math.round(video.videoWidth*ratio),height=Math.round(video.videoHeight*ratio);
  if(canvas.width!==width || canvas.height!==height){canvas.width=width;canvas.height=height;}
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", .9);
}

async function startCamera() {
  if(state.cameraStarting || state.stream) return;
  state.cameraStarting=true;const cameraGeneration=++state.cameraGeneration;
  $("camera-label").textContent="CONNECTING";updateControls();
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Use this app at http://localhost:8080 in a browser with camera support.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 720 } } });
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
    $("enroll-message").textContent = "Enter a name, confirm permission, then capture five samples.";
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
  state.scanning = false;
  state.scanSessionId = null;
  state.generation++;
  state.streak = 0;
  state.candidate = null;
  $("recognize").textContent = "Start recognition";
  $("attendance-scan").textContent = "Start attendance scan";
  $("feed-label").textContent = "LIVE PREVIEW";
  clearBox();updateControls();
  $("scan-face-results").replaceChildren();$("scan-face-results").hidden=true;
}

function stopCamera() {
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
  for (const current of ["attendance", "verify", "enroll"]) {
    $(`${current}-panel`).hidden = current !== mode;
    $(`${current}-tab`).classList.toggle("active", current === mode);
    $(`${current}-tab`).setAttribute("aria-selected", String(current === mode));
  }
  $("camera-hint").textContent = mode === "enroll" ? "One person only. Look forward, then turn slightly between samples." : "Up to 8 faces together. Keep every face clear and facing the camera.";
  updateControls();
}

async function refreshPeople() {
  const selected = $("person-select").value;
  state.people = (await api("/api/people")).people;
  document.dispatchEvent(new CustomEvent('people-refreshed',{detail:state.people}));
  $("people-count").textContent = state.people.length;
  const phoneTarget = $("iphone-target").value;
  $("iphone-target").replaceChildren(new Option("New person from capture file", ""));
  state.people.forEach(person => $("iphone-target").add(new Option(person.name, person.id)));
  if (state.people.some(person => String(person.id) === phoneTarget)) $("iphone-target").value = phoneTarget;
  $("person-select").replaceChildren(new Option("Recognize anyone enrolled", ""));
  state.people.forEach(person => $("person-select").add(new Option(person.name, person.id)));
  if (state.people.some(person => String(person.id) === selected)) $("person-select").value = selected;
  $("people-list").replaceChildren();
  if (!state.people.length) {
    const empty = document.createElement("div");empty.className = "directory-empty";
    empty.innerHTML = "<strong>No people enrolled yet.</strong><p>Use Enroll to add someone.</p>";
    $("people-list").append(empty);
  }
  for (const person of state.people) {
    const row = document.createElement("div");row.className = "person-row";
    const avatar = document.createElement("div");avatar.className = "avatar";
    avatar.textContent = person.name.split(/\s+/u).slice(0,2).map(part=>Array.from(part)[0]).join("").toUpperCase();
    const info = document.createElement("div");info.className = "person-info";
    const name = document.createElement("strong");name.textContent = person.name;
    const meta = document.createElement("p");
    meta.textContent = `ID ${String(person.id).padStart(3,"0")} · ${person.samples} samples${person.iphone_samples ? " · iPhone added" : ""} · Added ${new Date(person.created_at).toLocaleDateString(undefined,{month:"short",day:"numeric"})}`;
    info.append(name,meta);
    const status = document.createElement("span");status.className = "person-status";status.innerHTML = '<span class="green-dot"></span> ENROLLED';
    const remove = document.createElement("button");remove.className = "text-button";remove.textContent = "Remove";remove.setAttribute("aria-label",`Remove ${person.name}`);
    remove.onclick = () => askDelete(person);
    row.append(avatar,info,status,remove);$("people-list").append(row);
  }
  updateControls();
}

async function scan(attendanceSessionId = null) {
  if (state.scanning) { const attendanceScan = !!state.scanSessionId; stopScan();result(attendanceScan ? "Attendance scan paused" : "Recognition paused", "Start again when you’re ready.");return; }
  state.scanning = true;
  const scanAbort=new AbortController();state.scanAbort=scanAbort;
  state.scanSessionId = attendanceSessionId;
  const scannerId = crypto.randomUUID();
  const group = !!attendanceSessionId || !$("person-select").value;
  const groupStreaks = new Map();
  const generation = ++state.generation;
  $("recognize").textContent = "Pause recognition";
  if (attendanceSessionId) $("attendance-scan").textContent = "Pause attendance scan";
  $("feed-label").textContent = attendanceSessionId ? "TAKING ATTENDANCE" : "RECOGNIZING";
  updateControls();
  while (state.scanning && generation === state.generation && state.stream) {
    try {
      const selected = $("person-select").value;
      const capturedAt=performance.now(),image=frame();
      const data = await api(group ? "/api/recognize/group" : "/api/recognize", "POST", { image, person_id: !attendanceSessionId && selected ? Number(selected) : null, ...(attendanceSessionId ? {attendance_session_id: attendanceSessionId, scanner_id: scannerId} : {}) }, scanAbort.signal);
      if (!state.scanning || generation !== state.generation) break;
      if(group) {liveTracker.accept(data,image,capturedAt);await showGroupResults(data,groupStreaks,attendanceSessionId);continue;}
      if (data.status === "matched") {
        state.streak = state.candidate === data.person.id ? state.streak + 1 : 1;
        state.candidate = data.person.id;
        const confirmed = attendanceSessionId ? data.attendance?.status === "present" : state.streak >= 3;
        result(confirmed ? data.person.name : "Confirming match…", confirmed ? `Identity ${selected ? "verified" : "recognized"} · Database ID ${data.person.id}` : `${state.streak} of 3 consecutive matches`, confirmed ? "matched" : "", data.similarity);
        if (attendanceSessionId) {
          const attendance = data.attendance;
          if (attendance.status === "present") {
            result(data.person.name, "Marked present for this session.", "matched", data.similarity);
            if (attendance.newly_present) { toast(`${data.person.name} marked present.`); await refreshAttendance(attendanceSessionId); }
          } else if (attendance.status === "confirming") result("Confirming attendance…", `${attendance.confirmations} of 1 required match`, "", data.similarity);
          else result(data.person.name, attendance.message, "warning", data.similarity);
        }
        drawBox(data.box, confirmed, data.person.name);
      } else {
        state.streak = 0;state.candidate = null;
        result({unknown:"Unknown face",ambiguous:"Uncertain match",mismatch:"Identity mismatch",empty:"No enrolled faces"}[data.status] || "Try again",data.message,"warning",data.similarity ?? null);
        drawBox(data.box,false);
      }
    } catch (error) {
      if (!state.scanning || generation !== state.generation) break;
      state.streak = 0;state.candidate = null;clearBox();
      groupStreaks.clear();showCameraNames([]);$("scan-face-results").replaceChildren();$("scan-face-results").hidden=true;
      result("Adjust your camera view",error.message,"warning");
      if (!["no_face","multiple_faces","blurry","too_small"].includes(error.code)) { stopScan();toast(error.message);break; }
    }
    await sleep(attendanceSessionId ? 40 : 650);
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
      matched=sessionId ? (face.attendance.status==='present' || face.attendance.status==='manual_override' && face.attendance.attendance_status==='present') : streaks.get(face.person.id)>=3;
      if(matched) {confirmed++;detail=sessionId ? face.attendance.status==='manual_override' ? 'Present · staff correction' : 'Present for this session' : 'Recognized';}
      else if(!sessionId || face.attendance.status==='confirming') {confirming++;detail=`${sessionId ? face.attendance.confirmations : streaks.get(face.person.id)} of ${sessionId ? 1 : 3} consecutive matches`;}
      else detail=face.attendance.status==='manual_override' ? `Staff marked ${face.attendance.attendance_status}. Camera status kept unchanged.` : face.attendance.message;
      if(face.attendance?.newly_present) newlyPresent.push(name);
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
  result(visible?`${confirmed} ${sessionId?'present':'recognized'}`:'Ready for the next person',visible?`${confirming} confirming · ${visible-confirmed-confirming} need review`:'Enrolled people are checked as they pass the camera.',confirmed?'matched':'');
  if(newlyPresent.length) {toast(`${newlyPresent.join(', ')} marked present.`);refreshAttendance(sessionId).catch(error=>toast(error.message));}
  // Requests remain sequential; attendance resumes shortly after each response.
  await sleep(sessionId ? 40 : 650);
}

async function enroll(event) {
  event.preventDefault();
  if (state.enrolling || !state.stream) return;
  const name = $("person-name").value.trim();
  if (!name || !$("consent").checked) return;
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
    $("enroll-message").textContent="Saving face patterns to your local database…";
    const saved = await api("/api/enroll", "POST", {name,consent:true,images:samples});
    await refreshPeople();
    $("person-name").value="";$("consent").checked=false;
    $("enroll-message").textContent=`${saved.person.name} is enrolled. You can now recognize their face.`;
    toast(`${saved.person.name} enrolled successfully.`);completed=true;
  } catch (error) {
    $("enroll-message").textContent=error.message;$("enroll-message").classList.add("error");

  } finally {
    samples.length=0;state.enrolling=false;$("cancel-capture").hidden = true;clearBox();updateControls();
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
    people=[...people,...Array.from(recentCheckIns.values()).filter(person=>!current.has(person.id)).map(person=>({...person,detail:'Checked in'}))];
  }
  const holder=$("camera-names");holder.replaceChildren();holder.hidden=!people.length;
  for(const person of people){const badge=document.createElement('div');badge.className=`camera-name ${person.matched?'confirmed':'review'}`;const name=document.createElement('strong'),detail=document.createElement('span');name.textContent=person.name;detail.textContent=person.detail;badge.append(name,detail);holder.append(badge);}
}
function closeCameraFullscreen(){
  document.querySelectorAll('main > :not(.workspace), .workspace > .controls').forEach(element=>element.inert=false);
  $("camera-card").classList.remove('camera-expanded');document.body.classList.remove('camera-expanded-open');
  $("camera-fullscreen").textContent='Fullscreen camera';$("camera-fullscreen").setAttribute('aria-pressed','false');
  liveTracker.render();$("camera-fullscreen").focus();
}
$("camera-fullscreen").onclick=()=>{
  if($("camera-card").classList.contains('camera-expanded')){closeCameraFullscreen();return;}
  document.querySelectorAll('main > :not(.workspace), .workspace > .controls').forEach(element=>element.inert=true);
  $("camera-card").classList.add('camera-expanded');document.body.classList.add('camera-expanded-open');
  $("camera-fullscreen").textContent='Exit fullscreen';$("camera-fullscreen").setAttribute('aria-pressed','true');liveTracker.render();
};
document.addEventListener('keydown',event=>{if(event.key==='Escape' && $("camera-card").classList.contains('camera-expanded')) closeCameraFullscreen();});
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
$("add-person").onclick=()=>{setMode("enroll");setSource("laptop");$("enroll-tab").scrollIntoView({behavior:"smooth",block:"center"});$("person-name").focus();};

window.addEventListener("pagehide",stopCamera);
document.addEventListener("visibilitychange",()=>{if(document.hidden && state.scanning){const attendanceScan=!!state.scanSessionId;stopScan();result(attendanceScan ? "Attendance scan paused" : "Recognition paused","Start again when you return.");}});
(async()=>{
  $("camera-dot").classList.add("off");
  try {
    const status=await api("/api/status");state.ready=status.models_ready;
    await refreshPeople();
    await refreshAttendance();
    if(!state.ready){toast("Face models are missing. Run python scripts/download_models.py.");result("Setup needed","Download the face models, then reload this page.","warning");}
    else if(state.activeAttendance) result("Session in progress","Enable the camera and start attendance scan to continue.");
    else if(state.people.length) result("Ready to take attendance","Name your session, then click Start session.");
  }catch(error){toast(`Could not connect to the local server: ${error.message}`);result("Server unavailable","Start the app and reload this page.","warning");}
  updateControls();
})();

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
    $("iphone-message").textContent = "Ready to import. Choose an existing person to add phone samples, or create a new enrollment.";
  } catch (error) {
    $("iphone-file-detail").textContent = "No valid file selected.";
    $("iphone-message").textContent = error.message;
    $("iphone-message").classList.add("error");
  } finally { updateControls(); }
};
$("iphone-form").onsubmit = async event => {
  event.preventDefault();
  if (!state.phoneCapture || !$("iphone-consent").checked || state.enrolling) return;
  stopScan(); state.enrolling = true; updateControls();
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
    state.enrolling = false; updateControls();
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
  $('roster-tools').hidden=!detail;
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
    const head = document.createElement('thead');head.innerHTML='<tr><th>Person</th><th>Status</th><th>First seen</th><th>Last seen</th></tr>';table.append(head);
    const body = document.createElement('tbody');
    const query=$('roster-search').value.trim().toLocaleLowerCase(),filter=$('roster-filter').value;
    const visible=detail.roster.filter(person=>(!query || person.name.toLocaleLowerCase().includes(query)) && (!filter || person.status===filter));
    $('roster-matches').textContent=`${visible.length} of ${detail.roster.length} people`;
    for (const person of visible) {
      const row = document.createElement('tr');row.dataset.recordId = person.id;
      const name = document.createElement('td');name.textContent = person.name;
      const source = document.createElement('span');source.className='attendance-source';source.textContent=person.method === 'manual' ? 'Manual correction' : person.method === 'camera' ? 'Camera check-in' : person.person_id === null ? 'Enrollment removed' : '';
      name.append(source);
      const status = document.createElement('td');const select = document.createElement('select');select.className=person.status;select.setAttribute('aria-label',`Attendance for ${person.name}`);
      for (const [value,label] of [['present','Present'],['absent','Absent'],...(detail.status === 'active' ? [['pending','Not seen yet']] : [])]) select.add(new Option(label,value));
      select.value=person.status;select.onchange=()=>correctAttendance(detail.id,person.id,select.value);
      status.append(select);
      const seen = timestamp=>timestamp ? new Date(timestamp).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
      const first=document.createElement('td');first.className='attendance-time';first.textContent=seen(person.first_seen_at);
      const last=document.createElement('td');last.className='attendance-time';last.textContent=seen(person.last_seen_at);
      row.append(name,status,first,last);body.append(row);
    }
    if(!visible.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.textContent='No people match your filters.';row.append(cell);body.append(row);}
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
    const started=(await api('/api/attendance/sessions','POST',{name:$('session-name').value.trim()})).session;
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
    $('end-session-description').textContent=`End “${current.name}”? ${current.counts.present} present. ${current.counts.pending} not seen yet will be marked Absent. You can correct the saved record afterwards.`;
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
  if (!document.hidden && !attendancePolling && !state.attendanceBusy && !state.enrolling && !document.activeElement?.matches('#attendance-roster select, #session-history')) {attendancePolling=true;refreshAttendance().catch(()=>{}).finally(()=>attendancePolling=false);}
},5000);

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
