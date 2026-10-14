"use strict";
class FaceTracker {
  constructor({video,canvas,request,capture,onUpdate,pause=()=>false,guide=()=>null}) {
    Object.assign(this,{video,canvas,request,capture,onUpdate,pause,guide});
    this.generation=0;this.running=false;this.analysis=null;this.readyStreak=0;this.lastSample=0;
    this.box=null;this.matched=false;this.highlights=[];
  }
  start() {
    this.stop();this.running=true;const generation=++this.generation;
    this.status(null);this.check(generation);
  }
  async check(generation) {
    let delay=400;
    while(this.running && generation===this.generation) {
      if(this.pause()) {await new Promise(resolve=>setTimeout(resolve,150));continue;}
      try {
        const capturedAt=performance.now();
        const image=this.capture(),data=await this.request(image);
        if(!this.running || generation!==this.generation) return;
        this.accept(data,image,capturedAt);
        delay=data.face_count ? 400 : Math.min(1200,delay+200);
      }catch(error){
        if(!this.running || generation!==this.generation) return;
        this.readyStreak=0;this.analysis=null;this.highlights=[];
        this.status({status:'adjust',message:error.message,code:error.code || 'connection'});this.render();this.onUpdate();
        delay=['network_unavailable','server_timeout','server_unavailable'].includes(error.code)?Math.min(15000,Math.max(1000,delay*2)):400;
      }
      await new Promise(resolve=>setTimeout(resolve,delay));
    }
  }
  invalidate() {this.readyStreak=0;this.analysis=null;this.lastCapturedAt=performance.now();this.onUpdate();}
  accept(data,image,capturedAt=performance.now()) {
    if(!this.running || capturedAt<(this.lastCapturedAt || 0)) return;
    this.lastCapturedAt=capturedAt;
    this.readyStreak=data.status==='ready' && data.face_count===1 ? this.readyStreak+1 : 0;
    this.analysis={data,image,time:performance.now(),id:++this.lastSample};
    this.status(data);this.render();this.onUpdate();
  }
  status(data) {
    const ready=data?.status==='ready',panel=document.getElementById('tracking-status');
    panel.classList.toggle('ready',ready);panel.classList.toggle('adjust',!!data && !ready);
    document.getElementById('tracking-title').textContent=ready?data.face_count>1?`${data.face_count} faces detected`:'Face detected':data?.face_count>0?'Faces need adjustment':this.running?'Looking for faces':'Camera off';
    document.getElementById('tracking-detail').textContent=data?.message || (this.running?'Tracking starts when faces enter the frame.':'Enable camera to see live facial landmarks.');
    document.getElementById('tracking-metrics').hidden=!this.running;
    document.getElementById('face-metric').textContent=data?.faces?`${data.face_count} faces · ${data.ready_count || 0} ready`:data?.quality?`Face ${data.quality.face_size_px}px`:'No face yet';
    document.getElementById('clarity-metric').textContent='Local facial landmarks';
    document.getElementById('face-metric').title=data?.quality?`Normalized sharpness ${data.quality.sharpness}; brightness ${data.quality.brightness}`:'';
  }
  highlight(box,matched) {this.box=box;this.matched=matched;this.highlights=[];this.highlightTime=performance.now();this.render();}
  highlightGroup(faces) {this.box=null;this.highlights=faces;this.highlightTime=performance.now();this.render();}
  render() {
    const canvas=this.canvas,video=this.video;
    if(!video.videoWidth || !canvas.clientWidth) return;
    const width=Math.round(canvas.clientWidth*devicePixelRatio),height=Math.round(canvas.clientHeight*devicePixelRatio);
    if(canvas.width!==width || canvas.height!==height) {canvas.width=width;canvas.height=height;}
    const ctx=canvas.getContext('2d');ctx.clearRect(0,0,width,height);
    const guide=this.guide();
    if(guide){
      ctx.save();ctx.strokeStyle='rgba(255,255,255,.8)';ctx.lineWidth=2*devicePixelRatio;ctx.setLineDash([8*devicePixelRatio,8*devicePixelRatio]);ctx.beginPath();
      if(guide.axis==='x'){ctx.moveTo(width/2,0);ctx.lineTo(width/2,height);}else{ctx.moveTo(0,height/2);ctx.lineTo(width,height/2);}ctx.stroke();ctx.setLineDash([]);
      const labels=guide.axis==='x'?(guide.direction==='positive'?'CHECK IN →   /   ← CHECK OUT':'← CHECK IN   /   CHECK OUT →'):(guide.direction==='positive'?'CHECK IN ↓   /   ↑ CHECK OUT':'↑ CHECK IN   /   CHECK OUT ↓');
      ctx.font=`600 ${16*devicePixelRatio}px -apple-system, sans-serif`;const size=ctx.measureText(labels).width+24*devicePixelRatio;ctx.fillStyle='rgba(12,18,25,.9)';ctx.fillRect((width-size)/2,12*devicePixelRatio,size,34*devicePixelRatio);ctx.fillStyle='#fff';ctx.fillText(labels,(width-size)/2+12*devicePixelRatio,35*devicePixelRatio);ctx.restore();
    }
    const scale=Math.min(width/video.videoWidth,height/video.videoHeight),ox=(width-video.videoWidth*scale)/2,oy=(height-video.videoHeight*scale)/2;
    const point=p=>[ox+(1-p.x)*video.videoWidth*scale,oy+p.y*video.videoHeight*scale];
    const data=this.analysis && performance.now()-this.analysis.time<1800 ? this.analysis.data : null;
    const color=data?.status==='ready'?'#75edc5':'#efcd83';
    const highlighted=performance.now()-this.highlightTime<1800;
    const regions=highlighted && this.highlights.length ? this.highlights : highlighted && this.box ? [{box:this.box,matched:this.matched}] : data?.faces || (data?.box ? [data] : (data?.boxes || []).map(box=>({box})));
    for(const region of regions) {
      if(["unknown","empty"].includes(region.status))continue;
      const box=region.box;if(!box) continue;
      const [bx,by,bw,bh]=box,[x,y]=point({x:bx+bw,y:by}),w=bw*video.videoWidth*scale,h=bh*video.videoHeight*scale,length=Math.min(w,h)*.15;
      ctx.strokeStyle=region.matched?'#8ff0b6':['adjust','unknown','ambiguous','empty'].includes(region.status)?'#efcd83':color;ctx.lineWidth=2*devicePixelRatio;
      for(const[cx,cy,sx,sy]of[[x,y,1,1],[x+w,y,-1,1],[x,y+h,1,-1],[x+w,y+h,-1,-1]]){
        ctx.beginPath();ctx.moveTo(cx+length*sx,cy);ctx.lineTo(cx,cy);ctx.lineTo(cx,cy+length*sy);ctx.stroke();
      }
      if(region.label){
        const expanded=canvas.closest('.camera-expanded');
        const unit=devicePixelRatio,size=(expanded?Math.max(38,Math.min(84,canvas.clientWidth*.072)):Math.max(20,Math.min(36,canvas.clientWidth*.028)))*unit,pad=8*unit;
        ctx.font=`600 ${size}px -apple-system, sans-serif`;
        const available=Math.max(1,width-2*pad),chars=Array.from(region.label);let label=chars.join('');
        while(chars.length && ctx.measureText(label).width>available-2*pad){chars.pop();label=chars.join('')+'…';}
        const tw=Math.min(available,ctx.measureText(label).width+2*pad),th=size+2*pad;
        const tx=Math.max(pad,Math.min(x,width-tw-pad)),ty=Math.max(pad,Math.min(y-th-6*unit,height-th-pad));
        ctx.fillStyle='rgba(12,18,25,.88)';ctx.fillRect(tx,ty,tw,th);ctx.fillStyle=ctx.strokeStyle;ctx.fillText(label,tx+pad,ty+pad+size*.8);
      }
    }
    canvas.dataset.faceCount=String(data?.face_count || 0);
    let count=0;
    if(document.getElementById('show-mesh').checked) {
      for(const face of data?.faces || (data?.landmarks ? [data] : [])) {
        if(["unknown","empty"].includes(face.status))continue;
        const anchors=(face.landmarks || []).map(p=>({x:p[0],y:p[1]}));count+=anchors.length;
        ctx.strokeStyle='rgba(117,237,197,.45)';ctx.lineWidth=devicePixelRatio;ctx.beginPath();
        for(const[start,end]of[[0,2],[1,2],[2,3],[2,4],[3,4]]) {if(!anchors[start] || !anchors[end])continue;ctx.moveTo(...point(anchors[start]));ctx.lineTo(...point(anchors[end]));}ctx.stroke();
        ctx.fillStyle=face.status==='adjust'?'#efcd83':color;
        for(const p of anchors){ctx.beginPath();ctx.arc(...point(p),3*devicePixelRatio,0,2*Math.PI);ctx.fill();}
      }
    }
    canvas.dataset.landmarkCount=String(count);
  }
  stop() {
    this.running=false;this.generation++;this.analysis=null;this.readyStreak=0;this.box=null;this.matched=false;this.highlights=[];
    this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);this.canvas.dataset.landmarkCount='0';this.canvas.dataset.faceCount='0';
    this.status(null);
  }
}
