'use strict';
const assert=require('node:assert/strict'),{test}=require('node:test'),{performance}=require('node:perf_hooks');
const C=require('./static/workspace-chart.js');

function fixture(run){
  const previous={document:global.document,ResizeObserver:global.ResizeObserver,requestAnimationFrame:global.requestAnimationFrame,cancelAnimationFrame:global.cancelAnimationFrame};
  const frames=new Map();let frameId=0,created=0;
  const make=tag=>({tag,attrs:{},style:{},children:[],listeners:{},setAttribute(key,value){this.attrs[key]=String(value);},getAttribute(key){return this.attrs[key];},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},addEventListener(type,fn){this.listeners[type]=fn;},getBoundingClientRect(){return{left:0,top:0,width:1000,height:600};},focus(){},setPointerCapture(){},hasPointerCapture(){return false;}});
  global.document={createElement:make,createElementNS:(_,tag)=>{created++;return make(tag);}};
  global.ResizeObserver=class{observe(){}};
  global.requestAnimationFrame=fn=>{const id=++frameId;frames.set(id,fn);return id;};global.cancelAnimationFrame=id=>frames.delete(id);
  const drain=()=>{const work=[...frames.values()];frames.clear();for(const fn of work)fn();};
  const host=()=>Object.assign(make('div'),{clientWidth:1000,clientHeight:600});
  try{return run({create:()=>new C.Chart(host(),'m3',{}),make,drain,frames,created:()=>created});}finally{Object.assign(global,previous);}
}
function market(count){const base=Date.parse('2026-10-01T09:00:00+08:00');return{symbol:'SHFE.rb2701',tick:1,fresh:true,source:'live',history_token:'history-'+count,periods:{m3:Array.from({length:count},(_,index)=>{const price=3000+Math.sin(index/11)*20;return{dt:new Date(base+index*C.INTERVAL.m3).toISOString(),open:price,high:price+5,low:price-5,close:price+2,volume:100+index%30,oi:1000+index};})}};}
const walk=node=>[node,...node.children.flatMap(walk)];
function median(samples){return samples.sort((a,b)=>a-b)[Math.floor(samples.length/2)];}
function measure(action,count=20){const samples=[];for(let i=0;i<count;i++){const start=performance.now();action(i);samples.push(performance.now()-start);}return Number(median(samples).toFixed(3));}
function benchmark(){return fixture(({create,drain})=>[180,6000,10000].map(count=>{
  const data=market(count),plan={objects:[]};let chart;
  const initial=measure(()=>{chart=create();chart.setData(data,plan,true);},10);
  const repeated=measure(()=>chart.setData(data,plan));
  const render=measure(()=>chart.render());
  const move=measure(i=>{chart.move({clientX:300+i,clientY:200,pointerId:1});drain();});
  return{bars:count,initial_set_data_ms:initial,repeated_set_data_ms:repeated,render_ms:render,pointer_move_frame_ms:move};
}));}

