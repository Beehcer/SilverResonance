'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');

function harness(run){
  const before={document:global.document,ResizeObserver:global.ResizeObserver,requestAnimationFrame:global.requestAnimationFrame,cancelAnimationFrame:global.cancelAnimationFrame};
  const frames=new Map();let nextFrame=0;
  const make=tag=>({tag,style:{},attrs:{},children:[],listeners:new Map(),hidden:false,
    setAttribute(key,value){this.attrs[key]=String(value);if(key==='viewBox'){const [x,y,width,height]=String(value).split(' ').map(Number);this.viewBox={baseVal:{x,y,width,height}};}},getAttribute(key){return this.attrs[key];},
    append(...items){this.children.push(...items);},replaceChildren(...items){this.children=[...items];},addEventListener(type,fn,options){const entries=this.listeners.get(type)||[];entries.push({fn,options});this.listeners.set(type,entries);},
    getBoundingClientRect(){return{left:0,top:0,width:500,height:320};},focus(){},hasPointerCapture(){return false;},setPointerCapture(){assert.fail('Readonly touch must never capture a pointer');}});
  const document=make('document');document.createElement=make;document.createElementNS=(_,tag)=>make(tag);
  global.document=document;global.ResizeObserver=class{constructor(callback){this.callback=callback;}observe(){}};global.requestAnimationFrame=fn=>{const id=++nextFrame;frames.set(id,fn);return id;};global.cancelAnimationFrame=id=>frames.delete(id);
  const drain=()=>{const callbacks=[...frames.values()];frames.clear();for(const callback of callbacks)callback();};
  const fire=(target,type,event)=>{for(const {fn}of target.listeners.get(type)||[])fn(event);};
  const market=(count=80,symbol='SHFE.rb2701')=>{const start=Date.parse('2026-11-11T01:00:00Z'),bars=Array.from({length:count},(_,i)=>({time:start+i*C.INTERVAL.m3+(i>=40?7*3600000:0),open:100+i%5,high:110+i%4,low:90-i%3,close:102+i%4,volume:100,oi:300}));return{symbol,tick:1,fresh:true,history_token:'history-'+count,periods:{m3:bars}};};
  const create=()=>{
    const host=Object.assign(make('div'),{clientWidth:500,clientHeight:320}),events={views:[],interactions:0,activations:[],presence:[],mutations:[]};
    const mutation=(...args)=>events.mutations.push(args),chart=new C.Chart(host,'m3',{add:mutation,update:mutation,deleteObject:mutation,placeLevel:mutation,dragLevel:mutation,interaction:()=>events.interactions++,activate:p=>events.activations.push(p),presence:p=>events.presence.push(p),viewChanged:c=>events.views.push(structuredClone(c.view))});
    const data=market(),last=data.periods.m3.at(-1).time,plan={symbol:data.symbol,objects:[{id:'wave',kind:'wave',period:'m3',name:'预期',points:C.wavePoints([C.anchorPoint({time:last+C.INTERVAL.m3*1.5,price:104},data.periods.m3,'m3'),C.anchorPoint({time:last+C.INTERVAL.m3*3.5,price:98},data.periods.m3,'m3')])}]};
    chart.setData(data,plan,true);chart.setReadOnly(true);events.views=[];events.presence=[];return{chart,host,events,plan,data};
  };
  const touch=(chart,identifier,x,y)=>({identifier,clientX:x,clientY:y,target:chart.touchSurface});
  const emit=(chart,type,touches,changedTouches=touches,extra={})=>{const event={touches,changedTouches,cancelable:true,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){assert.fail('Chart must not stop global gesture protection');},...extra};if(type==='touchstart')fire(chart.touchSurface,type,event);fire(document,type,event);return event;};
  const start=(chart,ids=[1,2],points=[[150,100],[250,100]])=>{const a=touch(chart,ids[0],...points[0]),b=touch(chart,ids[1],...points[1]);const first=emit(chart,'touchstart',[a],[a]),second=emit(chart,'touchstart',[a,b],[b]);return{a,b,first,second};};
  const slots=chart=>{const scale=chart.getTimeScale();return scale.position(chart.view.end)-scale.position(chart.view.start);};
  try{return run({create,market,touch,emit,start,drain,frames,fire,document,slots});}finally{Object.assign(global,before);}
}
const near=(a,b,eps=1e-5)=>assert.ok(Math.abs(a-b)<eps,`${a} != ${b}`);

