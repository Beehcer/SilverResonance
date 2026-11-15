(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./workspace-market-cache.js'):root.WorkspaceMarketCache);if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceMarketPreview=api;}(globalThis,function(Coordinates){
  'use strict';
  const PERIODS=['day','m30','m3'],MAX_BARS=240,MAX_ITEMS=32,TTL=184*86400000;
  function cutoff(now){const date=new Date(now+8*3600000),year=date.getUTCFullYear(),month=date.getUTCMonth()-6,day=Math.min(date.getUTCDate(),new Date(Date.UTC(year,month+1,0)).getUTCDate());return Date.UTC(year,month,day,date.getUTCHours(),date.getUTCMinutes(),date.getUTCSeconds(),date.getUTCMilliseconds())-8*3600000;}
  function prepare(market,now=Date.now()){
    if(!market||typeof market.symbol!=='string'||!market.symbol||!PERIODS.every(p=>Array.isArray(market.periods?.[p])&&market.periods[p].length))return null;
    if(Coordinates.coordinateError(market))return null;
    const periods={};
    for(const p of PERIODS){
      const rows=market.periods[p].slice(-MAX_BARS).filter(row=>!Number.isFinite(Date.parse(row?.dt))||Date.parse(row.dt)>=cutoff(now));let previous=-Infinity;if(!rows.length)return null;
      for(const row of rows){const stamp=Date.parse(row?.dt),prices=['open','high','low','close'].map(k=>row?.[k]);if(!Number.isFinite(stamp)||stamp<=previous||prices.some(v=>!Number.isFinite(v)||v<=0)||row.low>Math.min(row.open,row.close)||row.high<Math.max(row.open,row.close))return null;previous=stamp;}
      periods[p]=rows.map(row=>({...row}));
    }
    return{symbol:market.symbol,saved_at:now,market:{symbol:market.symbol,name:market.name,price:market.price,tick:market.tick,source:market.source,as_of:market.as_of,quote_at:market.quote_at,period_counts:{...(market.period_counts||{})},...Coordinates.copyCoordinates(market),periods,fresh:false,partial:true,client_cached:true,feed_status:'preview',history_token:null,incremental:false,preview_revision:market.preview_revision,preview_saved_at:now}};
  }
  function valid(item,symbol,now){return item?.symbol===symbol&&item.market?.symbol===symbol&&Number.isFinite(item.saved_at)&&now-item.saved_at>=0&&now-item.saved_at<TTL&&item.saved_at>=cutoff(now)&&prepare(item.market,now);}
  function disk(indexedDB){
    let opened;
    const open=()=>opened||(opened=new Promise((resolve,reject)=>{
      const req=indexedDB.open('resonance-market-preview-v1',1);
      req.onupgradeneeded=()=>{const store=req.result.createObjectStore('markets',{keyPath:'symbol'});store.createIndex('saved_at','saved_at');};
      req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);req.onblocked=()=>reject(new Error('Preview cache unavailable'));
    }));
    return{
      async get(symbol){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('markets','readonly'),req=tx.objectStore('markets').get(symbol);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});},
      async put(item){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('markets','readwrite'),store=tx.objectStore('markets');store.put(item);const keys=[],request=store.index('saved_at').openKeyCursor();request.onsuccess=()=>{const cursor=request.result;if(cursor){keys.push({symbol:cursor.primaryKey,time:cursor.key});cursor.continue();}else for(let i=0;i<keys.length;i++)if(i<keys.length-MAX_ITEMS||keys[i].time<cutoff(item.saved_at))store.delete(keys[i].symbol);};tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});}
    };
  }
  class Store{
    constructor({storage,now=()=>Date.now(),indexedDB=globalThis.indexedDB}={}){this.storage=storage||(indexedDB?disk(indexedDB):null);this.now=now;this.memory=new Map();this.writes=new Map();this.coordinateBases=new Map();}
    remember(item){this.references?.restore(item.market);this.memory.delete(item.symbol);this.memory.set(item.symbol,item);const basis=Coordinates.protectedBasis(item.market);if(Object.keys(basis).length)this.coordinateBases.set(item.symbol,basis);while(this.memory.size>MAX_ITEMS)this.memory.delete(this.memory.keys().next().value);}
    peek(symbol){const item=this.memory.get(symbol);if(!valid(item,symbol,this.now()))return null;const prepared=prepare(item.market,this.now());prepared.market.preview_saved_at=item.saved_at;return prepared.market;}
    async get(symbol){
      const restore=item=>{const prepared=prepare(item.market,this.now());prepared.market.preview_saved_at=item.saved_at;return prepared.market;};
      const cached=this.memory.get(symbol);if(valid(cached,symbol,this.now()))return restore(cached);
      try{const item=await this.storage?.get(symbol),newer=this.memory.get(symbol);if(valid(newer,symbol,this.now()))return restore(newer);if(valid(item,symbol,this.now())&&!Coordinates.basisDowngrade({candle_basis:this.coordinateBases.get(symbol)},item.market)){this.remember(item);return restore(item);}}catch{/* Preview storage is optional and never blocks server loading. */}
      return null;
    }
    put(market){
      // Never renew the age of an offline or disk preview by writing it back.
      const checkedIdle=market?.source==='live'&&market?.connected===true&&market?.feed_status==='idle'&&PERIODS.every(period=>market?.freshness?.[period]?.checked_recent===true);
      if(market?.partial||market?.client_cached||market?.unavailable||market?.fresh!==true&&market?.source!=='mock'&&!checkedIdle||['loading','disconnected','stale'].includes(market?.feed_status))return;
      const now=this.now(),previous=this.memory.get(market?.symbol);
      if(Coordinates.basisDowngrade({candle_basis:this.coordinateBases.get(market?.symbol)},market))return;
      const sameCoordinates=previous&&Coordinates.sameCandleBasis(previous.market,market)&&Coordinates.referenceId(previous.market)===Coordinates.referenceId(market);
      if(sameCoordinates&&now-previous.saved_at<30000)return;
      const item=prepare(market,now);if(!item)return;this.remember(item);
      this.save(item);
    }
    putPreview(market){
      // Only the HTTP preview reader calls this. Stored/prepared previews are
      // never promoted to a checked market or used as a delta-history cursor.
      if(!market||market.partial!==true||market.incremental===true||!['live','mock'].includes(market.source)||market.unavailable||market.client_cached||Coordinates.basisDowngrade({candle_basis:this.coordinateBases.get(market.symbol)},market))return null;
      const now=this.now(),saved=market.preview_saved_at??Date.parse(market.as_of);
      if(!Number.isFinite(saved)||saved>now||saved<cutoff(now))return null;
      const item=prepare(market,saved);if(!item)return null;
      const previous=this.memory.get(item.symbol);
      const priorAt=Date.parse(previous?.market.as_of),nextAt=Date.parse(market.as_of);
      if(previous&&Number.isFinite(priorAt)&&Number.isFinite(nextAt)&&nextAt<priorAt)return this.peek(item.symbol);
      if(previous&&Coordinates.sameCandleBasis(previous.market,market)&&Coordinates.referenceId(previous.market)===Coordinates.referenceId(market)){
        if(PERIODS.some(period=>Date.parse(item.market.periods[period].at(-1).dt)<Date.parse(previous.market.periods[period].at(-1).dt)))return this.peek(item.symbol);
        // Re-fetching identical partial data must not make an old cache young.
        if(JSON.stringify(previous.market.periods)===JSON.stringify(item.market.periods)){
          if(item.market.preview_revision&&item.market.preview_revision!==previous.market.preview_revision){
            const retained={...item,saved_at:previous.saved_at,market:{...item.market,as_of:previous.market.as_of,quote_at:previous.market.quote_at,preview_saved_at:previous.saved_at}};
            this.remember(retained);this.save(retained);
          }
          return this.peek(item.symbol);
        }
      }
      this.remember(item);this.save(item);return this.peek(item.symbol);
    }
    save(item){
      if(!this.storage)return;
      const earlier=this.writes.get(item.symbol)||Promise.resolve(),write=earlier.catch(()=>{}).then(()=>this.storage.put(item)).catch(()=>{});this.writes.set(item.symbol,write);void write.finally(()=>{if(this.writes.get(item.symbol)===write)this.writes.delete(item.symbol);});
    }
  }
  class Hub{
    constructor({store=new Store(),references=new Coordinates.References(),fetch=globalThis.fetch?.bind(globalThis),now=()=>Date.now(),isBusy=()=>false,setTimer=(callback,ms)=>globalThis.setTimeout(callback,ms),clearTimer=timer=>globalThis.clearTimeout(timer),refreshAfter=60000,random=Math.random}={}){
      this.store=store;this.references=references;store.references=references;this.fetch=fetch;this.now=now;this.isBusy=isBusy;this.setTimer=setTimer;this.clearTimer=clearTimer;this.refreshAfter=refreshAfter;this.random=random;this.catalogue=[];this.requests=new Map();this.checked=new Map();this.retryAt=new Map();this.warmed=false;this.timer=null;this.background=null;this.active=new Map();this.stats={requests:0,background:0,aborted:0,restored:0};
    }
    focus(owner,symbol){
      this.active.set(owner,symbol);
      this.promote(this.requests.get(symbol));
      // The one background preview yields its connection immediately when the
      // user selects a contract. Never abort either pane's foreground request.
      if(this.background&&this.background.symbol!==symbol){this.background.controller.abort();this.stats.aborted++;}
    }
    release(owner){this.active.delete(owner);if(!this.active.size)this.stop();}
    promote(entry){if(!entry?.background)return;entry.background=false;if(this.background===entry)this.background=null;this.clearTimer(entry.timer);entry.timer=this.setTimer(()=>entry.controller.abort(),5000);}
    prime(symbols){
      const list=[...new Set((symbols||[]).map(item=>typeof item==='string'?item:item?.symbol).filter(item=>typeof item==='string'&&item))].slice(0,MAX_ITEMS);
      if(JSON.stringify(list)===JSON.stringify(this.catalogue))return;
      this.catalogue=list;
      // Only local reads here; network warming starts after the focused plot.
      let at=0;void Promise.all(Array.from({length:Math.min(2,list.length)},async()=>{while(at<list.length){const symbol=list[at++];if(await this.store.get(symbol))this.stats.restored++;}}));
    }
    refresh(symbol,{background=false}={}){
      if(this.requests.has(symbol)){const entry=this.requests.get(symbol);if(!entry.controller.signal.aborted){if(!background)this.promote(entry);return entry.task;}this.requests.delete(symbol);}
      const controller=new AbortController(),entry={symbol,controller,background,task:null,timer:null};
      if(background)this.background=entry;
      const task=(async()=>{
        entry.timer=this.setTimer(()=>controller.abort(),background?2500:5000);
        try{
          this.stats.requests++;if(background)this.stats.background++;
          const cached=this.store.peek(symbol),reference=this.references.get(symbol);
          const query='&preview=240'+(cached?.preview_revision?'&preview_revision='+encodeURIComponent(cached.preview_revision):'')+(reference?'&anchor_reference='+encodeURIComponent(reference.anchor_reference_id):'');
          const response=await this.fetch('/api/workspace/market?symbol='+encodeURIComponent(symbol)+query,{credentials:'same-origin',cache:'no-store',headers:{'X-Workspace-Candle-Protocol':'2'},signal:controller.signal});
          if(!response.ok){if(response.status===401)this.stop();throw new Error('近期行情暂未响应');}const value=await response.json();
          if(controller.signal.aborted)throw new Error('Preview superseded');
          if(value.status==='preview_unchanged'&&cached?.preview_revision&&value.symbol===symbol&&value.preview_revision===cached.preview_revision){this.checked.set(symbol,this.now());this.retryAt.delete(symbol);return cached;}
          if(value.market?.symbol!==symbol)throw new Error('Preview symbol mismatch');
          const market=this.store.putPreview(this.references.restore(value.market,reference));if(!market)throw new Error('Preview unavailable');
          this.checked.set(symbol,this.now());this.retryAt.delete(symbol);return market;
        }catch(error){this.retryAt.set(symbol,this.now()+1500+this.random()*1500);throw error;}
        finally{this.clearTimer(entry.timer);}
      })();
      entry.task=task;this.requests.set(symbol,entry);
      void task.finally(()=>{if(this.requests.get(symbol)===entry)this.requests.delete(symbol);if(this.background===entry)this.background=null;this.schedule();}).catch(()=>{});
      return task;
    }
    warm(){this.warmed=true;this.schedule();}
    schedule(){if(!this.warmed||this.timer!==null)return;this.timer=this.setTimer(()=>{this.timer=null;void this.tick();},250);}
    async tick(){
      if(!this.warmed)return;
      if(this.isBusy()||this.requests.size){this.schedule();return;}
      const active=new Set(this.active.values()),now=this.now();
      const symbol=this.catalogue.find(symbol=>!active.has(symbol)&&now>=(this.retryAt.get(symbol)||0)&&now-(this.checked.get(symbol)??-Infinity)>=this.refreshAfter);
      if(!symbol){this.timer=this.setTimer(()=>{this.timer=null;void this.tick();},5000);return;}
      try{await this.refresh(symbol,{background:true});}catch{/* A failed warm read never changes the active pane. */}
    }
    stop(){this.warmed=false;if(this.timer!==null)this.clearTimer(this.timer);this.timer=null;this.background?.controller.abort();}
  }
  return{Store,Hub,prepare,TTL,MAX_BARS,MAX_ITEMS,cutoff};
}));
