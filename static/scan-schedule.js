'use strict';
window.IdentiqScan = (() => {
  let sample = null;
  function delay(data, attendance, frameElapsedMs, followsCamera = false) {
    const visible = data.face_count > 0 || !!data.box;
    const elapsed = Number.isFinite(frameElapsedMs) ? Math.max(0, frameElapsedMs) : 0;
    if (!attendance) return followsCamera ? 0 : Math.max(25, Math.ceil((visible ? 80 : 100) - elapsed));
    // Keep searching promptly when faces are visible but not yet recognizable.
    if (!visible || Array.isArray(data.faces) && !data.faces.some(face => face.status === 'matched'))
      return followsCamera ? 0 : Math.max(25, Math.ceil(100 - elapsed));
    const confirming = (data.faces || []).map(face => face.attendance)
      .filter(update => update?.status === 'confirming' && Number.isFinite(update.retry_after_ms));
    if (confirming.length && Number.isFinite(frameElapsedMs)) {
      // These hints are relative to the frame's capture time. Encoding,
      // transport and inference have already used part of that interval.
      const nextCapture = Math.min(...confirming.map(update => update.retry_after_ms));
      return Math.max(0, Math.min(1000, Math.ceil(nextCapture + 10 - frameElapsedMs)));
    }
    const hint = data.retry_after_ms;
    // The next inference also consumes time before the server can count it.
    // Use the last processing duration as an estimate; the server still checks
    // frame freshness, spacing and all confirmations for every observation.
    const processing = (data.face_count > 0 || data.box) && Number.isFinite(data.processing_ms) ? Math.max(0,data.processing_ms) : 0;
    return Number.isFinite(hint) ? Math.max(40,Math.min(1000,Math.ceil(hint-processing))) : data.face_count ? 250 : 100;
  }
  function record(data, requestMs) {
    // Keep only diagnostic numbers; never retain the response, names or images.
    sample = {
      recorded_at:Date.now(),
      request_ms:Number.isFinite(requestMs) ? Math.max(0,Math.round(requestMs)) : null,
      processing_ms:Number.isFinite(data.processing_ms) ? Math.max(0,data.processing_ms) : null,
      face_count:Number.isFinite(data.face_count) ? Math.max(0,Math.floor(data.face_count)) : data.box ? 1 : 0,
    };
  }
  // References are shared across people: one scene is uploaded once, while
  // every provisional identity has its own bounded queue. Identity is always
  // rechecked by the server; a box or a queue key is never match evidence.
  function faceBuffer() {
    const queues=new Map();let warmup=[],targets=[];
    const prune=(items,now)=>items.filter(item=>now-item.capturedAt<=1500).slice(-4);
    return {
      push(frame) {
        warmup=prune([...warmup,frame],frame.capturedAt);
        for(const id of targets)queues.set(id,prune([...(queues.get(id)||[]),frame],frame.capturedAt));
      },
      accept(faces,now) {
        targets=[...new Set(faces.filter(face=>face.status==='matched' && Number.isInteger(face.person?.id) &&
          ['confirming','tracking','present','checked_out'].includes(face.attendance?.status)).map(face=>face.person.id))].slice(0,20);
        for(const id of queues.keys())if(!targets.includes(id))queues.delete(id);
        for(const id of targets)queues.set(id,prune(queues.get(id)||warmup,now));
        warmup=prune(warmup,now);
        return targets.length>0;
      },
      take(now) {
        warmup=prune(warmup,now);
        let oldest=warmup[0] || null;
        for(const [id,items] of queues){
          const fresh=prune(items,now);queues.set(id,fresh);
          if(fresh.length && (!oldest || fresh[0].capturedAt<oldest.capturedAt))oldest=fresh[0];
        }
        if(oldest){
          for(const [id,items] of queues)queues.set(id,items.filter(item=>item.capturedAt>oldest.capturedAt));
          warmup=warmup.filter(item=>item.capturedAt>oldest.capturedAt);
        }
        return oldest;
      },
      has(now){return prune(warmup,now).length>0 || [...queues.values()].some(items=>prune(items,now).length>0);},
      clear(){queues.clear();warmup=[];targets=[];},
    };
  }
  // Arm the callback at capture time so the camera and server can work in parallel.
  function frameClock(video, signal) {
    if (typeof video.requestVideoFrameCallback !== 'function') return null;
    let callback = null, release = null, pending = null;
    function reset() {
      if (callback !== null) video.cancelVideoFrameCallback(callback);
      callback = null;
      release?.();
      release = pending = null;
    }
    function captured() {
      reset();
      if (signal.aborted) return;
      pending = new Promise(resolve => { release = resolve; });
      callback = video.requestVideoFrameCallback(() => {
        callback = null;
        reset();
      });
    }
    signal.addEventListener('abort', reset, {once:true});
    return { captured, ready: () => pending, close() {
      signal.removeEventListener('abort', reset);
      reset();
    }};
  }
  function captureSize() {
    let size = 1920, lastFull = -Infinity, slowFullScan = false, needsDetail = false;
    return {
      observe(result, video, submittedSize, frameCount = 1) {
        size = 1920;
        const faces = result.faces || [result];
        needsDetail = faces.some(face => face.attendance?.status === 'confirming' ||
          ['too_small', 'blurry', 'low_light', 'uncertain_detection'].includes(face.code));
        if (needsDetail) return;
        if (submittedSize === 1920 && Number.isFinite(result.processing_ms))
          slowFullScan = result.processing_ms / frameCount >= 80;
        if (slowFullScan && result.code === 'no_face' && faces.length === 0) {
          size = 960;
          return;
        }
        if (!faces.length) return;
        let pixels = Infinity;
        for (const face of faces) {
          if (face.status !== 'matched' || !face.box ||
              !(face.quality?.brightness >= 55 && face.quality?.detection_score >= .9)) return;
          pixels = Math.min(pixels, face.box[2] * video.videoWidth, face.box[3] * video.videoHeight);
        }
        const side = Math.max(video.videoWidth, video.videoHeight);
        for (const candidate of [640, 960]) {
          if (pixels * Math.min(1, candidate / side) >= 150) {
            size = candidate;
            break;
          }
        }
      },
      next(now) {
        if (size < 1920 && now - lastFull < 500) return size;
        lastFull = now;
        return 1920;
      },
      jpegQuality() { return needsDetail ? .9 : .78; },
      reset() { size = 1920; slowFullScan = false; needsDetail = false; },
    };
  }
  return {delay, record, faceBuffer, frameClock, captureSize, last:() => sample ? {...sample} : null};
})();
