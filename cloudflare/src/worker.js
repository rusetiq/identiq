const json=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
const fail=(status,message)=>{throw Object.assign(new Error(message),{status});};
const hash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),b=>b.toString(16).padStart(2,'0')).join('');
const random=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');
const text=(v,max=160)=>typeof v==='string'&&v.length>0&&v.length<=max;
async function body(request){
  if(Number(request.headers.get('Content-Length'))>1000000)fail(413,'Request too large');
  const reader=request.body?.getReader();if(!reader)fail(400,'JSON body required');
  let size=0,chunks=[];while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>1000000){await reader.cancel();fail(413,'Request too large');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  let data;try{data=JSON.parse(new TextDecoder().decode(bytes));}catch{fail(400,'Invalid JSON');}
  if(!data||typeof data!=='object'||Array.isArray(data))fail(400,'JSON object required');return data;
}
const stmt=(env,sql,...args)=>env.DB.prepare(sql).bind(...args);
export default {
  async fetch(request,env){
    try{return await route(request,env);}catch(error){return json({error:error.status?error.message:'Internal API error'},error.status||500);}
  }
};
async function route(request,env){
  const path=new URL(request.url).pathname,method=request.method;
  if(path==='/health'&&method==='GET')return json({ok:true,service:'identiq-api'});
  const token=request.headers.get('Authorization')?.replace(/^Bearer /,'');
  if(!token)fail(401,'API key required');
  const digest=await hash(token);
  if(path.startsWith('/admin/')){
    if(!env.ADMIN_TOKEN||digest!==await hash(env.ADMIN_TOKEN))fail(401,'Admin authentication required');
    if(path==='/admin/keys'&&method==='GET'){
      const {results}=await env.DB.prepare('SELECT id,name,scope,source,created_at,revoked_at FROM api_keys ORDER BY created_at DESC').all();return json({keys:results});
    }
    if(path==='/admin/keys'&&method==='POST'){
      const data=await body(request);
      if(!text(data.name,80)||!text(data.source,80)||!['read','write','scanner'].includes(data.scope))fail(400,'Name, source and valid scope required');
      const key='iq_'+random(),id=crypto.randomUUID();
      await stmt(env,'INSERT INTO api_keys VALUES(?,?,?,?,?,?,NULL)',id,data.name,await hash(key),data.scope,data.source,new Date().toISOString()).run();
      return json({id,key,scope:data.scope,source:data.source},201);
    }
    const revoke=path.match(/^\/admin\/keys\/([a-f0-9-]+)$/);
    if(revoke&&method==='DELETE'){
      await stmt(env,'UPDATE api_keys SET revoked_at=? WHERE id=?',new Date().toISOString(),revoke[1]).run();return json({revoked:true});
    }
    fail(404,'Route not found');
  }
  const key=await stmt(env,'SELECT * FROM api_keys WHERE digest=? AND revoked_at IS NULL',digest).first();
  if(!key)fail(401,'Invalid or revoked API key');
  if(path==='/v1/sync'&&method==='POST'){
    if(key.scope!=='scanner')fail(403,'Scanner scope required');
    const data=await body(request);
    if(!text(data.id,80)||!['people','session','delete_people','clear_attendance'].includes(data.kind)||!text(data.entity,80))fail(400,'Invalid sync event');
    const receipt=await stmt(env,'SELECT id FROM receipts WHERE id=? AND source=?',data.id,key.source).first();
    if(!receipt){
      const now=new Date().toISOString(),queries=[];
      if(data.kind==='clear_attendance')queries.push(stmt(env,"DELETE FROM snapshots WHERE source=? AND kind='session'",key.source));
      else if(data.kind==='delete_people')queries.push(stmt(env,"DELETE FROM snapshots WHERE source=? AND kind='people' AND entity=?",key.source,data.entity));
      else {
        if(!data.data||typeof data.data!=='object'||Array.isArray(data.data))fail(400,'Snapshot object required');
        if(data.kind==='people'&&['vector','vectors','embeddings','images','password'].some(k=>k in data.data))fail(400,'Biometric samples and credentials are not accepted');
        queries.push(stmt(env,'INSERT INTO snapshots VALUES(?,?,?,?,?) ON CONFLICT(source,kind,entity) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',key.source,data.kind,data.entity,JSON.stringify(data.data),now));
      }
      queries.push(stmt(env,'INSERT OR IGNORE INTO receipts VALUES(?,?,?)',data.id,key.source,now));await env.DB.batch(queries);
    }
    return json({saved:true});
  }
  if(path==='/v1/commands'&&method==='GET'){
    if(key.scope!=='scanner')fail(403,'Scanner scope required');
    const {results}=await stmt(env,"SELECT * FROM commands WHERE source=? AND status='pending' ORDER BY created_at,id LIMIT 20",key.source).all();
    return json({commands:results.map(row=>({...row,data:JSON.parse(row.data)}))});
  }
  const ack=path.match(/^\/v1\/commands\/([a-f0-9-]+)$/);
  if(ack&&method==='POST'){
    if(key.scope!=='scanner')fail(403,'Scanner scope required');const data=await body(request);
    if(!['applied','rejected'].includes(data.status))fail(400,'Invalid command result');
    await stmt(env,"UPDATE commands SET status=?,result=? WHERE id=? AND source=? AND status='pending'",data.status,JSON.stringify(data.result||{}),ack[1],key.source).run();return json({saved:true});
  }
  if(ack&&method==='GET'){
    const row=await stmt(env,'SELECT id,status,result,created_at FROM commands WHERE id=? AND source=?',ack[1],key.source).first();
    if(!row)fail(404,'Command not found');return json({command:{...row,result:row.result?JSON.parse(row.result):null}});
  }
  const listKind=path==='/v1/people'?'people':path==='/v1/attendance/sessions'?'session':null;
  if(listKind&&method==='GET'){
    const url=new URL(request.url),limit=Math.min(100,Math.max(1,Number(url.searchParams.get('limit'))||50)),offset=Math.max(0,Number(url.searchParams.get('offset'))||0);
    const {results}=await stmt(env,'SELECT entity,data,updated_at FROM snapshots WHERE source=? AND kind=? ORDER BY entity LIMIT ? OFFSET ?',key.source,listKind,limit,offset).all();
    return json({[listKind==='people'?'people':'sessions']:results.map(row=>({...JSON.parse(row.data),synced_at:row.updated_at})),limit,offset});
  }
  const session=path.match(/^\/v1\/attendance\/sessions\/([0-9]+)$/);
  if(session&&method==='GET'){
    const row=await stmt(env,"SELECT data,updated_at FROM snapshots WHERE source=? AND kind='session' AND entity=?",key.source,session[1]).first();
    if(!row)fail(404,'Session not synced');return json({session:JSON.parse(row.data),synced_at:row.updated_at});
  }
  if(key.scope!=='write')fail(403,'Write scope required');
  const record=path.match(/^\/v1\/attendance\/sessions\/([0-9]+)\/records\/([0-9]+)(\/movement)?$/);
  let command;
  if(record&&((method==='PATCH'&&!record[3])||(method==='POST'&&record[3]))){
    const data=await body(request);
    if(record[3]){if(!['check_in','check_out'].includes(data.action)||!text(data.note))fail(400,'Valid movement and staff note required');}
    else if(!['present','absent','pending'].includes(data.status))fail(400,'Invalid attendance status');
    command={kind:record[3]?'movement':'correction',data:{session_id:Number(record[1]),record_id:Number(record[2]),...(record[3]?{action:data.action,note:data.note}:{status:data.status})}};
  }else if(path==='/v1/people'&&method==='POST'){
    const data=await body(request);if(!text(data.name,80)||!['student','staff'].includes(data.person_type))fail(400,'Name and person_type required');
    const profile={};for(const field of ['name','person_type','admission_number','employee_code','grade','section','academic_year']){
      if(data[field]!==undefined){if(!text(data[field],80)&&data[field]!=='')fail(400,'Invalid profile field');profile[field]=data[field];}
    }
    command={kind:'profile',data:profile};
    const required=profile.person_type==='staff'?['employee_code']:['admission_number','grade','section','academic_year'];
    if(required.some(field=>!text(profile[field],80)))fail(400,'Complete required school profile fields');
  }else fail(404,'Route not found');
  const requestId=request.headers.get('Idempotency-Key');if(!text(requestId,100))fail(400,'Idempotency-Key header required for writes');
  const id=await hash(key.id+':'+requestId),encoded=JSON.stringify(command.data);
  const previous=await stmt(env,'SELECT kind,data FROM commands WHERE id=? AND source=?',id,key.source).first();
  if(previous&&(previous.kind!==command.kind||previous.data!==encoded))fail(409,'Idempotency key already used with different data');
  await stmt(env,'INSERT OR IGNORE INTO commands(id,source,kind,data,created_at) VALUES(?,?,?,?,?)',id,key.source,command.kind,encoded,new Date().toISOString()).run();
  return json({id,status:'pending',message:'Accepted for validation by the school server. Poll /v1/commands/'+id},202);
}
