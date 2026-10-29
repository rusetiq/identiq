'use strict';
// A short-lived, device-local recovery queue. It only contains camera frames
// that could not reach the local attendance laptop. Recognition still runs on
// the laptop after reconnection, using the normal database and policy.
window.IdentiqOfflineAttendance=(()=>{
  const DB_NAME='identiq-offline-attendance',STORE='frames',MAX_ITEMS=4,MAX_AGE=1500;
  let database;
  function open(){
    if(database)return database;
    database=new Promise((resolve,reject)=>{
      const request=indexedDB.open(DB_NAME,1);
      request.onupgradeneeded=()=>request.result.createObjectStore(STORE,{keyPath:'id',autoIncrement:true});
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error || new Error('Offline storage is unavailable.'));
    });
    return database;
  }
  async function readAll(){
    const db=await open();return new Promise((resolve,reject)=>{
      const request=db.transaction(STORE,'readonly').objectStore(STORE).getAll();
      request.onsuccess=()=>resolve(request.result.sort((a,b)=>a.queued_at-b.queued_at));request.onerror=()=>reject(request.error);
    });
  }
  async function remove(ids){
    if(!ids.length)return;
    const db=await open();await new Promise((resolve,reject)=>{
      const transaction=db.transaction(STORE,'readwrite');ids.forEach(id=>transaction.objectStore(STORE).delete(id));
      transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);
    });
  }
  async function prune(){
    const items=await readAll(),now=Date.now();
    const discard=items.filter(item=>now-item.queued_at>MAX_AGE).map(item=>item.id);
    const remaining=items.filter(item=>!discard.includes(item.id));
    discard.push(...remaining.slice(0,Math.max(0,remaining.length-MAX_ITEMS)).map(item=>item.id));
    await remove(discard);return (await readAll()).length;
  }
  async function add(payload){
    await prune();
    const db=await open(),signature=`${payload.image.length}:${payload.image.slice(-48)}`;
    const items=await readAll();
    if(items.some(item=>item.signature===signature))return items.length;
    await new Promise((resolve,reject)=>{
      const transaction=db.transaction(STORE,'readwrite');transaction.objectStore(STORE).add({payload,signature,queued_at:Date.now()});
      transaction.oncomplete=resolve;transaction.onerror=()=>reject(transaction.error);
    });
    return prune();
  }
  return {add,entries:async()=>{await prune();return readAll();},remove,count:prune};
})();
