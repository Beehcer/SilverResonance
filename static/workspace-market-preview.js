(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceMarketPreview=api;}(globalThis,function(){
  'use strict';
  const PERIODS=['day','m30','m3'],MAX_BARS=240,MAX_ITEMS=16,TTL=86400000;
  function prepare(market,now=Date.now()){
    if(!market||typeof market.symbol!=='string'||!market.symbol||!PERIODS.every(p=>Array.isArray(market.periods?.[p])&&market.periods[p].length))return null;
    const periods={};
    for(const p of PERIODS){
      const rows=market.periods[p].slice(-MAX_BARS);let previous=-Infinity;
      for(const row of rows){const stamp=Date.parse(row?.dt),prices=['open','high','low','close'].map(k=>row?.[k]);if(!Number.isFinite(stamp)||stamp<=previous||prices.some(v=>!Number.isFinite(v)||v<=0)||row.low>Math.min(row.open,row.close)||row.high<Math.max(row.open,row.close))return null;previous=stamp;}
      periods[p]=rows.map(row=>({...row}));
    }
    return{symbol:market.symbol,saved_at:now,market:{symbol:market.symbol,name:market.name,price:market.price,tick:market.tick,source:market.source,as_of:market.as_of,quote_at:market.quote_at,period_counts:{...(market.period_counts||{})},periods,fresh:false,partial:true,client_cached:true,feed_status:'preview',history_token:null,incremental:false,preview_saved_at:now}};
  }
  function valid(item,symbol,now){return item?.symbol===symbol&&item.market?.symbol===symbol&&Number.isFinite(item.saved_at)&&now-item.saved_at>=0&&now-item.saved_at<TTL&&prepare(item.market,item.saved_at);}
  function disk(indexedDB){
    let opened;
    const open=()=>opened||(opened=new Promise((resolve,reject)=>{
      const req=indexedDB.open('resonance-market-preview-v1',1);
      req.onupgradeneeded=()=>{const store=req.result.createObjectStore('markets',{keyPath:'symbol'});store.createIndex('saved_at','saved_at');};
      req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);req.onblocked=()=>reject(new Error('Preview cache unavailable'));
    }));
    return{
      async get(symbol){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('markets','readonly'),req=tx.objectStore('markets').get(symbol);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});},
      async put(item){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('markets','readwrite'),store=tx.objectStore('markets');store.put(item);const keys=[],request=store.index('saved_at').openKeyCursor();request.onsuccess=()=>{const cursor=request.result;if(cursor){keys.push({symbol:cursor.primaryKey,time:cursor.key});cursor.continue();}else for(let i=0;i<keys.length;i++)if(i<keys.length-MAX_ITEMS||keys[i].time<item.saved_at-TTL)store.delete(keys[i].symbol);};tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});}
    };
  }
  class Store{
    constructor({storage,now=()=>Date.now(),indexedDB=globalThis.indexedDB}={}){this.storage=storage||(indexedDB?disk(indexedDB):null);this.now=now;this.memory=new Map();this.writes=new Map();}
    remember(item){this.memory.delete(item.symbol);this.memory.set(item.symbol,item);while(this.memory.size>MAX_ITEMS)this.memory.delete(this.memory.keys().next().value);}
    async get(symbol){
      const cached=this.memory.get(symbol);if(valid(cached,symbol,this.now()))return prepare(cached.market,cached.saved_at).market;
      try{const item=await this.storage?.get(symbol),newer=this.memory.get(symbol);if(valid(newer,symbol,this.now()))return prepare(newer.market,newer.saved_at).market;if(valid(item,symbol,this.now())){this.remember(item);return prepare(item.market,item.saved_at).market;}}catch{/* Preview storage is optional and never blocks server loading. */}
      return null;
    }
    put(market){
      // Never renew the age of an offline or disk preview by writing it back.
      if(market?.partial||market?.client_cached||market?.unavailable||market?.fresh!==true&&market?.source!=='mock'||['loading','disconnected','stale'].includes(market?.feed_status))return;
      const now=this.now(),previous=this.memory.get(market?.symbol);if(previous&&now-previous.saved_at<30000)return;
      const item=prepare(market,now);if(!item)return;this.remember(item);
      if(!this.storage)return;
      const earlier=this.writes.get(item.symbol)||Promise.resolve(),write=earlier.catch(()=>{}).then(()=>this.storage.put(item)).catch(()=>{});this.writes.set(item.symbol,write);void write.finally(()=>{if(this.writes.get(item.symbol)===write)this.writes.delete(item.symbol);});
    }
  }
  return{Store,prepare,TTL,MAX_BARS,MAX_ITEMS};
}));