test('two-finger spread zooms the compressed session scale around the moving midpoint and saves only on finish',()=>harness(({create,start,touch,emit,drain,frames,slots})=>{
  const {chart,events,plan}=create(),saved=JSON.stringify(plan),before=slots(chart),view={...chart.view},anchor={time:chart.tx.time(200),price:chart.tx.price(100)};
  const {a,b,first,second}=start(chart);assert.equal(first.defaultPrevented,false);assert.equal(second.defaultPrevented,true);assert.equal(events.interactions,1);assert.deepEqual(events.activations,['m3']);assert.equal(chart.pinch.ids.length,2);
  emit(chart,'touchmove',[touch(chart,1,100,120),touch(chart,2,300,120)]);emit(chart,'touchmove',[touch(chart,2,310,125),touch(chart,1,110,125)]);assert.equal(events.views.length,0);assert.equal(frames.size,1);drain();
  near(slots(chart),before/2);near(chart.tx.x(anchor.time),210);near(chart.tx.y(anchor.price),125);assert.equal(chart.autoPrice,false);assert.equal(chart.followLatest,false);
  emit(chart,'touchend',[],[a,b]);drain();assert.equal(chart.pinch,null);assert.equal(events.views.length,1);assert.equal(JSON.stringify(plan),saved);assert.deepEqual(events.mutations,[]);assert.ok(events.presence.every(value=>!value.editing&&!value.preview_object));assert.notDeepEqual(chart.view,view);
}));

test('bringing fingers together zooms out, bounded by existing minimum candle count and zoom limit',()=>harness(({create,start,touch,emit,slots})=>{
  const {chart}=create();let original=slots(chart);start(chart);emit(chart,'touchmove',[touch(chart,1,175,100),touch(chart,2,225,100)]);near(slots(chart),original*2);emit(chart,'touchend',[],[]);
  chart.clearTouchGesture();for(let i=0;i<4;i++){const pair=start(chart);emit(chart,'touchmove',[touch(chart,1,199.9,100),touch(chart,2,200.1,100)]);emit(chart,'touchend',[],[pair.a,pair.b]);}
  assert.ok(slots(chart)<=chart.zoomLimit()+1e-6);assert.ok(Object.values(chart.view).every(Number.isFinite));
  for(let i=0;i<4;i++){const pair=start(chart);emit(chart,'touchmove',[touch(chart,1,-2300,100),touch(chart,2,2700,100)]);emit(chart,'touchend',[],[pair.a,pair.b]);}
  near(slots(chart),4);assert.ok(chart.view.max>chart.view.min);
}));

test('single-finger scrolling is untouched and a browser-owned uncancelable gesture never becomes chart pinch',()=>harness(({create,start,touch,emit,fire,document})=>{
  const {chart,events}=create(),original={...chart.view},a=touch(chart,1,150,100),b=touch(chart,2,250,100);
  assert.equal(emit(chart,'touchstart',[a],[a]).defaultPrevented,false);assert.equal(emit(chart,'touchmove',[touch(chart,1,150,140)],[a]).defaultPrevented,false);
  const second=emit(chart,'touchstart',[a,b],[b],{cancelable:false});assert.equal(second.defaultPrevented,false);assert.equal(chart.pinch,null);assert.equal(chart.touchBlocked,true);
  emit(chart,'touchmove',[touch(chart,1,90,90),touch(chart,2,310,90)]);assert.deepEqual(chart.view,original);emit(chart,'touchend',[],[a,b]);assert.equal(chart.touchBlocked,false);
  start(chart);emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)],[],{cancelable:false});assert.equal(chart.pinch,null);assert.deepEqual(chart.view,original);assert.equal(events.views.length,0);
  assert.equal(chart.touchSurface.listeners.get('touchstart')[0].options.passive,false);assert.equal(document.listeners.get('touchmove')[0].options.passive,false);
}));

