'use strict';
window.IdentiqScan = (() => {
  let sample = null;
  function delay(data, attendance) {
    if (!attendance) return 650;
    const hint = data.retry_after_ms;
    return Number.isFinite(hint) ? Math.max(40,Math.min(1000,Math.ceil(hint))) : data.face_count ? 250 : 600;
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
  return {delay, record, last:() => sample ? {...sample} : null};
})();
