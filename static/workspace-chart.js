(function(root,factory){'use strict';const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceChart=api;}(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const NS='http://www.w3.org/2000/svg',INTERVAL={day:86400000,m30:1800000,m3:180000};
  const TYPES={entry:'点 3 允许区',danger:'危险区',obstacle:'障碍区',invalid:'失效区',middle:'区间中部'};
  const COLORS={entry:'#327fcd',danger:'#c17a32',obstacle:'#c17a32',invalid:'#8b61bf',middle:'#c17a32'};
  const PLAN_FIELDS=['planned_entry_price','stop_price'];
  const isForecast=object=>['wave','path'].includes(object?.kind);
  const finite=value=>typeof value==='number'&&Number.isFinite(value),PERIOD_RANK={day:0,m30:1,m3:2};
  const drawingOpacity=value=>finite(value)&&value>=0&&value<=1?value:1;
  const textWidth=(value,size)=>Array.from(String(value||'')).reduce((width,char)=>width+size*(char.codePointAt(0)>255?1:.6),0);
  function insertTextNewline(input){const start=input.selectionStart??input.value.length,end=input.selectionEnd??start,value=input.value.slice(0,start)+'\n'+input.value.slice(end);if(input.maxLength>0&&value.length>input.maxLength)return false;input.value=value;input.setSelectionRange?.(start+1,start+1);return true;}
  function pathPeriodOpacity(source,target){if(!source||source==='all')return 1;const from=PERIOD_RANK[source],to=PERIOD_RANK[target];if(from===undefined||to===undefined||to<from)return null;return Math.pow(.4,to-from);}
  function visibleOnPeriod(object,period){if(!object.period||object.period==='all')return true;if(['path','wave'].includes(object.kind))return pathPeriodOpacity(object.period,period)!==null;return object.period===period;}
  function timestamp(value){if(finite(value))return value;const parsed=Date.parse(value);return Number.isFinite(parsed)?parsed:null;}
  function snap(price,tick){if(!finite(price)||!finite(tick)||tick<=0)return price;return Number((Math.round(price/tick)*tick).toFixed(10));}
  function priceNumber(value){if(value===null||value===undefined||String(value).trim()==='')return null;const number=Number(value);return Number.isFinite(number)&&number>0?number:null;}
  function zoneBounds(object){if(!object||!Array.isArray(object.points)||object.points.length!==2)return null;const[a,b]=object.points;if(!finite(a.price)||!finite(b.price)||a.price<=0||b.price<=0||!finite(a.time)||!finite(b.time))return null;return{low:Math.min(a.price,b.price),high:Math.max(a.price,b.price),start:Math.min(a.time,b.time),end:Math.max(a.time,b.time)};}
  function timeScale(bars=[],period=null){
    const times=bars.map(bar=>timestamp(bar.time??bar.dt)).filter(finite),step=INTERVAL[period]||1800000;
    const position=time=>{
      if(!times.length)return time/step;
      if(time<=times[0])return (time-times[0])/step;
      const last=times.at(-1);if(time>=last)return times.length-1+(time-last)/step;
      let low=0,high=times.length-1;
      while(low+1<high){const middle=(low+high)>>1;if(times[middle]<=time)low=middle;else high=middle;}
      return low+(time-times[low])/(times[high]-times[low]);
    };
    const time=target=>{
      if(!times.length)return target*step;
      const last=times.at(-1);
      if(target<=0)return times[0]+target*step;
      if(target>=times.length-1)return last+(target-times.length+1)*step;
      const low=Math.floor(target),ratio=target-low;return times[low]+ratio*(times[low+1]-times[low]);
    };
    return{times,indices:new Map(times.map((value,index)=>[value,index])),position,time};
  }
  function barIndex(bars,time,after=false){let low=0,high=bars.length;while(low<high){const mid=(low+high)>>1;if(bars[mid].time<time||after&&bars[mid].time===time)low=mid+1;else high=mid;}return low;}
  function barsBetween(bars,start,end,inclusive=true){return bars.slice(barIndex(bars,start),barIndex(bars,end,inclusive));}
  function transform(view,plot,bars=[],period=null,preparedScale=null){
    const scale=preparedScale||timeScale(bars,period),times=scale.times,position=scale.position;
    const start=position(view.start),end=position(view.end),span=end-start;
    const x=time=>times.length<2?plot.x+(time-view.start)/(view.end-view.start)*plot.w:plot.x+(position(time)-start)/span*plot.w;
    const y=price=>plot.y+(view.max-price)/(view.max-view.min)*plot.h;
    const time=px=>{
      if(times.length<2)return view.start+(px-plot.x)/plot.w*(view.end-view.start);
      return scale.time(start+(px-plot.x)/plot.w*span);
    };
    return{x,y,time,price:py=>view.max-(py-plot.y)/plot.h*(view.max-view.min)};
  }
  function resolvePointTime(point,market,scales=null){
    const anchor=point?.chart_anchor;if(!anchor||!INTERVAL[anchor.period]||!finite(anchor.base_time)||!finite(anchor.offset))return point.time;
    const scale=scales?.[anchor.period]||timeScale(market?.periods?.[anchor.period]||[],anchor.period);
    if(!scale.indices.has(anchor.base_time))return point.time;
    return scale.time(scale.indices.get(anchor.base_time)+anchor.offset);
  }
  function anchorPoint(point,bars,period,preparedScale=null){
    const result={...point,chart_anchor:null};
    const scale=preparedScale||timeScale(bars,period),last=scale.times.at(-1);
    const offset=finite(last)?scale.position(point.time)-(scale.times.length-1):null;
    if(finite(last)&&point.time>last&&offset<=10000)result.chart_anchor={period,base_time:last,offset};
    return result;
  }
  function preserveView(view,previous,bars,period,follow=false,previousScale=null,nextScale=null){
    if(!view||!previous.length||!bars.length)return view;
    const before=previousScale||timeScale(previous,period),after=nextScale||timeScale(bars,period),base=before.times.at(-1);
    if(!after.indices.has(base))return view;
    const offset=after.position(base)-before.position(base),shift=follow?Math.max(0,after.position(after.times.at(-1))-after.position(base)):0;
    return{...view,start:after.time(before.position(view.start)+offset+shift),end:after.time(before.position(view.end)+offset+shift)};
  }
  function fitView(bars,period,levels=[]){const visible=[];for(let index=(bars||[]).length-1;index>=0&&visible.length<65;index--){const bar=bars[index],time=timestamp(bar.time??bar.dt);if(time!==null&&[bar.open,bar.high,bar.low,bar.close].every(finite))visible.push({...bar,time});}if(!visible.length)return null;visible.reverse();const last=visible.at(-1),step=INTERVAL[period];const prices=visible.flatMap(bar=>[bar.high,bar.low]).concat(levels.filter(value=>finite(value)&&value>0));let min=Math.min(...prices),max=Math.max(...prices);const pad=Math.max((max-min)*.12,max*.001,1e-5);return{start:visible[0].time-step,end:last.time+step*14,min:min-pad,max:max+pad};}
  function zoomView(view,period,scale,anchor=null,bars=[],maxSlots=10000){if(!view)return null;if(bars.length>1){const unit={x:0,y:0,w:1,h:1},tx=transform(view,unit,bars,period),ratio=finite(anchor?.time)?tx.x(anchor.time):.5,priceRatio=finite(anchor?.price)?(view.max-anchor.price)/(view.max-view.min):.5,price=finite(anchor?.price)?anchor.price:(view.min+view.max)/2,priceSpan=Math.max(1e-8,(view.max-view.min)/scale),slot=Math.abs(tx.x(bars.at(-1).time)-tx.x(bars.at(-2).time)),count=slot?1/slot:65,factor=Math.max(4,Math.min(Math.max(maxSlots,bars.length+14,count),count/scale))/count;return{start:tx.time(ratio-ratio*factor),end:tx.time(ratio+(1-ratio)*factor),min:price-(1-priceRatio)*priceSpan,max:price+priceRatio*priceSpan};}const step=INTERVAL[period]||1800000,oldTime=view.end-view.start,oldPrice=view.max-view.min,timeRange=Math.max(step*4,Math.min(Math.max(step*maxSlots,oldTime),oldTime/scale)),priceRange=Math.max(1e-8,oldPrice/scale),timeRatio=finite(anchor?.time)?(anchor.time-view.start)/oldTime:.5,priceRatio=finite(anchor?.price)?(view.max-anchor.price)/oldPrice:.5,centerTime=finite(anchor?.time)?anchor.time:(view.start+view.end)/2,centerPrice=finite(anchor?.price)?anchor.price:(view.min+view.max)/2;return{start:centerTime-timeRatio*timeRange,end:centerTime+(1-timeRatio)*timeRange,min:centerPrice-(1-priceRatio)*priceRange,max:centerPrice+priceRatio*priceRange};}
  function segmentDistance(point,a,b){const dx=b.x-a.x,dy=b.y-a.y,length=dx*dx+dy*dy,ratio=length?Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/length)):0;return Math.hypot(point.x-a.x-ratio*dx,point.y-a.y-ratio*dy);}
  function wavePoints(points){return points.map((p,index)=>({...p,id:p.id||('point-'+(globalThis.crypto?.randomUUID?.()||Math.random().toString(36).slice(2))),label:String(index+1)}));}
  function editPathPoints(points,segment,direction,tick,step){const next=JSON.parse(JSON.stringify(points));if(direction<0){if(next.length<=2)return next;if(Number.isInteger(segment)&&segment>=0&&segment<next.length-1)next.splice(segment+1,1);else next.pop();return next;}if(Number.isInteger(segment)&&segment>=0&&segment<next.length-1){const a=next[segment],b=next[segment+1];next.splice(segment+1,0,{time:Math.round((a.time+b.time)/2),price:snap((a.price+b.price)/2,tick)});return next;}const last=next.at(-1),previous=next.at(-2)||{time:last.time-step,price:last.price-tick*3};next.push({time:last.time+Math.max(step,last.time-previous.time),price:snap(Math.max(tick,last.price+(last.price-previous.price)),tick)});return next;}
  function dailyRange(bar,previous=null){
    if(!bar)return null;
    const label=bar.source_trade_day||String(bar.dt||'').slice(0,10),midnight=timestamp(label+'T00:00:00+08:00');
    if(midnight===null)return null;
    const previousLabel=previous?.source_trade_day||String(previous?.dt||'').slice(0,10),previousEnd=previousLabel?timestamp(previousLabel+'T18:00:00+08:00'):null;
    const boundary=timestamp(bar.session_boundary),start=previousEnd!==null?previousEnd:boundary!==null?boundary:midnight-6*3600000;
    return{start,end:midnight+18*3600000,label};
  }
  function rangeBars(bars,range){return range?barsBetween(bars,range.start,range.end,false):[];}
  function fitVisiblePrice(bars,view,tick=1,period="m30",drawings=[],pointTime=point=>point.time,preparedScale=null){
    if(!view)return view;
    const tx=transform(view,{x:0,y:0,w:1,h:1},bars,period,preparedScale),slot=bars.length>1?Math.abs(tx.x(bars[1].time)-tx.x(bars[0].time)):INTERVAL[period]/(view.end-view.start);
    const visible=barsBetween(bars,tx.time(-slot*.38),tx.time(1+slot*.38)).filter(b=>finite(b.high)&&finite(b.low));
    const prices=visible.flatMap(bar=>[bar.low,bar.high]);
    for(const object of drawings){
      if(object.visible===false||drawingOpacity(object.opacity)===0||!visibleOnPeriod(object,period)||!['wave','path','segment','zone'].includes(object.kind))continue;
      const points=(object.points||[]).map(point=>({x:tx.x(pointTime(point)),price:point.price})).filter(point=>finite(point.x)&&finite(point.price));
      if(object.kind==='zone'&&points.length===2){if(Math.min(...points.map(p=>p.x))<=1&&Math.max(...points.map(p=>p.x))>=0)prices.push(...points.map(p=>p.price));continue;}
      for(const point of points)if(point.x>=0&&point.x<=1)prices.push(point.price);
      for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i];if(a.x===b.x)continue;for(const edge of [0,1])if(Math.min(a.x,b.x)<edge&&Math.max(a.x,b.x)>edge)prices.push(a.price+(b.price-a.price)*(edge-a.x)/(b.x-a.x));}
    }
    if(!prices.length)return view;
    const low=Math.min(...prices),high=Math.max(...prices),pad=Math.max((high-low)*.08,tick*2,high*.00002);
    return{...view,min:low-pad,max:high+pad};
  }
  function candleWidth(bars,tx,plot,period){
    const first=barIndex(bars,tx.time(plot.x)),index=first<bars.length&&tx.x(bars[first].time)<=plot.x+plot.w?first:0,bar=bars[index];
    if(!bar)return 1;
    const next=bars[index+1],previous=bars[index-1],slot=next?Math.abs(tx.x(next.time)-tx.x(bar.time)):previous?Math.abs(tx.x(bar.time)-tx.x(previous.time)):Math.abs(tx.x(bar.time+INTERVAL[period])-tx.x(bar.time));
    return Math.max(1,Math.min(plot.w*.8,slot*.76));
  }
  function validStopSide(direction,entry,stop){entry=priceNumber(entry);stop=priceNumber(stop);return Boolean(entry&&stop&&(direction==='long'?stop<entry:direction==='short'?stop>entry:false));}
  function planLevelStatus(plan){if(!priceNumber(plan.planned_entry_price))return{state:'missing-entry',text:'未设入场'};if(!['long','short'].includes(plan.direction))return{state:'missing-direction',text:'未选多空'};if(!priceNumber(plan.stop_price))return{state:'missing-stop',text:'未设止损'};return validStopSide(plan.direction,plan.planned_entry_price,plan.stop_price)?{state:'valid',text:''}:{state:'invalid-stop',text:plan.direction==='long'?'止损须低于计划入场价 · 方向仍为做多':'止损须高于计划入场价 · 方向仍为做空'};}
  function planLevelGeometry(plan,tx,plot,width){const levels=PLAN_FIELDS.map(field=>({field,price:priceNumber(plan[field])})).filter(object=>object.price).map(object=>({...object,y:tx.y(object.price)})).filter(object=>object.y>=plot.y&&object.y<=plot.y+plot.h).sort((a,b)=>a.y-b.y),height=28,gap=3;let previous=-Infinity;for(const level of levels){let y=Math.max(plot.y,Math.min(plot.y+plot.h-height,level.y-height/2));y=Math.max(y,previous+height+gap);level.label={x:plot.x+plot.w+2,y,w:Math.max(40,width-(plot.x+plot.w)-5),h:height};previous=y;}if(levels.length&&levels.at(-1).label.y+height>plot.y+plot.h){const shift=levels.at(-1).label.y+height-plot.y-plot.h;for(const level of levels)level.label.y-=shift;}return levels;}
  function hitPlanLevel(point,levels,plot,width){for(const level of levels){const rect=level.label;if(point.x>=rect.x-3&&point.x<=Math.min(width,rect.x+rect.w+3)&&point.y>=rect.y-4&&point.y<=rect.y+rect.h+4)return{...level,target:'label'};}if(point.x<plot.x||point.x>plot.x+plot.w)return null;let closest=null;for(const level of levels){const distance=Math.abs(point.y-level.y);if(distance<=12&&(!closest||distance<closest.distance))closest={...level,target:'line',distance};}return closest;}
  function beginLevelDrag(field,price,tick){if(!PLAN_FIELDS.includes(field)||!priceNumber(price))return null;const value=snap(priceNumber(price),tick);return{kind:'plan-level',field,originPrice:value,price:value,moved:false};}
  function moveLevelDrag(drag,price,tick){if(!drag||!PLAN_FIELDS.includes(drag.field)||!priceNumber(price))return drag;const value=snap(Math.max(tick,price),tick);return{...drag,price:value,moved:value!==drag.originPrice};}
  function levelCommit(drag,cancelled=false){return!cancelled&&drag?.moved&&PLAN_FIELDS.includes(drag.field)?{[drag.field]:drag.price}:{};}
  function mergePending(remote,pending){const plan=JSON.parse(JSON.stringify(remote||{}));Object.assign(plan,pending.changes||{});const removed=new Set(pending.delete_object_ids||[]),upserts=new Map((pending.upsert_objects||[]).map(object=>[object.id,object]));plan.objects=(plan.objects||[]).filter(object=>!removed.has(object.id)).map(object=>upserts.has(object.id)?upserts.get(object.id):object);const ids=new Set(plan.objects.map(object=>object.id));for(const[id,object]of upserts)if(!ids.has(id)&&!removed.has(id))plan.objects.push(object);return plan;}
  function pendingAfterAck(current,sent){const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b),changes={};for(const[key,value]of Object.entries(current.changes||{}))if(!Object.hasOwn(sent.changes||{},key)||!equal(value,sent.changes[key]))changes[key]=value;const sentObjects=new Map((sent.upsert_objects||[]).map(object=>[object.id,object]));return{changes,upsert_objects:(current.upsert_objects||[]).filter(object=>!sentObjects.has(object.id)||!equal(object,sentObjects.get(object.id))),delete_object_ids:(current.delete_object_ids||[]).filter(id=>!(sent.delete_object_ids||[]).includes(id))};}
  function adoptRemoteConflicts(pending,conflicts){const keys=new Set(conflicts);if(keys.has('base_revision'))return{changes:{},upsert_objects:[],delete_object_ids:[]};return{changes:Object.fromEntries(Object.entries(pending.changes||{}).filter(([key])=>!keys.has(key))),upsert_objects:(pending.upsert_objects||[]).filter(object=>!keys.has('objects:'+object.id)),delete_object_ids:(pending.delete_object_ids||[]).filter(id=>!keys.has('objects:'+id))};}
  function createSVG(tag,attrs={},text){const element=document.createElementNS(NS,tag);for(const[key,value]of Object.entries(attrs))element.setAttribute(key,String(value));if(text!==undefined)element.textContent=String(text);return element;}
  function priceLabel(value,tick){return finite(value)?value.toLocaleString('en-US',{maximumFractionDigits:Math.min(6,Math.max(0,(String(tick||1).split('.')[1]||'').length))}):'—';}
  function movingAverages(bars,length){let sum=0;return bars.map((bar,index)=>{sum+=bar.close;if(index>=length)sum-=bars[index-length].close;return index>=length-1?{time:bar.time,value:sum/length}:null;});}
  function infiniteSegment(a,b,plot){const dx=b.x-a.x,dy=b.y-a.y;if(Math.abs(dx)<.001)return[{x:a.x,y:plot.y},{x:a.x,y:plot.y+plot.h}];const at=x=>({x,y:a.y+(x-a.x)*dy/dx});return[at(plot.x),at(plot.x+plot.w)];}
  class Chart{
    constructor(host,period,callbacks={}){
      this.host=host;this.period=period;this.callbacks=callbacks;this.bars=[];this.plan={objects:[]};this.view=null;this.tick=1;this.tool='select';this.toolOpacity=1;this.autoPrice=true;this.selected=null;this.selectedAnchor=null;this.path=[];this.drag=null;this.market=null;this.size={w:0,h:0};this.uid='chart-'+period;this.readOnly=false;this.hostTouchAction=host.style.touchAction||'';
      this.svg=createSVG('svg',{'aria-label':period+' K线、指标与共享绘图',role:'img'});this.svg.style.touchAction='none';host.replaceChildren(this.svg);this.observer=new ResizeObserver(()=>{if(this.pinch&&(this.pinch.width!==Math.round(host.clientWidth)||this.pinch.height!==Math.round(host.clientHeight))){this.touchBlocked=true;this.finishPinch();}this.render();});this.observer.observe(host);
      this.svg.addEventListener('pointerdown',event=>this.down(event));this.svg.addEventListener('pointermove',event=>this.move(event));this.svg.addEventListener('pointerup',event=>this.up(event));
      this.svg.addEventListener('pointercancel',()=>this.cancelGesture());this.svg.addEventListener('lostpointercapture',()=>{if(this.drag)this.cancelGesture();});this.svg.addEventListener('pointerleave',()=>{this.hoverPath=null;this.pathControl=null;this.snapPreview=null;this.crosshair=null;this.hoverDayKey=null;this.callbacks.hoverDay?.(null);if(!this.drag){this.clearPresence();}if(!this.drag){this.placementPreview=null;this.callbacks.previewLevel?.(null,null);this.render();}});
      this.svg.addEventListener('dblclick',event=>this.doubleClick(event));this.host.addEventListener('contextmenu',event=>this.contextMenu(event));
      this.svg.addEventListener('wheel',event=>{if(this.readOnly)return;event.preventDefault();this.callbacks.interaction?.();this.callbacks.activate?.(period);if(!this.drag)this.zoom(event.deltaY>0?.9:1.11,this.point(event));},{passive:false});
      host.addEventListener('keydown',event=>this.keydown(event));
      this.touchIds=new Set();
      // Track outside the SVG: its candle children are replaced on every redraw.
      document.addEventListener?.('touchstart',event=>{if(this.pinch&&(event.touches.length!==2||!this.pinch.ids.every(id=>Array.from(event.touches).some(touch=>touch.identifier===id)))){this.touchBlocked=true;this.finishPinch();}},{passive:false});
      document.addEventListener?.('touchmove',event=>this.touchMove(event),{passive:false});
      document.addEventListener?.('touchend',event=>this.touchEnd(event),{passive:false});
      document.addEventListener?.('touchcancel',event=>this.touchEnd(event,true),{passive:false});
    }
    setData(market,plan,reset=false){
      if(reset||market?.symbol&&this.market?.symbol&&market.symbol!==this.market.symbol){this.clearTouchGesture({applyMarket:false});this.pendingMarket=null;}
      if(reset){this.linkedRange=null;this.hoverDayKey=null;this.crosshair=null;this.pendingMarket=null;}
      if(!reset&&(this.drag||this.path.length||this.pinch)){this.pendingMarket=market;this.plan=plan||{objects:[]};this.render();return;}
      const previousBars=this.bars,previousScale=this.timelineBars===previousBars?this.timelineScale:null,planChanged=this.plan!==plan&&JSON.stringify(this.plan)!==JSON.stringify(plan||{objects:[]});
      this.market=market;this.plan=plan||{objects:[]};this.tick=market?.tick||1;this.anchorScales={};this.syncTitleEditor();
      const source=market?.periods?.[this.period]||[],rowKey=bar=>bar?['dt','time','open','high','low','close','volume','oi'].map(key=>bar[key]).join(':'):'',sourceSignature=[market?.symbol,market?.history_token,source.length,rowKey(source[0]),rowKey(source.at(-2)),rowKey(source.at(-1))].join('|');
      if(source!==this.sourceBars||sourceSignature!==this.sourceSignature){
        const normalized=new WeakMap(),previous=this.normalizedRows;this.bars=[];
        for(const raw of source){const cached=Object.isFrozen(raw)&&previous?.get(raw),bar=cached||{...raw,time:timestamp(raw.dt??raw.time)};if(bar.time!==null&&[bar.open,bar.high,bar.low,bar.close].every(finite)){this.bars.push(bar);normalized.set(raw,bar);}}
        this.normalizedRows=normalized;
      }
      this.sourceBars=source;this.sourceSignature=sourceSignature;
      // Mapped forecasts also depend on their source timeframe. Its opening may
      // arrive before this chart's next candle, so invalidate that projection too.
      const anchorTimelineSignature=Object.keys(INTERVAL).filter(period=>period!==this.period).map(period=>{const rows=market?.periods?.[period]||[];return[period,rows.length,...[rows[0],rows.at(-2),rows.at(-1)].map(row=>timestamp(row?.time??row?.dt))].join(':');}).join('|');
      const first=this.bars[0],last=this.bars.at(-1),previousLastTime=this.lastBarTime,signature=first&&last?`${first.time}:${first.open}:${last.time}:${this.bars.length}`:'',tailSignature=this.bars.slice(-2).map(bar=>['time','open','high','low','close','volume','oi'].map(key=>bar[key]).join(':')).join('|'),contentSignature=signature+`:${tailSignature}:${anchorTimelineSignature}:${market?.history_token}:${market?.fresh}:${market?.feed_status}:${market?.unavailable}`,unchanged=contentSignature===this.contentSignature;
      this.contentSignature=contentSignature;this.dataSignature=signature;
      if(!reset&&this.view&&previousBars.length&&signature!==this.previousTimelineSignature)this.view=preserveView(this.view,previousBars,this.bars,this.period,this.viewCustomized!==true&&this.followLatest!==false,previousScale,this.getTimeScale());
      this.previousTimelineSignature=signature;
      if(reset||!this.view)this.fit();
      else if(this.viewCustomized===true||this.followLatest===false){if(!unchanged||planChanged)this.render();}
      else if(last){
        let changed=false;
        if(finite(previousLastTime)&&last.time>previousLastTime)changed=true;
        const padding=(this.view.max-this.view.min)*.03;
        if(last.close>this.view.max){const delta=last.close-this.view.max+padding;this.view={...this.view,min:this.view.min+delta,max:this.view.max+delta};changed=true;}
        else if(last.close<this.view.min){const delta=last.close-this.view.min-padding;this.view={...this.view,min:this.view.min+delta,max:this.view.max+delta};changed=true;}
        if(changed||!unchanged||planChanged)this.render();
      }else if(!unchanged||planChanged)this.render();
      this.lastBarTime=last?.time??null;
    }
    visibleBars(margin=4){if(!this.view)return this.bars;const view=this.view,range=view.end-view.start,start=view.start-range*.03-INTERVAL[this.period]*margin,end=view.end+INTERVAL[this.period]*margin;return barsBetween(this.bars,start,end);}
    setLinkedRange(range){if(JSON.stringify(this.linkedRange)===JSON.stringify(range))return;this.linkedRange=range;this.render();}
    dayAt(point){
      if(this.period!=='day'||!this.tx||point.x<this.plot.x||point.x>this.plot.x+this.plot.w||point.y<this.plot.y||point.y>this.plot.y+this.plot.h)return null;
      let closest=-1,distance=Infinity;const at=barIndex(this.bars,this.tx.time(point.x));for(const i of [at-1,at]){if(i<0||i>=this.bars.length)continue;const dx=Math.abs(this.tx.x(this.bars[i].time)-point.x);if(dx<distance){distance=dx;closest=i;}}
      if(closest<0)return null;const bar=this.bars[closest],neighbor=this.bars[closest+1]||this.bars[closest-1],width=neighbor?Math.abs(this.tx.x(neighbor.time)-this.tx.x(bar.time)):8;
      return distance<=Math.max(3,width/2)?dailyRange(bar,this.bars[closest-1]):null;
    }
    drawLinkedRange(layer){
      const range=this.linkedRange;if(!range)return;
      const matches=rangeBars(this.bars,range),plot=this.plot,tx=this.tx;
      this.svg.append(createSVG('text',{'data-linked-day-label':range.label,x:plot.x+7,y:plot.y+13,fill:'#a1d2f0','font-size':10},range.label+(matches.length?(matches.at(-1).time<this.view.start?' · 对应区域在左侧':matches[0].time>this.view.end?' · 对应区域在右侧':` · 对应 ${matches.length} 根`):' · 此周期暂无对应数据')));
      if(!matches.length)return;
      const index=this.bars.indexOf(matches[0]),lastIndex=this.bars.indexOf(matches.at(-1)),firstX=tx.x(matches[0].time),lastX=tx.x(matches.at(-1).time),halfBefore=index>0?(firstX-tx.x(this.bars[index-1].time))/2:Math.abs(tx.x(matches[0].time+INTERVAL[this.period])-firstX)/2,halfAfter=lastIndex<this.bars.length-1?(tx.x(this.bars[lastIndex+1].time)-lastX)/2:Math.abs(tx.x(matches.at(-1).time+INTERVAL[this.period])-lastX)/2;
      const x=Math.max(plot.x,firstX-halfBefore),right=Math.min(plot.x+plot.w,lastX+halfAfter);if(right<=x)return;
      layer.append(createSVG('rect',{'data-linked-day':range.label,x,y:plot.y,width:right-x,height:plot.h,fill:'#509fce','fill-opacity':'.18',stroke:'#73b1d6','stroke-width':1,'stroke-dasharray':'4 3','pointer-events':'none'}));
      this.svg.append(createSVG('rect',{'data-linked-volume':range.label,x,y:this.indicator.y,width:right-x,height:this.indicator.h,fill:'#509fce','fill-opacity':'.12','pointer-events':'none'}));
    }
    setPresence(peers){const next=peers||[],signature=JSON.stringify(next);if(signature===this.presenceSignature)return;this.presenceSignature=signature;this.peers=next;if(this.peerLayer&&this.tx)this.renderPresence();}
    setReadOnly(enabled){enabled=Boolean(enabled);if(enabled===this.readOnly)return;this.readOnly=enabled;this.host.setAttribute?.('data-read-only',String(enabled));this.host.style.touchAction=enabled?'pan-y':this.hostTouchAction;this.svg.style.touchAction=enabled?'pan-y':'none';if(enabled)this.ensureTouchSurface();if(this.touchSurface)this.touchSurface.hidden=!enabled;this.beginUpdate();try{this.setTool('select');if(this.capturedPointerId!=null)this.releasePointer({pointerId:this.capturedPointerId});this.selected=null;this.selectedAnchor=null;this.selectedLevel=null;this.hoverLevel=null;this.lastTitleClick=null;this.secondaryPress=null;this.pathControls=null;this.clearPresence();this.render();}finally{this.endUpdate();}}
    ensureTouchSurface(){
      if(this.touchSurface)return;
      const surface=document.createElement('div');surface.setAttribute('aria-hidden','true');surface.setAttribute('data-chart-touch',this.period);
      Object.assign(surface.style,{position:'absolute',inset:'0',zIndex:'1',touchAction:'pan-y'});this.host.append(surface);this.touchSurface=surface;
      surface.addEventListener('pointerdown',event=>this.down(event));surface.addEventListener('pointermove',event=>this.move(event));surface.addEventListener('pointerup',event=>this.up(event));
      surface.addEventListener('pointerleave',()=>{if(!this.pinch){this.crosshair=null;this.hoverDayKey=null;this.callbacks.hoverDay?.(null);this.clearPresence();this.scheduleRender();}});
      surface.addEventListener('touchstart',event=>this.touchStart(event),{passive:false});
    }
    touchStart(event){
      if(!this.readOnly)return;
      for(const touch of Array.from(event.changedTouches||[]))if(touch.target===this.touchSurface)this.touchIds.add(touch.identifier);
      const touches=Array.from(event.touches||[]);if(touches.length<2)return;
      if(event.cancelable!==false)event.preventDefault();
      if(this.pinch||this.touchBlocked)return;
      if(touches.length!==2||event.cancelable===false||!touches.every(touch=>this.touchIds.has(touch.identifier))||!this.view||!this.tx){this.touchBlocked=true;return;}
      const points=touches.map(touch=>this.point(touch)),plot={...this.plot},inside=point=>finite(point.x)&&finite(point.y)&&point.x>=plot.x&&point.x<=plot.x+plot.w&&point.y>=plot.y&&point.y<=plot.y+plot.h,distance=Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y);
      if(!points.every(inside)||distance<8){this.touchBlocked=true;return;}
      const midpoint={x:(points[0].x+points[1].x)/2,y:(points[0].y+points[1].y)/2};
      this.callbacks.interaction?.();this.callbacks.activate?.(this.period);
      this.pinch={ids:touches.map(touch=>touch.identifier),distance,midpoint,plot,width:Math.round(this.host.clientWidth),height:Math.round(this.host.clientHeight),view:{...this.view},anchor:{time:this.tx.time(midpoint.x),price:this.tx.price(midpoint.y)},bars:this.bars,scale:this.getTimeScale(),limit:this.zoomLimit(),autoPrice:this.autoPrice,changed:false};this.autoPrice=false;
      this.crosshair=null;this.hoverDayKey=null;this.callbacks.hoverDay?.(null);this.clearPresence();this.scheduleRender();
    }
    touchMove(event){
      const gesture=this.pinch;if(!this.readOnly||!gesture)return;
      const touches=Array.from(event.touches||[]);
      if(event.cancelable===false||touches.length!==2||!gesture.ids.every(id=>touches.some(touch=>touch.identifier===id))){this.touchBlocked=true;this.finishPinch();return;}
      event.preventDefault();const points=gesture.ids.map(id=>this.point(touches.find(touch=>touch.identifier===id)));
      if(!points.every(point=>finite(point.x)&&finite(point.y))){this.touchBlocked=true;this.finishPinch();return;}
      const midpoint={x:(points[0].x+points[1].x)/2,y:(points[0].y+points[1].y)/2},distance=Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y),factor=Math.max(.02,Math.min(50,distance/gesture.distance));
      const view=zoomView(gesture.view,this.period,factor,gesture.anchor,gesture.bars,gesture.limit),scale=gesture.scale,span=scale.position(view.end)-scale.position(view.start),shift=(midpoint.x-gesture.midpoint.x)/gesture.plot.w*span,priceShift=(midpoint.y-gesture.midpoint.y)/gesture.plot.h*(view.max-view.min);
      const next={start:scale.time(scale.position(view.start)-shift),end:scale.time(scale.position(view.end)-shift),min:view.min+priceShift,max:view.max+priceShift};
      if(!Object.values(next).every(finite)||next.end<=next.start||next.max<=next.min)return;
      if(['start','end','min','max'].every(key=>next[key]===this.view[key]))return;
      this.view=next;this.autoPrice=false;this.followLatest=false;this.viewCustomized=true;gesture.changed=true;this.scheduleRender();
    }
    touchEnd(event,cancelled=false){
      if(!this.touchIds?.size&&!this.pinch)return;
      for(const touch of Array.from(event.changedTouches||[]))this.touchIds.delete(touch.identifier);
      const touches=Array.from(event.touches||[]);
      if(this.pinch&&(cancelled||touches.length!==2||!this.pinch.ids.every(id=>touches.some(touch=>touch.identifier===id)))){this.touchBlocked=true;this.finishPinch();}
      if(cancelled||!touches.length)this.touchIds.clear();
      if(!this.touchIds.size)this.touchBlocked=false;
    }
    finishPinch({notify=true,applyMarket=true}={}){const gesture=this.pinch;this.pinch=null;if(!gesture)return;if(!gesture.changed)this.autoPrice=gesture.autoPrice;this.crosshair=null;if(applyMarket)this.applyPendingMarket();if(notify&&gesture.changed)this.callbacks.viewChanged?.(this);this.scheduleRender();}
    clearTouchGesture({applyMarket=true}={}){this.finishPinch({notify:false,applyMarket});this.touchIds?.clear();this.touchBlocked=false;}
    beginUpdate(){this.updateDepth=(this.updateDepth||0)+1;}
    endUpdate(){this.updateDepth=Math.max(0,(this.updateDepth||0)-1);if(!this.updateDepth&&this.renderDeferred){this.renderDeferred=false;this.render();}}
    setPlan(plan){this.plan=plan||{objects:[]};this.syncTitleEditor();this.render();}
    setTool(tool,options={}){this.clearTouchGesture({applyMarket:false});if(this.readOnly)tool='select';this.snapPreview=null;this.lastPathClick=null;if(this.period==='day'&&this.hoverDayKey){this.hoverDayKey=null;this.callbacks.hoverDay?.(null);}if(this.drag)this.cancelGesture();this.closeTextEditor();this.closeTitleEditor();this.cancelPath({render:false});this.placementPreview=null;this.tool=tool;this.toolDirection=['long','short'].includes(options.direction)?options.direction:null;this.toolName=options.name||null;this.toolColor=options.color||null;this.toolOpacity=drawingOpacity(options.opacity);this.hoverPath=null;this.pathControl=null;this.insertWave=null;this.draft=null;this.host.style.cursor=tool==='pan'?'grab':tool==='select'?'default':'crosshair';this.render();}
    fit(includeDrawings=false){
      this.followLatest=true;this.viewCustomized=false;this.autoPrice=!includeDrawings;
      const drawings=(this.plan.objects||[]).filter(object=>object.visible!==false&&visibleOnPeriod(object,this.period)),levels=includeDrawings?[this.plan.entry_low,this.plan.entry_high,this.plan.stop_price,this.plan.planned_entry_price,this.plan.main_level,this.plan.trigger_price,this.plan.obstacle_price,...drawings.flatMap(object=>object.points.map(point=>point.price))]:[];
      this.view=fitView(this.bars,this.period,levels);
      if(includeDrawings&&this.view){const points=drawings.flatMap(object=>object.points);if(points.length){const start=Math.min(this.view.start,...points.map(point=>this.pointTime(point))),end=Math.max(this.view.end,...points.map(point=>this.pointTime(point))),padding=Math.max(INTERVAL[this.period],(end-start)*.03);this.view={...this.view,start:start-padding,end:end+padding};}}
      this.render();this.callbacks.viewChanged?.(this);
    }
    zoom(scale,point=null){if(!this.view)return;const inside=point&&this.plot&&point.x>=this.plot.x&&point.x<=this.plot.x+this.plot.w&&point.y>=this.plot.y&&point.y<=this.plot.y+this.plot.h,rightBar=this.bars.filter(b=>b.time>=this.view.start&&b.time<=this.view.end).at(-1),anchor=inside?this.toValue(point,false):rightBar?{time:rightBar.time,price:rightBar.close}:null;this.view=this.priceFit(zoomView(this.view,this.period,scale,anchor,this.bars,this.zoomLimit()));this.autoPrice=true;this.viewCustomized=true;this.callbacks.viewChanged?.(this);this.render();}
    getTimeScale(){if(this.timelineBars!==this.bars||this.timelinePeriod!==this.period){this.timelineBars=this.bars;this.timelinePeriod=this.period;this.timelineScale=timeScale(this.bars,this.period);}return this.timelineScale;}
    getAverages(length){if(this.averageBars!==this.bars){this.averageBars=this.bars;this.averages=new Map();}if(!this.averages.has(length))this.averages.set(length,movingAverages(this.bars,length));return this.averages.get(length);}
    pointTime(point){this.anchorScales??={};const period=point?.chart_anchor?.period;if(period&&INTERVAL[period]&&!this.anchorScales[period])this.anchorScales[period]=period===this.period?this.getTimeScale():timeScale(this.market?.periods?.[period]||[],period);return resolvePointTime(point,this.market,this.anchorScales);}
    zoomLimit(){const scale=this.getTimeScale();let left=0,right=this.bars.length;for(const object of this.objects())for(const point of object.points||[]){const position=scale.position(this.pointTime(point));if(finite(position)){left=Math.min(left,position);right=Math.max(right,position);}}return Math.max(10000,(right-left)*1.4+30);}
    forecastPoint(point){return anchorPoint(point,this.bars,this.period,this.getTimeScale());}
    applyPendingMarket(){if(this.pendingMarket&&!this.drag&&!this.path.length&&!this.pinch){const market=this.pendingMarket;this.pendingMarket=null;this.setData(market,this.plan);}}
    editForecastPoints(object,segment,direction){
      // Preserve original coordinates by provenance, not geometric equality: an inserted
      // midpoint may coincide with another legacy node that has no stable ID.
      const rendered=object.points.map((point,index)=>({...point,time:this.pointTime(point),sourceIndex:index})),edited=editPathPoints(rendered,segment,direction,this.tick,INTERVAL[this.period]);
      return edited.map(({sourceIndex,...point})=>Number.isInteger(sourceIndex)?{...object.points[sourceIndex]}:this.forecastPoint(point));
    }
    priceFit(view){return fitVisiblePrice(this.bars,view,this.tick,this.period,this.objects(),point=>this.pointTime(point),this.getTimeScale());}
    locateObject(object){const points=object?.points||[];if(!points.length||!this.view)return;const times=points.map(point=>this.pointTime(point)),prices=points.map(point=>point.price),scale=this.getTimeScale(),positions=times.map(time=>scale.position(time)),left=Math.min(...positions),right=Math.max(...positions),span=Math.max(8,(right-left)*1.3),low=Math.min(...prices),high=Math.max(...prices),pad=Math.max(this.tick*4,(high-low)*.15);this.view={start:scale.time((left+right-span)/2),end:scale.time((left+right+span)/2),min:low-pad,max:high+pad};this.autoPrice=false;this.followLatest=false;this.viewCustomized=true;this.render();}
    point(event){const bounds=this.svg.getBoundingClientRect(),box=this.svg.viewBox?.baseVal,sx=box?.width&&bounds.width?box.width/bounds.width:1,sy=box?.height&&bounds.height?box.height/bounds.height:1;return{x:(event.clientX-bounds.left)*sx+(box?.x||0),y:(event.clientY-bounds.top)*sy+(box?.y||0)};}
    snapTarget(point){
      if(this.callbacks.snapEnabled&&!this.callbacks.snapEnabled()||point.x<this.plot.x||point.x>this.plot.x+this.plot.w)return null;
      const radius=Math.max(18,candleWidth(this.bars,this.tx,this.plot,this.period)/.76*.75);let target=null,distance=Infinity;
      for(const bar of this.visibleBars(6)){const x=this.tx.x(bar.time),dx=Math.abs(x-point.x);if(x<this.plot.x||x>this.plot.x+this.plot.w||dx>radius)continue;for(const field of ['open','high','low','close']){if(!finite(bar[field]))continue;const d=Math.hypot(dx,this.tx.y(bar[field])-point.y);if(d<distance){distance=d;target={time:bar.time,price:bar[field],field};}}}
      return target;
    }
    toValue(point,useSnap=true,freePrice=false){const target=useSnap?this.snapTarget(point):null;return target?{time:target.time,price:target.price}:{time:Math.max(0,Math.round(this.tx.time(point.x))),price:freePrice?Math.max(Number.EPSILON,this.tx.price(point.y)):snap(Math.max(this.tick,this.tx.price(point.y)),this.tick)};}
    objects(){return(this.plan.objects||[]).map((o,i)=>({o,rank:Number.isFinite(o.z_index)?o.z_index:i})).sort((a,b)=>a.rank-b.rank).map(item=>item.o).filter(object=>object.visible!==false&&visibleOnPeriod(object,this.period));}
    objectPoints(object){return(object.points||[]).map(point=>({x:this.tx.x(this.pointTime(point)),y:this.tx.y(point.price)}));}
    hit(point){
      const objects=this.objects().slice().reverse();let closest=null;
      for(const object of objects){if((!isForecast(object)&&object.id!==this.selected)||object.kind==='zone')continue;const points=this.objectPoints(object);for(let index=0;index<points.length;index++){const distance=Math.hypot(point.x-points[index].x,point.y-points[index].y);if(distance<=12&&(!closest||distance<closest.distance))closest={object,anchor:index,distance};}}
      if(closest)return closest;
      for(const object of [...objects.filter(o=>o.kind!=='zone'),...objects.filter(o=>o.kind==='zone')]){const points=this.objectPoints(object);
        if(object.kind==='zone'){const bounds=zoneBounds(object);if(bounds&&point.x>=this.tx.x(bounds.start)-5&&point.x<=this.tx.x(bounds.end)+5&&point.y>=this.tx.y(bounds.high)-5&&point.y<=this.tx.y(bounds.low)+5)return{object,anchor:null};}
        else if(object.kind==='horizontal'&&points[0]&&Math.abs(point.y-points[0].y)<7)return{object,anchor:null};
        else if(object.kind==='text'&&points[0]&&(this.titleContains(object,point)||Math.hypot(point.x-points[0].x,point.y-points[0].y)<=8))return{object,anchor:null};
        else if(['trend','reference'].includes(object.kind)&&points.length>=2){const segment=infiniteSegment(points[0],points[1],this.plot);if(segmentDistance(point,segment[0],segment[1])<8)return{object,anchor:null};}
        else for(let index=1;index<points.length;index++)if(segmentDistance(point,points[index-1],points[index])<8)return{object,anchor:null,segment:index-1};
      }return null;
    }
    displayPlan(){let plan=this.plan;if(this.readOnly)return plan;if(this.placementPreview&&['planned-entry','planned-stop'].includes(this.tool))plan={...plan,[this.placementPreview.field]:this.placementPreview.price};if(this.drag&&['plan-level','place-level'].includes(this.drag.kind))plan={...plan,[this.drag.field]:this.drag.price};if(this.drag||this.placementPreview){const entry=priceNumber(plan.planned_entry_price),stop=priceNumber(plan.stop_price);if(entry&&stop&&entry!==stop)plan={...plan,direction:stop>entry?'short':'long'};}if(this.tool==='planned-entry'&&this.toolDirection)plan={...plan,direction:this.toolDirection};return plan;}
    levelGeometry(){return planLevelGeometry(this.displayPlan(),this.tx,this.plot,this.host.clientWidth);}
    clearPresence(cursor=null){this.callbacks.presence?.({object_id:null,preview_object:null,cursor,editing:false},true);}
    releasePointer(drag){if(drag&&this.svg.hasPointerCapture?.(drag.pointerId))this.svg.releasePointerCapture(drag.pointerId);if(drag?.pointerId===this.capturedPointerId)this.capturedPointerId=null;}
    cancelGesture(){this.snapPreview=null;const drag=this.drag;this.drag=null;this.placementPreview=null;this.callbacks.previewLevel?.(null,null);if(drag?.kind==='plan-level')this.callbacks.dragLevel?.(drag.field,drag.originPrice,{phase:'cancel'});if(drag?.kind==='pan')this.view=drag.view;this.releasePointer(drag);this.clearPresence();this.applyPendingMarket();this.render();}
    closeTextEditor(){if(this.textEditor)this.textEditor.hidden=true;this.textAnchor=null;this.textComposing=false;}
    nameLabel(object){
      const point=object?.points?.[0];if(!point||!this.tx)return null;const tx=this.tx,plot=this.plot;let x,y,name,size=10,anchor='start';
      if(object.kind==='zone'){const b=zoneBounds(object);if(!b)return null;x=Math.max(plot.x+4,tx.x(b.start)+4);y=Math.max(plot.y+12,tx.y(b.high)+12);name=object.name||TYPES[object.zone_type]||'区域';}
      else if(object.kind==='text'){size=12;name=String(object.name||'文本').replace(/\r\n?/g,'\n');const lines=name.split('\n'),lineHeight=16,width=Math.max(12,...lines.map(line=>textWidth(line,size))),height=lines.length*lineHeight,top=tx.y(point.price)-height/2;x=tx.x(point.time)+(object.text_side==='left'?-width-7:7);y=top+size;return{x,y,name,size,anchor:'start',left:x,width,top,height,lineHeight,lines};}
      else if(object.kind==='horizontal'){x=plot.x+5;y=tx.y(point.price)-4;name=object.name||'水平线';}
      else if(['trend','reference'].includes(object.kind)){x=tx.x(point.time)+4;y=tx.y(point.price)-6;name=object.name||(object.kind==='trend'?'趋势线':'参考线');}
      else if(object.kind==='segment'&&object.name&&object.name!=='水平线段'){x=Math.max(plot.x+4,Math.min(...this.objectPoints(object).map(p=>p.x)));y=tx.y(point.price)+14;name=object.name;}
      else return null;
      name=String(name).replace(/[\r\n]+/g,' ');const width=Math.max(12,textWidth(name,size)),left=anchor==='end'?x-width:x;
      return{x,y,name,size,anchor,left,width,top:y-size,height:size+4};
    }
    titleContains(object,point){const label=this.nameLabel(object);return Boolean(label&&point.x>=label.left-3&&point.x<=label.left+label.width+3&&point.y>=label.top-3&&point.y<=label.top+label.height+3);}
    editableTitleAt(point,event=null){
      if(event?.target?.closest?.('[data-layer-price],[data-segment-price]'))return null;
      const objects=this.objects().slice().reverse(),id=event?.target?.closest?.('[data-edit-name]')?.getAttribute('data-edit-name');
      if(id){const exact=objects.find(object=>object.id===id&&this.nameLabel(object));if(exact)return exact;}
      return objects.find(object=>this.titleContains(object,point))||null;
    }
    zoneTitleAt(point){return this.objects().slice().reverse().find(object=>object.kind==='zone'&&this.titleContains(object,point))||null;}
    syncTitleEditor(){if(!this.titleObjectId)return;const object=this.plan.objects?.find(o=>o.id===this.titleObjectId);if(this.readOnly||!object||object.locked||object.visible===false||!visibleOnPeriod(object,this.period)||!this.nameLabel(object)||this.selected!==object.id)this.closeTitleEditor();else this.positionTitleEditor(object);}
    closeTitleEditor(){const editing=this.titleObjectId;this.titleObjectId=null;if(this.titleEditor)this.titleEditor.hidden=true;if(editing)this.clearPresence();}
    positionTitleEditor(object){const label=this.nameLabel({...object,name:this.titleEditor.value}),width=Math.min(Math.max(90,label.width+24),360,Math.max(32,this.host.clientWidth-8)),height=this.titleEditorMultiline?Math.min(Math.max(42,label.height+14),Math.max(32,this.host.clientHeight-8)):32;Object.assign(this.titleEditor.style,{width:width+'px',height:height+'px',left:Math.max(4,Math.min(label.left,this.host.clientWidth-width-4))+'px',top:Math.max(4,Math.min(label.top-7,this.host.clientHeight-height-4))+'px'});}
    openTitleEditor(object,point){
      if(this.readOnly)return;
      const current=this.plan.objects?.find(item=>item.id===object?.id);if(!current||!this.nameLabel(current))return;
      if(current.locked){this.callbacks.notice?.('标记已锁定，请先解锁。');return;}
      this.closeTextEditor();this.closeTitleEditor();this.selected=current.id;this.selectedAnchor=null;this.callbacks.select?.(current.id,null);
      const multiline=current.kind==='text';
      if(!this.titleEditor||this.titleEditorMultiline!==multiline){this.titleEditor?.remove?.();const input=document.createElement(multiline?'textarea':'input');input.className='chart-title-editor';input.maxLength=multiline?2000:80;input.autocomplete='off';if(multiline){input.wrap='off';input.rows=2;input.setAttribute('aria-description','Alt+Enter 换行，Enter 完成');}this.host.append(input);this.titleEditor=input;this.titleEditorMultiline=multiline;
        const save=event=>{if(this.readOnly||input!==this.titleEditor||event?.isComposing||this.titleComposing||!this.titleObjectId)return;const latest=this.plan.objects?.find(o=>o.id===this.titleObjectId);if(!latest||latest.locked||latest.visible===false){this.closeTitleEditor();return;}if(input.value===this.titleLastValue)return;this.titleLastValue=input.value;this.positionTitleEditor(latest);if(latest.name!==input.value)this.callbacks.update?.(latest.id,{name:input.value});};
        input.addEventListener('compositionstart',()=>{if(input===this.titleEditor)this.titleComposing=true;});input.addEventListener('input',save);input.addEventListener('compositionend',event=>{if(input!==this.titleEditor)return;this.titleComposing=false;save(event);});
        input.addEventListener('blur',event=>{if(input!==this.titleEditor)return;save(event);this.closeTitleEditor();});input.addEventListener('keydown',event=>{event.stopPropagation();if(input!==this.titleEditor||event.isComposing||this.titleComposing||event.keyCode===229)return;if(event.key==='Enter'&&event.altKey&&multiline){event.preventDefault();if(insertTextNewline(input))save(event);return;}if(event.key==='Enter'||event.key==='Escape'){event.preventDefault();if(event.key==='Enter')save(event);this.closeTitleEditor();this.host.focus({preventScroll:true});}});
      }
      this.titleObjectId=current.id;this.titleComposing=false;this.titleEditor.value=current.name||'';this.titleLastValue=this.titleEditor.value;this.titleEditor.setAttribute('aria-label',multiline?'文本内容':'图层名称');this.titleEditor.hidden=false;this.positionTitleEditor(current);this.titleEditor.focus();this.titleEditor.select();this.callbacks.presence?.({object_id:current.id,editing:true},true);
    }
    openTextEditor(value,point){
      if(this.readOnly)return;
      let form=this.textEditor;
      if(!form){
        form=document.createElement('form');form.className='chart-text-editor';form.hidden=true;
        const input=document.createElement('textarea');input.name='text';input.maxLength=2000;input.rows=3;input.wrap='off';input.placeholder='输入文本，Alt+Enter 换行，Enter 添加';input.setAttribute('aria-label','文本内容');input.autocomplete='off';input.required=true;
        const save=document.createElement('button');save.type='submit';save.textContent='添加';
        const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';cancel.addEventListener('click',()=>this.closeTextEditor());
        form.append(input,save,cancel);this.host.append(form);this.textEditor=form;this.textInput=input;
        const submit=event=>{event.preventDefault();if(this.readOnly||event.isComposing||this.textComposing)return;const name=input.value.replace(/\r\n?/g,'\n'),anchor=this.textAnchor;if(!name.trim()||!anchor)return;this.callbacks.add?.({kind:'text',name,points:[anchor],color:'#f1c40f',opacity:this.toolOpacity,period:this.period,text_side:'right'});this.closeTextEditor();this.callbacks.cancelTool?.();};
        input.addEventListener('compositionstart',()=>{this.textComposing=true;});input.addEventListener('compositionend',()=>{this.textComposing=false;});
        form.addEventListener('submit',submit);form.addEventListener('keydown',event=>{event.stopPropagation();if(event.isComposing||this.textComposing||event.keyCode===229)return;if(event.key==='Enter'&&event.target!==save&&event.target!==cancel){event.preventDefault();if(event.altKey)insertTextNewline(input);else submit(event);}else if(event.key==='Escape'){this.insertWave=null;event.preventDefault();this.closeTextEditor();}});
      }
      this.textAnchor=value;this.textComposing=false;form.hidden=false;const width=Math.min(360,Math.max(32,this.host.clientWidth-8)),height=Math.min(86,Math.max(32,this.host.clientHeight-8));form.style.width=width+'px';form.style.height=height+'px';form.style.left=Math.max(4,Math.min(point.x,this.host.clientWidth-width-4))+'px';form.style.top=Math.max(4,Math.min(point.y,this.host.clientHeight-height-4))+'px';this.textInput.value='';this.textInput.focus();
    }
    hitPathControl(point){const controls=this.pathControls||(this.pathControl?[this.pathControl]:[]),padding=3;for(const control of controls.slice().reverse())if(point.x>=control.x-padding&&point.x<=control.x+control.w+padding&&point.y>=control.y-padding&&point.y<=control.y+control.h+padding)return{...control,direction:point.x<control.x+control.w/2?-1:1};return null;}
    insertWavePoint(id,point,atSegment=null){
      if(this.readOnly)return;
      const object=this.plan.objects?.find(o=>o.id===id&&isForecast(o));if(!object||object.locked)return;
      const points=JSON.parse(JSON.stringify(object.points));let segment=Number.isInteger(atSegment)?atSegment:this.selected===id&&Number.isInteger(this.selectedAnchor)?this.selectedAnchor:null;
      if(segment===null){const screen=this.objectPoints(object);let distance=Infinity;for(let i=0;i<screen.length-1;i++){const d=segmentDistance(point,screen[i],screen[i+1]);if(d<distance){distance=d;segment=i;}}}
      const index=Math.min(points.length,(segment??points.length-1)+1);points.splice(index,0,this.forecastPoint(this.toValue(point,false,true)));
      this.selectedAnchor=index;this.insertWave=null;this.callbacks.update?.(id,{points:wavePoints(points)});this.callbacks.anchorSelected?.(id,index);this.render();
    }
    deleteSelection(){if(this.readOnly)return;const object=this.plan.objects?.find(o=>o.id===this.selected);if(!object)return;if(object.locked){this.callbacks.notice?.('标记已锁定，请先解锁。');return;}if(isForecast(object)&&Number.isInteger(this.selectedAnchor)){if(object.points.length<=2){this.callbacks.notice?.('预期线至少保留两个点；点击线段后按 Delete 可删除整条。');return;}this.deleteWavePoint();return;}this.callbacks.deleteObject?.(object.id);}
    deleteWavePoint(){
      if(this.readOnly)return;
      const object=this.plan.objects?.find(o=>o.id===this.selected&&isForecast(o));if(!object||object.locked||object.points.length<=2||!Number.isInteger(this.selectedAnchor))return;
      const points=object.points.filter((p,i)=>i!==this.selectedAnchor);this.selectedAnchor=null;this.callbacks.update?.(object.id,{points:wavePoints(points)});this.callbacks.anchorSelected?.(object.id,null);this.render();
    }
    adjustPathControl(control){if(this.readOnly)return;const object=this.plan.objects?.find(item=>item.id===control.id&&['path','wave'].includes(item.kind));if(!object||object.locked)return;let points;if(Number.isInteger(control.index)&&control.direction<0){points=JSON.parse(JSON.stringify(object.points));if(points.length<=2)return;points.splice(control.index,1);}else{const segment=Number.isInteger(control.index)?(control.index<object.points.length-1?control.index:null):control.segment;points=this.editForecastPoints(object,segment,control.direction);}if(JSON.stringify(points)!==JSON.stringify(object.points))this.callbacks.update?.(object.id,{points:isForecast(object)?wavePoints(points):points});this.hoverPath=null;this.pathControl=null;this.pathControls=null;}
    inspectPointer(event,activate=false){if(this.pinch||this.touchBlocked||!this.view||!this.tx)return;const point=this.point(event);if(point.x<this.plot.x||point.x>this.plot.x+this.plot.w||point.y<this.plot.y||point.y>this.plot.y+this.plot.h)return;if(activate){this.callbacks.interaction?.();this.callbacks.activate?.(this.period);}this.crosshair=point;this.host.style.cursor='default';if(this.period==='day'){const range=this.dayAt(point),key=range?.label||null;if(key!==this.hoverDayKey){this.hoverDayKey=key;this.callbacks.hoverDay?.(range);}}this.callbacks.presence?.({object_id:null,cursor:this.toValue(point,false,true),preview_object:null,editing:false});this.scheduleRender();}
    down(event){
      if(this.readOnly){if(event.button===0)this.inspectPointer(event,true);return;}
      // Open on secondary press: live SVG redraws can remove the native contextmenu target.
      if(event.button===2){this.contextMenu(event);this.secondaryPress={x:event.clientX,y:event.clientY,at:event.timeStamp??Date.now()};return;}
      if(event.button!==0||!this.view||!this.tx)return;this.callbacks.interaction?.();this.callbacks.activate?.(this.period);this.host.focus({preventScroll:true});const point=this.point(event),inside=point.x>=this.plot.x&&point.x<=this.plot.x+this.plot.w&&point.y>=this.plot.y&&point.y<=this.plot.y+this.plot.h,levelHit=this.tool==='select'?hitPlanLevel(point,this.levelGeometry(),this.plot,this.host.clientWidth):null;
      if(!inside&&!levelHit)return;event.preventDefault?.();this.svg.setPointerCapture(event.pointerId);this.capturedPointerId=event.pointerId;const value=this.toValue(point);if(this.insertWave){this.insertWavePoint(this.insertWave,point);this.releasePointer({pointerId:event.pointerId});return;}const objectHit=this.tool==='select'?this.hit(point):null,pathControl=this.tool==='select'?this.hitPathControl(point):null;if(pathControl){this.adjustPathControl(pathControl);this.render();return;}
      const title=this.tool==='select'?this.editableTitleAt(point,event):null,titleTime=event.timeStamp??Date.now(),lastTitle=this.lastTitleClick;this.lastTitleClick=title?{id:title.id,at:titleTime,point}:null;
      if(title&&lastTitle?.id===title.id&&titleTime-lastTitle.at<=450&&Math.hypot(point.x-lastTitle.point.x,point.y-lastTitle.point.y)<=6){this.lastTitleClick=null;this.suppressDoubleClickUntil=titleTime+450;this.releasePointer({pointerId:event.pointerId});this.openTitleEditor(title,point);return;}
      if(['planned-entry','planned-stop'].includes(this.tool)){this.drag={kind:'place-level',field:this.tool==='planned-entry'?'planned_entry_price':'stop_price',price:this.toValue(point,false).price,pointerId:event.pointerId};this.callbacks.previewLevel?.(this.drag.field,this.drag.price);this.render();return;}
      if(['path','wave'].includes(this.tool)){
        const now=event.timeStamp??Date.now(),previous=this.lastDrawingClick;
        const finish=previous&&now-previous.at>=0&&now-previous.at<=450&&Math.hypot(point.x-previous.x,point.y-previous.y)<=6;
        this.releasePointer({pointerId:event.pointerId});
        if(finish){this.lastDrawingClick=null;this.suppressDoubleClickUntil=now+450;this.finishPath(event);return;}
        this.lastDrawingClick={x:point.x,y:point.y,at:now};this.path.push(this.forecastPoint(this.toValue(point,false,this.tool==='wave')));
        this.callbacks.draftChanged?.({period:this.period,count:this.path.length});this.render();return;
      }
      if(this.tool==='horizontal'){this.callbacks.add?.({kind:'horizontal',name:'水平线',points:[value],color:'#f1c40f',opacity:this.toolOpacity,period:'all'});return;}
      if(this.tool==='segment'){const origin=this.toValue(point,false);this.drag={kind:'new-segment',origin,point:origin,pointerId:event.pointerId};this.render();return;}
      if(this.tool==='text'){this.openTextEditor(value,point);return;}
      if(['trend','reference'].includes(this.tool)){this.snapPreview=this.snapTarget(point);this.drag={kind:'new-line',lineKind:this.tool,origin:value,point:value,pointerId:event.pointerId};this.render();return;}
      if(this.tool==='zone'){this.drag={kind:'new-zone',origin:value,point:value,pointerId:event.pointerId};this.render();return;}
      if(levelHit&&!(isForecast(objectHit?.object)&&levelHit.target!=='label')){this.selected=null;this.selectedLevel=levelHit.field;this.callbacks.select?.(null);this.drag={...beginLevelDrag(levelHit.field,levelHit.price,this.tick),pointerId:event.pointerId};this.callbacks.dragLevel?.(levelHit.field,levelHit.price,{phase:'start'});this.callbacks.presence?.({object_id:'field:'+levelHit.field,cursor:{...value,price:levelHit.price},preview_object:null,editing:true},true);this.render();return;}
      if(this.tool==='pan'){this.followLatest=false;this.drag={kind:'pan',origin:point,view:{...this.view},pointerId:event.pointerId};this.render();return;}
      const hit=title?{object:title,anchor:null}:objectHit;this.selectedLevel=null;
      // SVG children are replaced while selecting, so native dblclick may lose its target.
      const now=event.timeStamp??Date.now(),previousClick=this.lastPathClick;
      this.lastPathClick=isForecast(hit?.object)?{id:hit.object.id,anchor:hit.anchor,segment:hit.segment,x:point.x,y:point.y,at:now}:null;
      if(this.lastPathClick&&previousClick?.id===hit.object.id&&previousClick.anchor===hit.anchor&&previousClick.segment===hit.segment&&now-previousClick.at>=0&&now-previousClick.at<=450&&Math.hypot(point.x-previousClick.x,point.y-previousClick.y)<=6){
        this.lastPathClick=null;this.suppressDoubleClickUntil=now+450;this.editPathAtHit(hit,point);this.releasePointer({pointerId:event.pointerId});return;
      }
      if(hit){this.selected=hit.object.id;this.callbacks.select?.(hit.object.id,hit.anchor);this.selectedAnchor=hit.anchor;this.callbacks.anchorSelected?.(hit.object.id,hit.anchor);const canDrag=!hit.object.locked&&(hit.anchor!==null||!['path','wave'].includes(hit.object.kind));if(canDrag){this.drag={kind:'object',origin:hit.anchor===null||['path','wave'].includes(hit.object.kind)?this.toValue(point,false,isForecast(hit.object)):value,start:{...point},object:JSON.parse(JSON.stringify(hit.object)),anchor:hit.anchor,moved:false,pointerId:event.pointerId};this.callbacks.presence?.({object_id:hit.object.id,editing:true},true);}}
      else{this.selected=null;this.selectedAnchor=null;this.callbacks.select?.(null);this.followLatest=false;this.drag={kind:'pan',origin:point,view:{...this.view},pointerId:event.pointerId};}this.render();
    }
    scheduleRender(){if(this.renderFrame!=null)return;const frame=globalThis.requestAnimationFrame;if(!frame){this.render();return;}const task={};this.renderTask=task;this.renderFrame=frame(()=>{if(this.renderTask!==task)return;this.renderTask=null;this.renderFrame=null;this.render();});}
    objectDragPreview(drag){
      if(this.readOnly)return null;
      const current=this.plan.objects?.find(object=>object.id===drag.object.id);
      if(!current||current.locked||current.kind!==drag.object.kind||!drag.value||!drag.point)return null;
      const value={...drag.value},dt=value.time-drag.origin.time,dp=value.price-drag.origin.price;
      if(drag.anchor===null){
        // Replay this gesture on the latest geometry instead of restoring its starting snapshot.
        const zoneDelta=current.kind==='zone'?Math.max(dp,this.tick-Math.min(...current.points.map(point=>point.price))):dp;
        return{...current,points:current.points.map(anchor=>{const horizontalMove=Math.abs(drag.point.x-drag.start.x)>.01,moved={...anchor,time:isForecast(current)&&!horizontalMove?anchor.time:['zone','segment'].includes(current.kind)||isForecast(current)?Math.max(0,Math.round(this.tx.time(this.tx.x(this.pointTime(anchor))+drag.point.x-drag.start.x))):Math.max(0,anchor.time+dt),price:current.kind==='zone'?anchor.price+zoneDelta:isForecast(current)?Math.max(Number.EPSILON,anchor.price+dp):snap(Math.max(this.tick,anchor.price+dp),this.tick)};return isForecast(current)&&horizontalMove?this.forecastPoint(moved):moved;})};
      }
      const original=drag.object.points[drag.anchor];
      // Stable IDs survive peer insertions/deletions. Legacy forecasts use an exact, unique anchor match.
      const matches=current.points.map((point,index)=>({point,index})).filter(({point})=>point.time===original?.time&&point.price===original?.price);
      const index=original?.id?current.points.findIndex(point=>point.id===original.id):isForecast(current)?(matches.length===1?matches[0].index:-1):drag.anchor;
      if(index<0||!current.points[index])return null;
      if(current.kind==='segment'){
        const other=current.points[1-index];if(!other)return null;
        const otherX=this.tx.x(other.time),direction=Math.sign(drag.point.x-otherX)||Math.sign(current.points[index].time-other.time)||1;
        if(Math.abs(drag.point.x-otherX)<3)value.time=Math.max(0,Math.round(this.tx.time(otherX+direction*3)));
        value.price=current.points[0].price;
      }
      this.selectedAnchor=index;
      return{...current,points:current.points.map((anchor,i)=>i===index?(isForecast(current)?Math.abs(drag.point.x-drag.start.x)<=.01?{...anchor,price:value.price}:this.forecastPoint({...anchor,...value}):{...anchor,...value}):anchor)};
    }
    move(event){
      if(this.readOnly){this.inspectPointer(event);return;}
      if(!this.view||!this.tx)return;const point=this.point(event);this.crosshair=point;if(this.period==='day'){const range=!this.drag&&this.tool==='select'?this.dayAt(point):null,key=range?.label||null;if(key!==this.hoverDayKey){this.hoverDayKey=key;this.callbacks.hoverDay?.(range);}}if(!this.drag&&['planned-entry','planned-stop'].includes(this.tool)){const inside=point.x>=this.plot.x&&point.x<=this.plot.x+this.plot.w&&point.y>=this.plot.y&&point.y<=this.plot.y+this.plot.h;this.placementPreview=inside?{field:this.tool==='planned-entry'?'planned_entry_price':'stop_price',price:this.toValue(point,false).price}:null;this.callbacks.previewLevel?.(this.placementPreview?.field||null,this.placementPreview?.price||null);}
      if(this.drag){const drag=this.drag;
        if(drag.kind==='pan'){const tx=transform(drag.view,this.plot,this.bars,this.period,this.getTimeScale()),dx=point.x-drag.origin.x,dp=(point.y-drag.origin.y)/this.plot.h*(drag.view.max-drag.view.min);this.view={start:tx.time(this.plot.x-dx),end:tx.time(this.plot.x+this.plot.w-dx),min:drag.view.min+dp,max:drag.view.max+dp};this.autoPrice=true;this.view=this.priceFit(this.view);this.callbacks.viewChanged?.(this);}
        else if(drag.kind==='plan-level'){this.drag=moveLevelDrag(drag,this.toValue(point,false).price,this.tick);this.callbacks.dragLevel?.(drag.field,this.drag.price,{phase:'move'});}
        else if(drag.kind==='place-level'){drag.price=this.toValue(point,false).price;this.callbacks.previewLevel?.(drag.field,drag.price);}
        else if(drag.kind==='new-zone'||drag.kind==='new-line')drag.point=this.toValue(point);
        else if(drag.kind==='new-segment')drag.point={time:this.toValue(point,false).time,price:drag.origin.price};
        else if(drag.kind==='object'){drag.value=this.toValue(point,drag.anchor!==null&&!['path','wave','segment'].includes(drag.object.kind),isForecast(drag.object));drag.point={...point};drag.moved=drag.moved||Math.hypot(point.x-drag.start.x,point.y-drag.start.y)>=3;if(drag.moved){this.lastPathClick=null;this.lastTitleClick=null;drag.preview=this.objectDragPreview(drag);}}
      }
      const drag=this.drag,isLevel=drag&&['plan-level','place-level'].includes(drag.kind),overControl=!drag&&this.tool==='select'?this.hitPathControl(point):null;this.hoverLevel=!drag&&this.tool==='select'?hitPlanLevel(point,this.levelGeometry(),this.plot,this.host.clientWidth)?.field:null;
      this.snapPreview=drag?.kind==='new-line'||drag?.kind==='object'&&Number.isInteger(drag.anchor)&&['trend','reference'].includes(drag.object.kind)||!drag&&['trend','reference','horizontal'].includes(this.tool)?this.snapTarget(point):null;
      if(!drag&&this.tool==='select'&&!overControl){const hover=this.hit(point);this.hoverPath=hover&&['path','wave'].includes(hover.object.kind)&&Number.isInteger(hover.segment)?{id:hover.object.id,segment:hover.segment,point:{...point}}:null;}else if(drag)this.hoverPath=null;
      this.host.style.cursor=this.insertWave?'crosshair':overControl?'pointer':isLevel||this.hoverLevel?'ns-resize':drag?.kind==='pan'?'grabbing':this.tool==='pan'?'grab':this.tool==='select'?'default':'crosshair';
      const cursor=this.toValue(point,false,true);if(isLevel)cursor.price=drag.price;this.callbacks.presence?.({object_id:isLevel?'field:'+drag.field:drag?.object?.id||(drag?.kind==='new-zone'?'new-zone':null),cursor,preview_object:drag?.preview||null,editing:Boolean(drag&&drag.kind!=='pan')});this.scheduleRender();
    }
    up(event){
      if(this.readOnly){if(this.drag)this.cancelGesture();return;}
      if(!this.drag)return;const drag=this.drag;this.drag=null;this.releasePointer(drag);this.snapPreview=null;
      if(drag.kind==='place-level'){this.placementPreview=null;this.callbacks.previewLevel?.(null,null);this.callbacks.placeLevel?.(drag.field,drag.price);}
      else if(drag.kind==='plan-level'){const commit=levelCommit(drag);this.callbacks.dragLevel?.(drag.field,Object.hasOwn(commit,drag.field)?commit[drag.field]:drag.originPrice,{phase:Object.hasOwn(commit,drag.field)?'end':'cancel'});}
      else if(drag.kind==='new-zone'){if(Math.abs(drag.origin.price-drag.point.price)>=this.tick&&Math.abs(drag.origin.time-drag.point.time)>INTERVAL[this.period]/4)this.callbacks.add?.({kind:'zone',name:'危险区',zone_type:'danger',effect:'block',direction:'both',period:this.period,points:[drag.origin,drag.point],opacity:this.toolOpacity});}
      else if(drag.kind==='new-line'){if(Math.hypot(drag.origin.time-drag.point.time,drag.origin.price-drag.point.price)>0)this.callbacks.add?.({kind:drag.lineKind,name:drag.lineKind==='trend'?'趋势线':'参考线',period:'all',points:[drag.origin,drag.point],color:'#f1c40f',opacity:this.toolOpacity,line_style:drag.lineKind==='reference'?'dashed':'solid'});}
      else if(drag.kind==='new-segment'){if(Math.abs(this.tx.x(drag.origin.time)-this.tx.x(drag.point.time))>=3)this.callbacks.add?.({kind:'segment',name:'水平线段',period:this.period,points:[drag.origin,drag.point],opacity:this.toolOpacity});}
      else if(drag.kind==='object'&&drag.moved){const preview=this.objectDragPreview(drag);if(preview)this.callbacks.update?.(drag.object.id,{points:preview.points});}this.render();
      this.snapPreview=null;const point=this.point(event),inside=point.x>=this.plot.x&&point.x<=this.plot.x+this.plot.w&&point.y>=this.plot.y&&point.y<=this.plot.y+this.plot.h;this.clearPresence(inside?this.toValue(point,false,true):null);this.applyPendingMarket();
    }
    editPathAtHit(hit,point){if(this.readOnly||!isForecast(hit?.object))return;this.selected=hit.object.id;this.selectedAnchor=hit.anchor;this.callbacks.select?.(hit.object.id,hit.anchor);if(Number.isInteger(hit.anchor))this.deleteSelection();else if(Number.isInteger(hit.segment))this.insertWavePoint(hit.object.id,point,hit.segment);}
    doubleClick(event){if(this.readOnly)return;if((event.timeStamp??Date.now())<=this.suppressDoubleClickUntil){event.preventDefault();return;}if(['path','wave'].includes(this.tool)){event.preventDefault();this.finishPath(event);return;}if(this.tool!=='select'||!this.tx)return;const point=this.point(event),title=this.editableTitleAt(point,event);if(title){event.preventDefault();this.openTitleEditor(title,point);return;}const hit=this.hit(point);if(isForecast(hit?.object)){event.preventDefault();this.lastPathClick=null;this.editPathAtHit(hit,point);}}
    cancelPath({render=true}={}){const hadPoints=this.path.length>0;this.path=[];this.lastDrawingClick=null;this.callbacks.draftChanged?.({period:this.period,count:0});this.applyPendingMarket();if(render)this.render();return hadPoints;}
    finishPath(event){
      if(this.readOnly){this.cancelPath();return false;}
      event?.preventDefault();if(!['path','wave'].includes(this.tool)||this.path.length<2)return false;
      const points=this.path.filter((point,index,array)=>!index||point.time!==array[index-1].time||point.price!==array[index-1].price);
      if(points.length<2){this.cancelPath();return false;}
      const object={kind:this.tool,name:this.toolName||'预期走势',period:this.period,points:this.tool==='wave'?wavePoints(points):points,locked:false,color:this.toolColor||'#f1c40f',opacity:this.toolOpacity,note:'手工预期走势，按时间和价格固定'};
      // add may synchronously choose another tool; clear before calling it.
      this.cancelPath({render:false});this.callbacks.add?.(object);this.render();return true;
    }
    keydown(event){if(this.readOnly){if(event.key==='Escape'){this.crosshair=null;this.clearPresence();this.render();}return;}if(event.key==='Escape'){this.insertWave=null;event.preventDefault();this.cancelPath({render:false});this.draft=null;this.cancelGesture();this.tool='select';this.callbacks.cancelTool?.();this.render();}if(event.key==='Enter'&&this.path.length>=2)this.finishPath(event);}
    adjustSelectedPath(direction){if(this.readOnly)return;const object=this.plan.objects?.find(item=>item.id===this.selected&&['path','wave'].includes(item.kind));if(!object||object.locked)return;const points=this.editForecastPoints(object,null,direction);if(JSON.stringify(points)!==JSON.stringify(object.points))this.callbacks.update?.(object.id,{points:isForecast(object)?wavePoints(points):points});}
    drawPathControl(){
      this.pathControl=null;this.pathControls=null;if(this.readOnly||this.tool!=='select'||!this.hoverPath)return;
      const object=this.objects().find(item=>item.id===this.hoverPath?.id);if(!object||object.locked||isForecast(object))return;
      const points=this.objectPoints(object),cursor=this.hoverPath.point;if(!points.length)return;
      let index=0;for(let current=1;current<points.length;current++)if(Math.hypot(cursor.x-points[current].x,cursor.y-points[current].y)<Math.hypot(cursor.x-points[index].x,cursor.y-points[index].y))index=current;
      const anchor=points[index];if(anchor.x<this.plot.x-10||anchor.x>this.plot.x+this.plot.w+10||anchor.y<this.plot.y-10||anchor.y>this.plot.y+this.plot.h+10)return;
      const w=40,h=17,x=Math.max(this.plot.x,Math.min(this.plot.x+this.plot.w-w,anchor.x-w/2)),below=anchor.y+h+9<=this.plot.y+this.plot.h,y=Math.max(this.plot.y,Math.min(this.plot.y+this.plot.h-h,below?anchor.y+18:anchor.y-h-18)),control={id:object.id,index,x,y,w,h};
      this.pathControl=control;this.pathControls=[control];
      const group=createSVG('g',{'data-path-control':object.id,'data-path-index':index});group.append(createSVG('rect',{x,y,width:w,height:h,rx:3,fill:'#1c2730',stroke:object.color||'#f1c40f','stroke-width':.8}),createSVG('line',{x1:x+w/2,y1:y+3,x2:x+w/2,y2:y+h-3,stroke:'#4d5d69'}),createSVG('text',{x:x+w/4,y:y+12,'text-anchor':'middle',fill:'#d8e1e8','font-size':13},'−'),createSVG('text',{x:x+w*3/4,y:y+12,'text-anchor':'middle',fill:'#d8e1e8','font-size':13},'+'));this.svg.append(group);
    }
    contextMenu(event){
      if(this.readOnly){event.preventDefault?.();return;}
      if(event.target?.closest?.('input,textarea,form'))return;
      event.preventDefault();event.stopPropagation?.();if(!this.tx)return;
      const press=this.secondaryPress;this.secondaryPress=null;
      if(event.type==='contextmenu'&&press&&(event.timeStamp??Date.now())-press.at<1200&&Math.hypot(event.clientX-press.x,event.clientY-press.y)<4)return;
      const point=this.point(event),targetId=event.target?.closest?.('[data-chart-object]')?.getAttribute('data-chart-object'),target=this.objects().find(o=>o.id===targetId);
      const hit=target?{object:target}:this.hit(point),level=hitPlanLevel(point,this.levelGeometry(),this.plot,this.host.clientWidth);
      this.callbacks.activate?.(this.period);
      if(target||hit&&(!level||isForecast(hit.object)&&level.target!=='label')){
        this.selected=hit.object.id;this.selectedAnchor=null;this.selectedLevel=null;this.callbacks.context?.(hit.object.id,event);
      }else if(level){this.selected=null;this.selectedAnchor=null;this.selectedLevel=level.field;this.callbacks.context?.(null,event,{field:level.field});}
      else this.callbacks.context?.(null,event);
      this.render();
    }
    render(){
      if(this.updateDepth){this.renderDeferred=true;return;}
      if(this.renderFrame!=null)globalThis.cancelAnimationFrame?.(this.renderFrame);this.renderFrame=null;this.renderTask=null;
      this.syncTitleEditor();
      const width=Math.round(this.host.clientWidth),height=Math.round(this.host.clientHeight);if(!width||!height)return;this.svg.setAttribute('width',width);this.svg.setAttribute('height',height);this.svg.setAttribute('viewBox',`0 0 ${width} ${height}`);this.svg.replaceChildren();const indicatorHeight=Math.max(54,Math.min(82,height*.2));this.plot={x:5,y:5,w:Math.max(80,width-67),h:Math.max(45,height-indicatorHeight-40)};this.indicator={x:5,y:this.plot.y+this.plot.h+24,w:this.plot.w,h:Math.max(35,height-this.plot.y-this.plot.h-42)};
      if(!this.view||!this.bars.length){this.svg.append(createSVG('text',{x:width/2,y:height/2,'text-anchor':'middle',fill:'#6c7985','font-size':12},'等待行情'));return;}
      if(this.autoPrice&&!this.drag)this.view=this.priceFit(this.view);const view=this.view;this.tx=transform(view,this.plot,this.bars,this.period,this.getTimeScale());const tx=this.tx,plot=this.plot,shown=this.displayPlan(),defs=createSVG('defs'),clip=createSVG('clipPath',{id:this.uid+'-clip'});clip.append(createSVG('rect',{x:plot.x,y:plot.y,width:plot.w,height:plot.h}));const hatch=createSVG('pattern',{id:this.uid+'-hatch',width:7,height:7,patternUnits:'userSpaceOnUse',patternTransform:'rotate(35)'});hatch.append(createSVG('rect',{width:7,height:7,fill:'#372d18','fill-opacity':'.25'}),createSVG('line',{x1:0,y1:0,x2:0,y2:7,stroke:'#a67b32','stroke-opacity':'.35'}));defs.append(clip,hatch);this.svg.append(defs);const layer=createSVG('g',{'clip-path':`url(#${this.uid}-clip)`});
      const last=this.bars.at(-1),futureX=Math.max(plot.x,tx.x(last.time));layer.append(createSVG('rect',{x:futureX,y:plot.y,width:Math.max(0,plot.x+plot.w-futureX),height:plot.h,fill:'#a5adb5','fill-opacity':'.035'}));
      this.drawLinkedRange(layer);
      for(let index=0;index<=5;index++){const price=view.min+(view.max-view.min)*index/5,y=tx.y(price);this.svg.append(createSVG('line',{x1:plot.x,y1:y,x2:plot.x+plot.w,y2:y,stroke:'#2b333b','stroke-dasharray':'1 3','stroke-width':.6}),createSVG('text',{x:plot.x+plot.w+5,y:y+3,fill:'#7f8b96','font-size':10},priceLabel(price,this.tick)));}
      for(let index=0;index<5;index++){const x=plot.x+plot.w*index/5,time=tx.time(x),date=new Date(time);this.svg.append(createSVG('line',{x1:x,y1:plot.y,x2:x,y2:plot.y+plot.h,stroke:'#293139','stroke-dasharray':'1 3','stroke-width':.6}),createSVG('text',{x:x+2,y:plot.y+plot.h+13,fill:'#75818c','font-size':9},this.period==='day'?`${date.getMonth()+1}/${date.getDate()}`:date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})));}
      const maStart=Math.max(0,barIndex(this.bars,view.start)-1),maEnd=Math.min(this.bars.length,barIndex(this.bars,view.end,true)+1);
      for(const[length,color]of[[5,'#dededb'],[10,'#d8c345'],[20,'#c65aba']]){const values=this.getAverages(length).slice(maStart,maEnd).filter(Boolean).map(point=>`${tx.x(point.time)},${tx.y(point.value)}`).join(' ');if(values)layer.append(createSVG('polyline',{points:values,fill:'none',stroke:color,'stroke-width':.65,opacity:.8}));}
      const visible=this.visibleBars(),body=candleWidth(this.bars,tx,plot,this.period);for(const bar of visible){const x=tx.x(bar.time);if(x<plot.x-body||x>plot.x+plot.w+body)continue;const up=bar.close>=bar.open,color=up?'#ef5350':'#22c55e',top=Math.min(tx.y(bar.open),tx.y(bar.close));layer.append(createSVG('line',{x1:x,y1:tx.y(bar.high),x2:x,y2:tx.y(bar.low),stroke:color,'stroke-width':.8}),createSVG('rect',{'data-candle':bar.time,x:x-body/2,y:top,width:body,height:Math.max(1,Math.abs(tx.y(bar.open)-tx.y(bar.close))),fill:up?'#111519':color,stroke:color,'stroke-width':.8}));}
      const planOpacity=drawingOpacity(shown.plan_opacity),planLayer=createSVG('g',{'data-plan-overlays':this.period,opacity:planOpacity});layer.append(planLayer);
      const entryLow=priceNumber(shown.entry_low),entryHigh=priceNumber(shown.entry_high);if(entryLow&&entryHigh&&entryLow<entryHigh)planLayer.append(createSVG('rect',{'data-plan-overlay':'entry-range',x:plot.x,y:tx.y(entryHigh),width:plot.w,height:tx.y(entryLow)-tx.y(entryHigh),fill:'#287ec1','fill-opacity':'.09',stroke:'#4883aa','stroke-dasharray':'4 4'}));
      const linePrices=[['main_level','主结构','#8795a2'],['trigger_price','扳机','#45a1b4'],['obstacle_price','障碍','#c99a49']];for(const[key,label,color]of linePrices){const value=priceNumber(shown[key]);if(value&&tx.y(value)>=plot.y&&tx.y(value)<=plot.y+plot.h)planLayer.append(createSVG('line',{'data-plan-overlay':key,x1:plot.x,y1:tx.y(value),x2:plot.x+plot.w,y2:tx.y(value),stroke:color,'stroke-dasharray':'5 4'}),createSVG('text',{x:plot.x+5,y:tx.y(value)-4,fill:color,'font-size':10},`${label} ${priceLabel(value,this.tick)}`));}
      const editable=this.levelGeometry();if(priceNumber(shown.planned_entry_price)&&priceNumber(shown.stop_price)){const top=Math.min(tx.y(shown.planned_entry_price),tx.y(shown.stop_price)),bottom=Math.max(tx.y(shown.planned_entry_price),tx.y(shown.stop_price)),bandX=Math.max(plot.x,Math.min(plot.x+plot.w*.7,tx.x(last.time))),valid=validStopSide(shown.direction,shown.planned_entry_price,shown.stop_price);planLayer.append(createSVG('rect',{'data-plan-overlay':'risk-fill',x:bandX,y:top,width:plot.x+plot.w-bandX,height:Math.max(1,bottom-top),fill:valid?'#c78951':'#e46261','fill-opacity':'.07','pointer-events':'none'}));}for(const level of editable){const active=this.drag?.field===level.field||this.hoverLevel===level.field,color=level.field==='planned_entry_price'?(priceNumber(shown.stop_price)?(shown.direction==='short'?'#22c55e':'#ef5350'):'#6ca9d4'):'#d7ac66';planLayer.append(createSVG('line',{'data-plan-overlay':level.field,x1:plot.x,y1:level.y,x2:plot.x+plot.w,y2:level.y,stroke:color,'stroke-width':active?2.4:1.4,'stroke-dasharray':level.field==='stop_price'?'6 3':''}),createSVG('text',{x:plot.x+5,y:level.y-5,fill:color,'font-size':10},level.field==='planned_entry_price'?'入场':'止损'));}
      for(const object of this.objects())this.drawObject(layer,this.drag?.object?.id===object.id&&this.drag.preview?this.drag.preview:object);
      if(this.drag?.kind==='new-zone')this.drawObject(layer,{kind:'zone',name:'危险区',zone_type:'danger',effect:'block',direction:'both',points:[this.drag.origin,this.drag.point],opacity:this.toolOpacity});
      if(this.drag?.kind==='new-line')this.drawObject(layer,{kind:this.drag.lineKind,name:this.drag.lineKind==='trend'?'趋势线':'参考线',points:[this.drag.origin,this.drag.point],color:'#f1c40f',opacity:this.toolOpacity});
      if(this.drag?.kind==='new-segment')this.drawObject(layer,{kind:'segment',points:[this.drag.origin,this.drag.point],width:2,color:'#f1c40f',opacity:this.toolOpacity});
      if(this.path.length)this.drawObject(layer,{kind:'path',name:'计划路径',points:this.path,color:'#f1c40f',opacity:this.toolOpacity});
      if(last){const y=tx.y(last.close);layer.append(createSVG('line',{x1:plot.x,y1:y,x2:plot.x+plot.w,y2:y,'pointer-events':'none',stroke:last.close>=last.open?'#a54e4b':'#16a34a','stroke-dasharray':'2 3','stroke-width':.7}));if(y>=plot.y&&y<=plot.y+plot.h)this.svg.append(createSVG('rect',{x:plot.x+plot.w+1,y:y-8,width:61,height:16,fill:last.close>=last.open?'#9e4542':'#16a34a'}),createSVG('text',{x:plot.x+plot.w+5,y:y+3,fill:'#fff','font-size':10},priceLabel(last.close,this.tick)));}
      this.drawIndicators(tx,plot,body);
      if(this.crosshair&&!this.drag){const point=this.crosshair;if(point.x>=plot.x&&point.x<=plot.x+plot.w&&point.y>=plot.y&&point.y<=plot.y+plot.h){const crossX=point.x,crossY=point.y;layer.append(createSVG('line',{'pointer-events':'none','data-crosshair':'vertical',x1:crossX,y1:plot.y,x2:crossX,y2:plot.y+plot.h,stroke:'#657686','stroke-dasharray':'2 3','stroke-width':.6}),createSVG('line',{'pointer-events':'none','data-crosshair':'horizontal',x1:plot.x,y1:crossY,x2:plot.x+plot.w,y2:crossY,stroke:'#657686','stroke-dasharray':'2 3','stroke-width':.6}));this.svg.append(createSVG('rect',{x:plot.x+plot.w+1,y:crossY-8,width:61,height:16,fill:'#35424e'}),createSVG('text',{x:plot.x+plot.w+5,y:crossY+3,fill:'#d6dde3','font-size':10},priceLabel(tx.price(crossY),this.tick)));}}
      this.peerLayer=createSVG('g',{'clip-path':`url(#${this.uid}-clip)`,'pointer-events':'none','data-presence-layer':this.period});layer.append(this.peerLayer);this.renderPresence();
      this.svg.append(layer);this.drawPathControl();
      if(this.snapPreview){const target=this.snapPreview,x=tx.x(target.time),y=tx.y(target.price);this.svg.append(createSVG('circle',{'data-snap-target':target.field,cx:x,cy:y,r:5,fill:'#fff3a3',stroke:'#f1c40f','stroke-width':1.2,'pointer-events':'none'}),createSVG('text',{x:Math.min(x+10,plot.x+plot.w-75),y:Math.max(plot.y+12,y-9),fill:'#f5d565','font-size':10,'pointer-events':'none'},({open:'开',high:'高',low:'低',close:'收'}[target.field])+' '+priceLabel(target.price,this.tick)));}
      for(const level of editable){const rect=level.label,color=level.field==='planned_entry_price'?(priceNumber(shown.stop_price)?(shown.direction==='short'?'#22c55e':'#ef5350'):'#6ca9d4'):'#d7ac66',active=this.drag?.field===level.field||this.hoverLevel===level.field,badge=createSVG('g',{'data-plan-field':level.field,'data-plan-target':'price-label',opacity:planOpacity});badge.append(createSVG('rect',{x:rect.x,y:rect.y,width:rect.w,height:rect.h,fill:active?color:'#18212a',stroke:color,'stroke-width':1}),createSVG('text',{x:rect.x+4,y:rect.y+11,fill:active?'#fff':color,'font-size':9},level.field==='planned_entry_price'?'入场':'止损'),createSVG('text',{x:rect.x+4,y:rect.y+23,fill:active?'#fff':color,'font-size':10},priceLabel(level.price,this.tick)));this.svg.append(badge);}
      const sourceNotice=this.market?.unavailable?'行情读取失败':this.market?.source==='mock'?'模拟行情':this.market?.fresh===true?'':({idle:'已连接 · 暂无新报价',loading:'K线加载中',aligning:'周期对齐中',stale:'刷新未确认',disconnected:'连接中断'}[this.market?.feed_status]||'行情未就绪');if(sourceNotice)this.svg.append(createSVG('text',{x:plot.x+3,y:plot.y+11,fill:'#b18b43','font-size':9},sourceNotice));
    }
    drawIndicators(tx,plot,body){
      const box=this.indicator;if(!box||box.h<30)return;const clip=createSVG('clipPath',{id:this.uid+'-indicator-clip'});clip.append(createSVG('rect',{x:plot.x,y:box.y-6,width:plot.w,height:box.h+6}));const indicatorLayer=createSVG('g',{'clip-path':`url(#${this.uid}-indicator-clip)`});this.svg.append(clip,indicatorLayer);const bars=this.visibleBars(),split=box.y+box.h*.52,maxVolume=Math.max(1,...bars.map(bar=>finite(bar.volume)?bar.volume:0));
      indicatorLayer.append(createSVG('line',{x1:plot.x,y1:box.y-5,x2:plot.x+plot.w,y2:box.y-5,stroke:'#323b44'}),createSVG('line',{x1:plot.x,y1:split,x2:plot.x+plot.w,y2:split,stroke:'#252e36'}),createSVG('text',{x:plot.x+3,y:box.y+9,fill:'#74838f','font-size':9},'CCL · 持仓'),createSVG('text',{x:plot.x+3,y:split+10,fill:'#74838f','font-size':9},'VOL · 成交量'));
      const oi=bars.filter(bar=>finite(bar.oi)),oiMin=Math.min(...oi.map(bar=>bar.oi)),oiMax=Math.max(...oi.map(bar=>bar.oi)),oiY=value=>box.y+12+(oiMax-value)/Math.max(1,oiMax-oiMin)*(split-box.y-17);if(oi.length)indicatorLayer.append(createSVG('polyline',{points:oi.map(bar=>`${tx.x(bar.time)},${oiY(bar.oi)}`).join(' '),fill:'none',stroke:'#b7bdc4','stroke-width':.8}));
      for(const bar of bars){if(!finite(bar.volume))continue;const x=tx.x(bar.time),height=(box.y+box.h-split-4)*bar.volume/maxVolume,color=bar.close>=bar.open?'#a84947':'#22c55e';indicatorLayer.append(createSVG('rect',{x:x-body/2,y:box.y+box.h-height,width:body,height,fill:color,opacity:.8}));}
    }
    renderPresence(){
      const layer=this.peerLayer,tx=this.tx,plot=this.plot;if(!layer||!tx||!plot)return;layer.replaceChildren();
      for(const peer of this.peers||[]){
        if(peer.period!==this.period)continue;
        const name=peer.name||peer.user?.name||'伙伴',color=peer.color||peer.user?.color||'#6d8baf',id=peer.id||peer.user_id||peer.user?.id;
        const selected=this.objects().find(o=>o.id===peer.object_id);if(selected?.points?.length){const points=this.objectPoints(selected),xs=points.map(p=>p.x),ys=points.map(p=>p.y),x=Math.max(plot.x,Math.min(...xs)-7),y=Math.max(plot.y,Math.min(...ys)-7),right=Math.min(plot.x+plot.w,Math.max(...xs)+7),bottom=Math.min(plot.y+plot.h,Math.max(...ys)+7);if(right>x&&bottom>y)layer.append(createSVG('rect',{'data-peer-selection':id,x,y,width:right-x,height:bottom-y,fill:'none',stroke:color,'stroke-width':1,'stroke-dasharray':'3 3'}),createSVG('text',{x:x+3,y:Math.max(plot.y+12,y-4),fill:color,'font-size':10},name+' · 正在编辑'));}
        if(peer.preview_object){const ghost=createSVG('g',{opacity:.5,'data-peer-preview':id});this.drawObject(ghost,{...peer.preview_object,color},{selection:false});layer.append(ghost);}
        if(!peer.cursor)continue;const x=tx.x(peer.cursor.time),y=tx.y(peer.cursor.price);
        if(['field:planned_entry_price','field:stop_price'].includes(peer.object_id)&&y>=plot.y&&y<=plot.y+plot.h){const label=peer.object_id==='field:stop_price'?'止损':'入场';layer.append(createSVG('line',{'data-peer-level':peer.object_id,x1:plot.x,y1:y,x2:plot.x+plot.w,y2:y,stroke:color,'stroke-width':1,'stroke-dasharray':'4 4',opacity:.7}),createSVG('text',{x:plot.x+8,y:y-6,fill:color,'font-size':10},name+' · '+label+' '+priceLabel(peer.cursor.price,this.tick)));}
        if(x<plot.x||x>plot.x+plot.w||y<plot.y||y>plot.y+plot.h)continue;
        const group=createSVG('g',{'data-peer-cursor':id,'data-peer-period':this.period,transform:`translate(${x} ${y})`});
        const width=Math.min(180,Math.max(44,name.length*11+14)),left=x+width+14>plot.x+plot.w?-width-6:12,top=y+37>plot.y+plot.h?-22:17;
        group.append(createSVG('path',{d:'M 0 0 L 0 18 L 5 13 L 10 22 L 14 20 L 9 11 L 17 11 Z',fill:color,stroke:'#10151b','stroke-width':1.2}),createSVG('rect',{x:left,y:top,width,height:20,rx:4,fill:color}),createSVG('text',{x:left+7,y:top+14,fill:'#fff','font-size':11,'font-weight':600},name));layer.append(group);
      }
    }
    nameText(object,color,attrs={}){const label=this.nameLabel(object),text=createSVG('text',{'data-edit-name':object.id||'',x:label.x,y:label.y,'text-anchor':label.anchor,fill:color,'font-size':label.size,style:this.readOnly||object.locked?'':'cursor:text',...attrs},label.lines?undefined:label.name);if(label.lines){text.setAttribute('xml:space','preserve');for(const [index,line]of label.lines.entries())text.append(createSVG('tspan',{x:label.x,y:label.y+index*label.lineHeight},line));}return text;}
    drawObject(layer,object,{selection=true}={}){const tx=this.tx,plot=this.plot;if(!object.points?.length)return;const periodOpacity=['path','wave'].includes(object.kind)?pathPeriodOpacity(object.period,this.period):1;if(periodOpacity===null)return;const parent=layer,opacity=drawingOpacity(object.opacity),group=createSVG('g',{'data-chart-object':object.id||'','data-object-artwork':object.id||object.kind,opacity});layer.append(group);layer=group;if(periodOpacity<1){const mapped=createSVG('g',{opacity:periodOpacity,'data-mapped-wave':isForecast(object)?object.id:'','data-source-period':object.period});layer.append(mapped);layer=mapped;}const selected=!this.readOnly&&selection&&object.id&&object.id===this.selected,color=object.color||'#f1c40f',width=Number(object.width)||1.5,dash=object.line_style==='dashed'?'5 4':'';
      if(object.kind==='zone'){
        const bounds=zoneBounds(object);if(!bounds)return;const zoneColor=object.color||COLORS[object.zone_type]||COLORS.danger,patternId=this.uid+'-zone-'+(this.patternSeq=(this.patternSeq||0)+1),defs=createSVG('defs'),pattern=createSVG('pattern',{id:patternId,width:7,height:7,patternUnits:'userSpaceOnUse',patternTransform:'rotate(35)'});
        pattern.append(createSVG('rect',{width:7,height:7,fill:zoneColor,'fill-opacity':.1}),createSVG('line',{x1:0,y1:0,x2:0,y2:7,stroke:zoneColor,'stroke-opacity':.35}));defs.append(pattern);layer.append(defs);
        layer.append(createSVG('rect',{'data-zone':object.id||'',x:tx.x(bounds.start),y:tx.y(bounds.high),width:Math.max(1,tx.x(bounds.end)-tx.x(bounds.start)),height:Math.max(1,tx.y(bounds.low)-tx.y(bounds.high)),fill:`url(#${patternId})`,stroke:zoneColor,'stroke-width':selected?2:1,'stroke-dasharray':selected?'':'4 2'}),this.nameText(object,zoneColor,{'data-zone-title':object.id||''}));
      }else if(object.kind==='horizontal'){const point=object.points[0];layer.append(createSVG('line',{x1:plot.x,y1:tx.y(point.price),x2:plot.x+plot.w,y2:tx.y(point.price),stroke:color,'stroke-width':(selected?width+1:width)/2,'stroke-dasharray':dash}),this.nameText(object,color),createSVG('text',{'data-layer-price':object.id||'',x:plot.x+10+this.nameLabel(object).width,y:tx.y(point.price)-4,fill:color,'font-size':10},priceLabel(point.price,this.tick)));
      }else if(object.kind==='segment'&&object.points.length===2){const [a,b]=this.objectPoints(object),right=Math.max(a.x,b.x),label=priceLabel(object.points[0].price,this.tick),labelWidth=label.length*6.5,x=right+labelWidth+8<=plot.x+plot.w?right+6:Math.min(right,plot.x+plot.w)-labelWidth;layer.append(createSVG('line',{'data-price-segment':object.id||'',x1:a.x,y1:a.y,x2:b.x,y2:a.y,stroke:color,'stroke-width':(selected?width+1:width)/2,'stroke-dasharray':dash}),createSVG('text',{'data-segment-price':object.id||'',x,y:a.y-5,fill:color,'font-size':11},label));if(this.nameLabel(object))layer.append(this.nameText(object,color));
      }else if(object.kind==='text'){const point=object.points[0],label=this.nameLabel(object);layer.append(createSVG('circle',{cx:tx.x(point.time),cy:tx.y(point.price),r:3,fill:color}),this.nameText(object,color,{'paint-order':'stroke',stroke:'#0d1115','stroke-width':3}));if(selected)layer.append(createSVG('rect',{'data-text-selection':object.id,x:label.left-3,y:label.top-3,width:label.width+6,height:label.height+6,fill:'none',stroke:color,'stroke-width':1,'stroke-dasharray':'3 3','pointer-events':'none'}));
      }else{let points=this.objectPoints(object);if(['trend','reference'].includes(object.kind)&&points.length>=2)points=infiniteSegment(points[0],points[1],plot);layer.append(createSVG('polyline',{'data-wave-line':isForecast(object)?object.id:'',points:points.map(point=>`${point.x},${point.y}`).join(' '),fill:'none',stroke:color,'stroke-width':selected?width+1:width,'stroke-dasharray':dash}));if(['path','wave'].includes(object.kind)){object.points.forEach((point,index)=>{const x=tx.x(this.pointTime(point)),y=tx.y(point.price),active=selected&&this.selectedAnchor===index;layer.append(createSVG('circle',{'data-wave-node':isForecast(object)?point.id:'','data-wave-index':index,'data-node-selected':String(active),cx:x,cy:y,r:active?10:7,fill:active?'#fff3a3':'#171b20',stroke:active?'#ffffff':color,'stroke-width':active?2.5:1.2}),createSVG('text',{x,y:y+3,'text-anchor':'middle',fill:active?'#151719':color,'font-size':9,'font-weight':600},point.label||index+1));});}else{const point=object.points[0];layer.append(this.nameText(object,color));}}
      if(selected&&!object.locked&&!['path','wave','zone'].includes(object.kind))for(const point of object.points)layer.append(createSVG('circle',{'data-object-anchor':object.id,'data-anchor-index':object.points.indexOf(point),cx:tx.x(point.time),cy:tx.y(point.price),r:4,fill:'#11161b',stroke:'#f4d35e','stroke-width':1.4}));
      if(selected&&opacity*periodOpacity<.5)this.drawSelectionAffordance(parent,object);
    }
    drawSelectionAffordance(layer,object){
      if(this.readOnly)return;
      const plot=this.plot,points=this.objectPoints(object),xs=points.map(point=>point.x),ys=points.map(point=>point.y),horizontal=object.kind==='horizontal',text=object.kind==='text'?this.nameLabel(object):null;if(text){xs.push(text.left,text.left+text.width);ys.push(text.top,text.top+text.height);}
      const left=Math.max(plot.x,horizontal?plot.x:Math.min(...xs)-7),right=Math.min(plot.x+plot.w,horizontal?plot.x+plot.w:Math.max(...xs)+7),top=Math.max(plot.y,Math.min(...ys)-7),bottom=Math.min(plot.y+plot.h,Math.max(...ys)+7);
      const outline=createSVG('g',{'data-chart-object':object.id,'data-object-selection':object.id,opacity:.9,'pointer-events':'none'});
      if(right>left&&bottom>top)outline.append(createSVG('rect',{x:left,y:top,width:right-left,height:bottom-top,fill:'none',stroke:'#b7d7ee','stroke-width':1,'stroke-dasharray':'3 3'}));
      if(!object.locked)for(const [index,point]of points.entries()){const x=horizontal?Math.max(plot.x+5,Math.min(plot.x+plot.w-5,point.x)):point.x,y=point.y;if(x<plot.x||x>plot.x+plot.w||y<plot.y||y>plot.y+plot.h)continue;outline.append(createSVG('circle',{'data-selection-anchor':index,cx:x,cy:y,r:this.selectedAnchor===index?6:4,fill:'#11161b',stroke:this.selectedAnchor===index?'#fff':'#b7d7ee','stroke-width':1.5}));}
      layer.append(outline);
    }
  }
  return{isForecast,timeScale,resolvePointTime,anchorPoint,preserveView,dailyRange,rangeBars,fitVisiblePrice,candleWidth,wavePoints,Chart,timestamp,snap,priceNumber,zoneBounds,transform,fitView,zoomView,segmentDistance,infiniteSegment,editPathPoints,pathPeriodOpacity,visibleOnPeriod,priceLabel,mergePending,pendingAfterAck,adoptRemoteConflicts,validStopSide,planLevelStatus,planLevelGeometry,hitPlanLevel,beginLevelDrag,moveLevelDrag,levelCommit,PLAN_FIELDS,INTERVAL,TYPES};
}));
