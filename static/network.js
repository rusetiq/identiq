'use strict';
window.IdentiqNetwork=(()=>{
  let lastFailure=null;
  const endpoint=path=>path.split('?')[0].replace(/\/\d+(?=\/|$)/g,'/:id');
  const transient=error=>['network_unavailable','server_timeout','server_unavailable','invalid_response'].includes(error?.code);
  function failure(message,code,status){return Object.assign(new Error(message),{code,status});}
  async function request(path,{method='GET',body,headers={},signal,timeoutMs=10000}={}){
    const controller=new AbortController(),cancel=()=>controller.abort();
    if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',cancel,{once:true});
    const timer=setTimeout(cancel,timeoutMs);
    try{
      const response=await fetch(path,{method,signal:controller.signal,cache:'no-store',headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...headers},...(body!==undefined?{body:JSON.stringify(body)}:{})});
      if([408,500,502,503,504].includes(response.status))throw failure('The attendance server is temporarily unavailable. Reconnecting…','server_unavailable',response.status);
      let data;
      try{data=await response.json();}catch(error){
        if(error?.name==='AbortError')throw error;
        throw failure('The server returned an incomplete response. Open Connection check to check the running version.','invalid_response',response.status);
      }
      if(data===null || typeof data!=='object' || Array.isArray(data))throw failure('The server returned an unexpected response. Open Connection check.','invalid_response',response.status);
      if(!response.ok)throw failure(data.error || 'The request could not be completed.',data.code || 'request_error',response.status);
      if(lastFailure?.request===endpoint(path))lastFailure.recovered=true;
      return data;
    }catch(error){
      if(error?.name==='AbortError')error=failure(signal?.aborted?'Request cancelled.':'The server took too long to respond. Reconnecting…',signal?.aborted?'cancelled':'server_timeout');
      // Android browsers and errors from another realm do not always share TypeError's prototype.
      if(!error?.code && (error?.name==='TypeError' || /failed to fetch|load failed|networkerror|network request failed/i.test(error?.message || ''))){
        error=failure(method==='GET'?'Cannot reach the attendance laptop. Check its server and Wi-Fi.':'Connection interrupted. Check whether this action was saved before trying again.','network_unavailable');
      }
      if(error?.code!=='cancelled' && !path.startsWith('/connection-check'))lastFailure={request:endpoint(path),code:error.code || 'request_error',status:error.status || null,recovered:false};
      throw error;
    }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
  }
  return {request,transient,lastFailure:()=>lastFailure?{...lastFailure}:null};
})();