test('touches beginning in separate charts never zoom either chart even if they move over the same chart',()=>harness(({create,touch,emit})=>{
  const a=create(),b=create(),first=touch(a.chart,1,150,100),second=touch(b.chart,2,250,100),av={...a.chart.view},bv={...b.chart.view};
  emit(a.chart,'touchstart',[first],[first]);emit(b.chart,'touchstart',[first,second],[second]);emit(a.chart,'touchmove',[{...first,clientX:80},{...second,clientX:320}]);
  assert.equal(a.chart.pinch,null);assert.equal(b.chart.pinch,null);assert.deepEqual(a.chart.view,av);assert.deepEqual(b.chart.view,bv);assert.equal(a.events.views.length+b.events.views.length,0);
  emit(a.chart,'touchend',[second],[first]);emit(b.chart,'touchend',[],[second]);assert.equal(a.chart.touchIds.size+b.chart.touchIds.size,0);assert.equal(b.chart.touchBlocked,false);
}));

test('third fingers and finger replacement finish once then require all original touches to lift',()=>harness(({create,start,touch,emit})=>{
  const {chart,events}=create(),{a,b}=start(chart);emit(chart,'touchmove',[touch(chart,1,110,100),touch(chart,2,290,100)]);const view={...chart.view},third=touch(chart,3,310,140);
  emit(chart,'touchstart',[a,b,third],[third]);assert.equal(chart.pinch,null);assert.equal(events.views.length,1);assert.equal(chart.touchBlocked,true);
  emit(chart,'touchend',[a,b],[third]);emit(chart,'touchmove',[touch(chart,1,80,100),touch(chart,2,320,100)]);assert.deepEqual(chart.view,view);
  emit(chart,'touchend',[a],[b]);const replacement=touch(chart,4,300,100);emit(chart,'touchstart',[a,replacement],[replacement]);assert.equal(chart.pinch,null);emit(chart,'touchend',[],[a,replacement]);assert.equal(chart.touchBlocked,false);
  start(chart,[5,6]);assert.ok(chart.pinch);emit(chart,'touchcancel',[],[]);assert.equal(chart.pinch,null);assert.equal(chart.touchIds.size,0);assert.equal(events.views.length,1);
}));

test('a third finger elsewhere on the page interrupts the active chart before another chart move',()=>harness(({create,start,touch,emit})=>{
  const a=create(),other=create();start(a.chart);emit(a.chart,'touchmove',[touch(a.chart,1,100,100),touch(a.chart,2,300,100)]);
  const outside=touch(other.chart,3,200,100);emit(other.chart,'touchstart',[touch(a.chart,1,100,100),touch(a.chart,2,300,100),outside],[outside]);assert.equal(a.chart.pinch,null);assert.equal(a.events.views.length,1);
}));

test('zero distance and nonfinite touches never create invalid view ranges or writes',()=>harness(({create,start,touch,emit})=>{
  const {chart,events}=create(),original={...chart.view};start(chart,[1,2],[[200,100],[200,100]]);assert.equal(chart.pinch,null);emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);assert.deepEqual(chart.view,original);
  emit(chart,'touchcancel',[],[]);start(chart);emit(chart,'touchmove',[touch(chart,1,NaN,100),touch(chart,2,300,100)]);assert.equal(chart.pinch,null);assert.deepEqual(chart.view,original);assert.deepEqual(events.mutations,[]);assert.equal(events.views.length,0);
}));

test('market ticks are deferred during pinch while peer drawings update, then latest bars preserve viewport slots',()=>harness(({create,start,touch,emit,market,drain,slots})=>{
  const {chart,events,plan}=create(),original=JSON.stringify(plan);const pair=start(chart);emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);drain();const width=slots(chart),range={...chart.view};
  const peerPlan={...plan,objects:[...plan.objects,{id:'peer',kind:'text',period:'m3',name:'伙伴实时备注',points:[{time:chart.bars.at(-2).time,price:102}]}]};
  chart.setData(market(81),peerPlan);chart.setData(market(82),peerPlan);assert.equal(chart.bars.length,80);assert.equal(chart.plan,peerPlan);assert.deepEqual(chart.view,range);assert.equal(chart.pendingMarket.periods.m3.length,82);
  emit(chart,'touchend',[],[pair.a,pair.b]);drain();assert.equal(chart.bars.length,82);assert.equal(chart.pendingMarket,null);near(slots(chart),width);assert.equal(events.views.length,1);assert.equal(JSON.stringify(plan),original);assert.equal(chart.plan.objects.at(-1).name,'伙伴实时备注');assert.deepEqual(events.mutations,[]);
}));