if(process.argv.includes('--benchmark'))console.log(JSON.stringify({runtime:process.version,measure:'median milliseconds; real Chart with fake DOM, not browser end-to-end',results:benchmark()},null,2));
else{
  test('benchmark fixture renders real candle and moving-average SVG',()=>fixture(({create})=>{
    const chart=create();chart.setData(market(180),{objects:[]},true);
    const nodes=walk(chart.svg);assert.ok(nodes.some(node=>node.attrs['data-candle']));assert.ok(nodes.some(node=>node.tag==='polyline'&&node.attrs.stroke==='#d8c345'));
  }));

  test('repeated snapshots and pointer frames reuse time scales and averages rather than revisiting full history',()=>fixture(({create,drain})=>{
    const chart=create(),data=market(10000),plan={objects:[]};chart.setData(data,plan,true);
    const bars=chart.bars,scale=chart.getTimeScale(),averages=chart.getAverages(20);
    let reads=0;for(const bar of bars){const close=bar.close;Object.defineProperty(bar,'close',{get(){reads++;return close;}});}
    chart.setData(data,plan);chart.render();chart.move({clientX:320,clientY:180,pointerId:1});drain();
    assert.strictEqual(chart.bars,bars);assert.strictEqual(chart.getTimeScale(),scale);assert.strictEqual(chart.getAverages(20),averages);
    assert.ok(reads<1000,`Only visible bars should be visited, read ${reads} closes.`);
  }));

  test('visible SVG stays bounded while moving averages still include preceding offscreen history',()=>fixture(({create})=>{
    const chart=create();chart.setData(market(10000),{objects:[]},true);
    const nodes=walk(chart.svg),candles=nodes.filter(node=>node.attrs['data-candle']),line=nodes.find(node=>node.tag==='polyline'&&node.attrs.stroke==='#d8c345');
    assert.ok(candles.length>50&&candles.length<90);assert.ok(nodes.length<400,'Offscreen candles and volumes must not create SVG nodes.');
    const coords=line.attrs.points.split(' ').map(value=>value.split(',').map(Number));assert.ok(coords.length<90);
    const firstTime=chart.tx.time(coords[0][0]),index=chart.bars.findIndex(bar=>Math.abs(bar.time-firstTime)<1);
    const expected=chart.bars.slice(index-9,index+1).reduce((sum,bar)=>sum+bar.close,0)/10;
    assert.ok(index>9000);assert.ok(Math.abs(chart.tx.price(coords[0][1])-expected)<1e-8);
  }));

  test('immutable tail updates reuse unchanged normalized rows and invalidate SMA and corrected history',()=>fixture(({create})=>{
    const chart=create(),data=market(180);data.periods.m3.forEach(Object.freeze);Object.freeze(data.periods.m3);
    chart.setData(data,{objects:[]},true);const previousBars=chart.bars,previousAverage=chart.getAverages(10);
    const tail=Object.freeze({...data.periods.m3.at(-1),close:3049,high:3050}),next={...data,periods:{m3:[...data.periods.m3.slice(0,-1),tail]}};
    chart.setData(next,chart.plan);assert.strictEqual(chart.bars[0],previousBars[0]);assert.notStrictEqual(chart.bars.at(-1),previousBars.at(-1));assert.notStrictEqual(chart.getAverages(10),previousAverage);
    assert.equal(previousBars.at(-1).close,data.periods.m3.at(-1).close);assert.equal(chart.bars.at(-1).close,3049);
    const expected=chart.bars.slice(-10).reduce((sum,bar)=>sum+bar.close,0)/10;assert.ok(Math.abs(chart.getAverages(10).at(-1).value-expected)<1e-8);
    const corrected={...next,history_token:'corrected-prefix',periods:{m3:next.periods.m3.map((bar,index)=>index===150?{...bar,close:3100,high:3101}:bar)}};
    chart.setData(corrected,chart.plan);assert.equal(chart.bars[150].close,3100);assert.ok(Math.abs(chart.getAverages(10)[150].value-chart.bars.slice(141,151).reduce((sum,bar)=>sum+bar.close,0)/10)<1e-8);
  }));

  test('mutable quote corrections and feed status invalidate caches without moving stored drawings',()=>fixture(({create})=>{
    const chart=create(),data=market(180),time=Date.parse(data.periods.m3.at(-1).dt),point=C.anchorPoint({time:time+C.INTERVAL.m3*2.5,price:3001},data.periods.m3,'m3');
    const plan={objects:[{id:'w',kind:'wave',period:'m3',points:C.wavePoints([point,{...point,time:point.time+C.INTERVAL.m3,price:3004,chart_anchor:{...point.chart_anchor,offset:3.5}}])}]},saved=JSON.stringify(plan);
    chart.setData(data,plan,true);const previous=chart.getAverages(5);data.periods.m3.at(-2).close+=5;chart.setData(data,plan);
    assert.notStrictEqual(chart.getAverages(5),previous);assert.equal(chart.bars.at(-2).close,data.periods.m3.at(-2).close);
    chart.setData({...data,fresh:false,feed_status:'disconnected'},plan);
    assert.ok(walk(chart.svg).some(node=>node.textContent==='连接中断'));assert.equal(JSON.stringify(plan),saved);
  }));

  test('same-market plan changes render immediately without rebuilding market caches',()=>fixture(({create})=>{
    const chart=create(),data=market(180);chart.setData(data,{objects:[]},true);const scale=chart.getTimeScale(),averages=chart.getAverages(5);
    chart.setData(data,{objects:[{id:'shared',kind:'horizontal',name:'伙伴刚画的线',color:'#00ff00',points:[{time:chart.bars.at(-1).time,price:3002}]}]});
    assert.ok(walk(chart.svg).some(node=>node.attrs['data-chart-object']==='shared'));assert.strictEqual(chart.getTimeScale(),scale);assert.strictEqual(chart.getAverages(5),averages);
  }));

  test('nested synchronous tool/data/view batches render the final viewport once',()=>fixture(({create})=>{
    const chart=create(),data=market(180);let replacements=0;const replace=chart.svg.replaceChildren;chart.svg.replaceChildren=function(...nodes){replacements++;return replace.apply(this,nodes);};
    chart.beginUpdate();chart.setTool('select');chart.beginUpdate();chart.setData(data,{objects:[]},true);chart.endUpdate();assert.equal(replacements,0);
    chart.view={...chart.view,min:2900,max:3100};chart.autoPrice=false;chart.render();chart.endUpdate();assert.equal(replacements,1);assert.equal(chart.view.min,2900);
    assert.ok(walk(chart.svg).some(node=>node.attrs['data-candle']));assert.equal(chart.updateDepth,0);
  }));

  test('an immediate collaboration redraw cancels an already queued pointer frame without dropping its crosshair',()=>fixture(({create,drain,frames})=>{
    const chart=create();chart.setData(market(180),{objects:[]},true);let replacements=0;const replace=chart.svg.replaceChildren;chart.svg.replaceChildren=function(...nodes){replacements++;return replace.apply(this,nodes);};
    chart.move({clientX:300,clientY:200,pointerId:1});chart.move({clientX:320,clientY:210,pointerId:1});assert.equal(frames.size,1);
    chart.setPlan({objects:[]});assert.equal(replacements,1);drain();assert.equal(replacements,1);assert.equal(frames.size,0);
    assert.equal(Number(walk(chart.svg).find(node=>node.attrs['data-crosshair']==='vertical').attrs.x1),320);
  }));
}
