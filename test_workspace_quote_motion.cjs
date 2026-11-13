'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/workspace.js','utf8');
function node(){const classes=new Set();return{dataset:{},style:{},classes,classList:{add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name))}};}
function harness(){let now=0;const stamp=node(),state={},context=vm.createContext({state,$:()=>stamp,Map,Date:{now:()=>now}});vm.runInContext(source.slice(source.indexOf('  function renderPriceMotion('),source.indexOf('  function renderMarket(')),context);return{context,state,stamp,advance:ms=>now+=ms};}
const live=(symbol='SHFE.ag2702',stamp='2026-11-13T23:55:37+08:00')=>({symbol,as_of:stamp,source:'live',connected:true,fresh:true,feed_status:'live'});

test('price animation starts only after an actual same-contract live change, not initial display',()=>{
  const h=harness(),first=node();h.context.renderPriceMotion(first,12000,'ag',true);assert.equal(first.classes.size,0);
  h.context.renderPriceMotion(first,12000,'ag',true);assert.equal(first.classes.size,0);
  h.context.renderPriceMotion(first,12001,'ag',true);assert.ok(first.classes.has('price-tick-up'));
  h.context.renderPriceMotion(first,11999,'ag',true);assert.ok(first.classes.has('price-tick-down'));assert.equal(first.classes.has('price-tick-up'),false);
  const other=node();h.context.renderPriceMotion(other,2500,'ma',true);assert.equal(other.classes.size,0);
});

test('a rebuilt row resumes the existing short animation instead of restarting its clock',()=>{
  const h=harness();h.context.renderPriceMotion(node(),100,'list:ag',true);h.context.renderPriceMotion(node(),101,'list:ag',true);h.advance(300);
  const rebuilt=node();h.context.renderPriceMotion(rebuilt,101,'list:ag',true);assert.ok(rebuilt.classes.has('price-tick-up'));assert.equal(rebuilt.style.animationDelay,'-300ms');
  h.advance(351);const expired=node();h.context.renderPriceMotion(expired,101,'list:ag',true);assert.equal(expired.classes.size,0);
});

test('cached, mock, unavailable and nonnumeric prices cannot impersonate a live price change',()=>{
  const h=harness(),target=node();h.context.renderPriceMotion(target,100,'ag',true);h.context.renderPriceMotion(target,102,'ag',false);assert.equal(target.classes.size,0);
  h.context.renderPriceMotion(target,103,'ag',true);assert.equal(target.classes.size,0);
  h.context.renderPriceMotion(target,NaN,'ag',true);assert.equal(target.classes.size,0);
});

test('live silver and idle methanol have independent feed indications and no shared clock pulse',()=>{
  const silver=harness(),methanol=harness(),active=node(),idle=node();silver.context.renderFeedMotion(active,live());methanol.context.renderFeedMotion(idle,{...live('CZCE.MA701'),feed_status:'idle',fresh:false});
  assert.equal(silver.stamp.dataset.feedState,'live');assert.ok(active.classes.has('live-update-pulse'));
  assert.equal(methanol.stamp.dataset.feedState,'idle');assert.equal(idle.classes.size,0);
});

test('preview, disconnected and stale responses never pulse and old successful checks expire',()=>{
  for(const alteration of [{partial:true},{client_cached:true},{unavailable:true},{source:'mock'},{connected:false},{fresh:false},{feed_status:'stale'},{feed_status:'idle'}]){
    const h=harness(),indicator=node();h.context.renderFeedMotion(indicator,{...live(),...alteration});assert.equal(indicator.classes.size,0,JSON.stringify(alteration));
  }
  const h=harness();h.context.renderFeedMotion(node(),live());h.advance(1000);const repeated=node();h.context.renderFeedMotion(repeated,live());assert.equal(repeated.classes.size,0);
  h.advance(20000);h.context.renderFeedMotion(node(),live());assert.equal(h.stamp.dataset.feedState,'stale');
  const refreshed=node();h.context.renderFeedMotion(refreshed,live('SHFE.ag2702','2026-11-13T23:56:00+08:00'));assert.ok(refreshed.classes.has('live-update-pulse'));
});

test('the top market notice pulses once per live check while warnings and unchanged rerenders do not',()=>{
  const h=harness(),banner={...node(),textContent:'',replacements:0,replaceChildren(child){this.child=child;this.textContent=child.textContent;this.replacements++;}};
  h.context.$=id=>id==='notice'?banner:h.stamp;h.context.el=(tag,text)=>({...node(),textContent:text});
  const start=source.indexOf('  function notice('),end=source.indexOf('\n  function expandPanel(',start);vm.runInContext(source.slice(start,end),h.context);
  h.state.market={...live(),message:'真实行情更新中'};h.context.notice(h.state.market.message);assert.ok(banner.child.classes.has('live-update-pulse'));assert.equal(banner.dataset.feedState,'live');
  h.context.notice(h.state.market.message);assert.equal(banner.replacements,1,'same check does not recreate/restart the pulse');
  h.context.notice('修改尚未保存');assert.equal(banner.textContent,'修改尚未保存');assert.equal(banner.dataset.marketPulse,undefined);assert.equal(banner.dataset.feedState,undefined);
  h.state.market={...live('CZCE.MA701'),message:'暂无新报价',feed_status:'idle',fresh:false};h.context.notice(h.state.market.message);assert.equal(banner.dataset.feedState,'idle');assert.equal(banner.child.classes.size,0);
});