test('symbol reset, tool reset and switching readonly discard stale gesture state without saving into the new context',()=>{
  for(const change of ['symbol','reset','tool','readonly'])harness(({create,start,touch,emit,market})=>{
    const {chart,events,plan}=create();start(chart);emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);chart.setData(market(81),plan);
    if(change==='symbol')chart.setData(market(32,'SHFE.au2612'),{objects:[]});else if(change==='reset')chart.setData(market(32),{objects:[]},true);else if(change==='tool')chart.setTool('select');else chart.setReadOnly(false);
    assert.equal(chart.pinch,null,change);assert.equal(chart.touchIds.size,0);assert.equal(chart.touchBlocked,false);assert.equal(events.views.length,change==='reset'?1:0,'Only an explicit reset fit may publish the new viewport.');
    const view={...chart.view};emit(chart,'touchmove',[touch(chart,1,80,100),touch(chart,2,320,100)]);assert.deepEqual(chart.view,view);assert.deepEqual(events.mutations,[]);
    if(change==='symbol')assert.equal(chart.market.symbol,'SHFE.au2612');if(change==='readonly')assert.equal(chart.touchSurface.hidden,true);
  });
});

test('the stable touch surface survives SVG replacement and desktop mode ignores all touch zoom handlers',()=>harness(({create,start,touch,emit,drain})=>{
  const {chart,events}=create(),surface=chart.touchSurface;start(chart);chart.render();assert.equal(chart.touchSurface,surface);assert.ok(chart.host.children.includes(surface));emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);drain();assert.ok(chart.pinch.changed);
  chart.setReadOnly(false);events.views=[];const view={...chart.view};const result=emit(chart,'touchstart',[touch(chart,3,100,100),touch(chart,4,300,100)]);emit(chart,'touchmove',[touch(chart,3,50,100),touch(chart,4,350,100)]);assert.equal(result.defaultPrevented,false);assert.deepEqual(chart.view,view);assert.equal(events.views.length,0);assert.equal(chart.svg.style.touchAction,'none');
}));

test('rotation finishes a changed gesture once and waits for fingers to lift before using the resized plot',()=>harness(({create,start,touch,emit})=>{
  const {chart,host,events}=create(),{a,b}=start(chart);chart.observer.callback();assert.ok(chart.pinch,'An observer notification with unchanged size is harmless.');
  emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);const view={...chart.view};host.clientWidth=390;host.clientHeight=400;chart.observer.callback();
  assert.equal(chart.pinch,null);assert.equal(chart.touchBlocked,true);assert.equal(events.views.length,1);assert.deepEqual(chart.view,view);assert.equal(chart.svg.attrs.width,'390');
  emit(chart,'touchmove',[touch(chart,1,60,100),touch(chart,2,320,100)]);assert.deepEqual(chart.view,view);emit(chart,'touchend',[],[a,b]);start(chart,[3,4],[[100,100],[200,100]]);assert.equal(chart.pinch.width,390);assert.equal(chart.pinch.height,400);
}));

test('touch midpoint uses scaled SVG coordinates and keeps the value under both fingers',()=>harness(({create,start,touch,emit,drain})=>{
  const {chart}=create();chart.svg.getBoundingClientRect=()=>({left:20,top:40,width:250,height:160});
  const anchor={time:chart.tx.time(200),price:chart.tx.price(100)};start(chart,[1,2],[[95,90],[145,90]]);emit(chart,'touchmove',[touch(chart,1,70,90),touch(chart,2,170,90)]);drain();
  near(chart.tx.x(anchor.time),200);near(chart.tx.y(anchor.price),100);assert.ok(chart.pinch.changed);
}));

test('pointer cancellation does not discard a separately tracked touch pinch or release its deferred market frame',()=>harness(({create,start,touch,emit,fire,market})=>{
  const {chart,plan,events}=create();start(chart);chart.setData(market(81),plan);fire(chart.svg,'pointercancel',{});
  assert.ok(chart.pinch);assert.equal(chart.bars.length,80);emit(chart,'touchmove',[touch(chart,1,100,100),touch(chart,2,300,100)]);assert.ok(chart.pinch.changed);assert.deepEqual(events.mutations,[]);
  emit(chart,'touchcancel',[],[]);assert.equal(chart.pinch,null);assert.equal(chart.bars.length,81);assert.equal(events.views.length,1);
}));
