'use strict';
window.IdentiqScan = (() => {
  let sample = null;
  function delay(data, attendance) {
    if (!attendance) return data.face_count > 0 || data.box ? 150 : 650;
    const hint = data.retry_after_ms;
    // The next inference also consumes time before the server can count it.
    // Use the last processing duration as an estimate; the server still checks
    // frame freshness, spacing and all confirmations for every observation.
    const processing = (data.face_count > 0 || data.box) && Number.isFinite(data.processing_ms) ? Math.max(0,data.processing_ms) : 0;
    return Number.isFinite(hint) ? Math.max(40,Math.min(1000,Math.ceil(hint-processing))) : data.face_count ? 250 : 600;
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
        warmup=[];
        return targets.length>0;
      },
      take(now) {
        let oldest=null;
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
      has(now){return [...queues.values()].some(items=>prune(items,now).length>0);},
      clear(){queues.clear();warmup=[];targets=[];},
    };
  }
  return {delay, record, faceBuffer, last:() => sample ? {...sample} : null};
})();
