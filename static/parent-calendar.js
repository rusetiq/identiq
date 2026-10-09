'use strict';
class ParentCalendar {
  constructor(onMonthChange){
    this.zone='Asia/Dubai';this.month=this.dateKey(new Date()).slice(0,7);this.selected=this.dateKey(new Date());this.records=[];this.byDay=new Map();this.onMonthChange=onMonthChange;
    document.getElementById('calendar-prev').onclick=()=>this.move(-1);
    document.getElementById('calendar-next').onclick=()=>this.move(1);
    document.getElementById('calendar-today').onclick=()=>{this.selected=this.dateKey(new Date());this.month=this.selected.slice(0,7);this.render();this.onMonthChange();};
  }
  dateKey(value){this.dateFormatter ||= new Intl.DateTimeFormat('en-CA',{timeZone:this.zone,year:'numeric',month:'2-digit',day:'2-digit'});return this.dateFormatter.format(new Date(value));}
  move(direction){
    const [year,month]=this.month.split('-').map(Number),next=new Date(Date.UTC(year,month-1+direction,1));
    if(next.getUTCFullYear()<1900 || next.getUTCFullYear()>2100)return;
    this.month=`${next.getUTCFullYear()}-${String(next.getUTCMonth()+1).padStart(2,'0')}`;this.selected=`${this.month}-01`;this.render();this.onMonthChange();
  }
  clear(){this.records=[];this.byDay.clear();document.getElementById('calendar-days').replaceChildren();document.getElementById('calendar-detail').replaceChildren();}
  update(data){
    if(data.timezone && data.timezone!==this.zone){this.zone=data.timezone;this.dateFormatter=null;}
    this.records=data.calendar || [];this.byDay.clear();
    for(const record of this.records){const key=this.dateKey(record.started_at);if(!this.byDay.has(key))this.byDay.set(key,[]);this.byDay.get(key).push(record);}
    this.render();
  }
  render(){
    const [year,month]=this.month.split('-').map(Number),start=new Date(Date.UTC(year,month-1,1)),total=new Date(Date.UTC(year,month,0)).getUTCDate();
    document.getElementById('calendar-month').textContent=start.toLocaleDateString(undefined,{timeZone:'UTC',month:'long',year:'numeric'});
    document.getElementById('calendar-prev').disabled=this.month==='1900-01';document.getElementById('calendar-next').disabled=this.month==='2100-12';
    document.getElementById('calendar-timezone').textContent=`Dates use ${this.zone.replaceAll('_',' ')}. Sessions appear on the date they started.`;
    const days=document.getElementById('calendar-days');days.replaceChildren();const today=this.dateKey(new Date());
    for(let offset=0;offset<start.getUTCDay();offset++){const blank=document.createElement('span');blank.className='calendar-blank';days.append(blank);}
    for(let day=1;day<=total;day++){
      const key=`${this.month}-${String(day).padStart(2,'0')}`,records=(this.byDay.get(key) || []),statuses=new Set(records.map(r=>r.status));
      const status=statuses.size>1?'mixed':statuses.values().next().value || 'none';
      const button=document.createElement('button');button.className=`calendar-day ${status}`;button.type='button';button.setAttribute('aria-pressed',String(this.selected===key));
      if(key===today)button.setAttribute('aria-current','date');
      const number=document.createElement('strong');number.textContent=day;const label=document.createElement('span');label.textContent=({present:'Present',absent:'Absent',pending:'Not seen',mixed:'Mixed',none:''})[status];
      button.setAttribute('aria-label',`${key}: ${records.length?label.textContent+', '+records.length+' session'+(records.length===1?'':'s'):'No recorded session'}`);
      button.append(number,label);button.onclick=()=>{this.selected=key;this.render();document.querySelector('#calendar-days [aria-pressed="true"]').focus();};days.append(button);
    }
    this.renderDetail();
  }
  renderDetail(){
    const holder=document.getElementById('calendar-detail');holder.replaceChildren();const heading=document.createElement('h3');
    heading.textContent=new Date(`${this.selected}T12:00:00Z`).toLocaleDateString(undefined,{timeZone:'UTC',weekday:'long',month:'long',day:'numeric',year:'numeric'});holder.append(heading);
    const records=(this.byDay.get(this.selected) || []);
    if(!records.length){const empty=document.createElement('p');empty.className='message';empty.textContent='No attendance session recorded for this date.';holder.append(empty);return;}
    for(const record of records){
      const row=document.createElement('div');row.className='calendar-session';const name=document.createElement('strong');name.textContent=record.session_name;
      const badge=document.createElement('span');badge.className=`status-badge ${record.status}`;badge.textContent=({present:'Present',absent:'Absent',pending:'Not seen yet'})[record.status];
      const detail=document.createElement('p');detail.className='message';const time=value=>new Date(value).toLocaleTimeString(undefined,{timeZone:this.zone,hour:'2-digit',minute:'2-digit'});
      detail.textContent=`Started ${time(record.started_at)} · ${record.session_status==='active'?'In progress':'Ended'} · ${record.checked_in_at?'Check-in '+time(record.checked_in_at)+(record.checked_out_at?' · Check-out '+time(record.checked_out_at):' · No check-out recorded'):record.method==='manual'?'Staff correction':'No check-in recorded'}`;
      row.append(name,badge,detail);
      for(const movement of record.movements || []){const item=document.createElement('p');item.className='message';item.textContent=`${movement.action.replaceAll('_',' ')} · ${time(movement.occurred_at)} · ${movement.source==='staff'?'Staff recorded':movement.source==='automatic'?'Direction scan':'Camera scan'}`;row.append(item);}
      holder.append(row);
    }
  }
}
