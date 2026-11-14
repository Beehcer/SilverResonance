(function(root,factory){const api=factory(typeof module==='object'&&module.exports?require('./workspace-market-cache.js'):root.WorkspaceMarketCache);if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceHistoryCache=api;}(globalThis,function(MarketCache){
  'use strict';
  const PERIODS=['day','m30','m3'],HASH=/^[a-f0-9]{64}$/,MAX_DISK_BYTES=128*1024*1024,MAX_MEMORY_BYTES=24*1024*1024,MAX_CHUNKS=2048;
  const copy=value=>JSON.parse(JSON.stringify(value)),record=value=>value&&typeof value==='object'&&!Array.isArray(value);
  function sixMonthsAgo(now=Date.now()){
    const date=new Date(now+8*3600000),year=date.getUTCFullYear(),month=date.getUTCMonth()-6;
    const day=Math.min(date.getUTCDate(),new Date(Date.UTC(year,month+1,0)).getUTCDate());
    return Date.UTC(year,month,day,date.getUTCHours(),date.getUTCMinutes(),date.getUTCSeconds(),date.getUTCMilliseconds())-8*3600000;
  }
  function disk(indexedDB){
    let opening;
    const open=()=>opening||(opening=new Promise((resolve,reject)=>{
      const request=indexedDB.open('resonance-history-blocks-v1',1);
      request.onupgradeneeded=()=>{const store=request.result.createObjectStore('blocks',{keyPath:'sha256'});store.createIndex('saved_at','saved_at');};
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);request.onblocked=()=>reject(new Error('History cache blocked'));
    }));
    return{
      async get(key){const db=await open();return new Promise((resolve,reject)=>{const request=db.transaction('blocks','readonly').objectStore('blocks').get(key);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});},
      async put(item){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('blocks','readwrite');tx.objectStore('blocks').put(item);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});},
      async putMany(items){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('blocks','readwrite'),store=tx.objectStore('blocks');tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);try{for(const item of items)store.put(item);}catch(error){tx.abort();reject(error);}});},
      async prune(now){const db=await open();return new Promise((resolve,reject)=>{const tx=db.transaction('blocks','readwrite'),store=tx.objectStore('blocks'),request=store.index('saved_at').openCursor(null,'prev');let bytes=0,count=0;request.onsuccess=()=>{const cursor=request.result;if(!cursor)return;const item=cursor.value;bytes+=item.bytes||0;count++;if(item.last_time<sixMonthsAgo(now)||count>MAX_CHUNKS||bytes>MAX_DISK_BYTES)cursor.delete();cursor.continue();};tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});}
    };
  }
  async function digest(text,crypto=globalThis.crypto){
    if(!crypto?.subtle)throw new Error('History integrity verification unavailable');
    const bytes=new TextEncoder().encode(text),hash=await crypto.subtle.digest('SHA-256',bytes);
    return Array.from(new Uint8Array(hash),value=>value.toString(16).padStart(2,'0')).join('');
  }
  function descriptor(value){
    if(!record(value)||!HASH.test(value.sha256)||!Number.isInteger(value.count)||value.count<1||value.count>10000||typeof value.first!=='string'||typeof value.last!=='string'||!Number.isFinite(Date.parse(value.first))||!Number.isFinite(Date.parse(value.last))||Date.parse(value.first)>Date.parse(value.last))throw new Error('Invalid history descriptor');
    if(value.url!=='/api/workspace/market/history/'+value.sha256+'.json')throw new Error('Invalid history fallback URL');
    return value;
  }
  function publicUrl(value,hash){
    if(!value)return null;
    const url=new URL(value);
    if(url.protocol!=='https:'||url.hostname!=='silver-resonance.oss-cn-hangzhou.aliyuncs.com'||url.port||url.username||url.password||url.search||url.hash||url.pathname!=='/history/v1/blocks/'+hash+'.json')throw new Error('Invalid public history URL');
    return url.href;
  }
  function validateBlock(value,desc,market,period){
    const basis=market.candle_basis?.[period]||'native_v1',id=market.anchor_reference_id??null;
    if(!record(value)||value.schema!==1||value.symbol!==market.symbol||value.period!==period||value.candle_basis!==basis||value.anchor_reference_id!==id||!Array.isArray(value.bars)||value.bars.length!==desc.count||value.bars[0]?.dt!==desc.first||value.bars.at(-1)?.dt!==desc.last)throw new Error('History block does not match snapshot');
    let previous=-Infinity;
    for(const bar of value.bars){const time=Date.parse(bar?.dt),prices=['open','high','low','close'].map(key=>bar?.[key]);if(!Number.isFinite(time)||time<=previous||prices.some(p=>!Number.isFinite(p)||p<=0)||bar.low>Math.min(bar.open,bar.close)||bar.high<Math.max(bar.open,bar.close))throw new Error('Invalid history candle');previous=time;}
    return value.bars;
  }
  class Blocks{
    constructor({storage,indexedDB=globalThis.indexedDB,crypto=globalThis.crypto,now=()=>Date.now(),memoryBytes=MAX_MEMORY_BYTES}={}){this.storage=storage||(indexedDB?disk(indexedDB):null);this.crypto=crypto;this.now=now;this.memory=new Map();this.memoryBytes=memoryBytes;this.bytes=0;this.requests=new Map();this.writes=new Set();this.writeBatch=null;this.writeTimer=null;this.writing=false;this.activeAssemblies=0;this.prunePending=false;this.lastPrune=0;this.stats={memoryHits:0,diskHits:0,downloads:0,fallbacks:0};}
    remember(item){const old=this.memory.get(item.sha256);if(old)this.bytes-=old.bytes;this.memory.delete(item.sha256);this.memory.set(item.sha256,item);this.bytes+=item.bytes;while(this.bytes>this.memoryBytes&&this.memory.size){const [key,first]=this.memory.entries().next().value;this.memory.delete(key);this.bytes-=first.bytes;}}
    async save(item){
      if(!this.storage)return;
      // Backpressure only when a multi-pane restore queues more than 24 MiB.
      // Bound both pending and in-flight batches without losing verified data.
      while(this.writeBatch&&this.writeBatch.bytes+item.bytes-(this.writeBatch.items.get(item.sha256)?.bytes||0)>MAX_MEMORY_BYTES){const pending=this.writeBatch;pending.urgent=true;this.scheduleWrites();await pending.promise;}
      if(!this.writeBatch){let resolve;const promise=new Promise(done=>{resolve=done;});this.writeBatch={items:new Map(),bytes:0,urgent:false,promise,resolve};this.writes.add(promise);void promise.finally(()=>this.writes.delete(promise));}
      this.writeBatch.bytes+=item.bytes-(this.writeBatch.items.get(item.sha256)?.bytes||0);this.writeBatch.items.set(item.sha256,item);this.scheduleWrites();
    }
    scheduleWrites(){
      if((this.activeAssemblies&&!this.writeBatch?.urgent)||this.writing||this.writeTimer!==null||(!this.writeBatch&&!this.prunePending))return;
      // IndexedDB writes lock the entire store, including reads of other keys.
      // Let every chart finish reading first; commit verified blocks as a batch
      // in the next task, after the restored market can reach the renderer.
      this.writeTimer=setTimeout(()=>{this.writeTimer=null;void this.flushWrites();},0);
    }
    async flushWrites(){
      if((this.activeAssemblies&&!this.writeBatch?.urgent)||this.writing)return;
      const batch=this.writeBatch;this.writeBatch=null;this.writing=true;
      try{
        if(batch){const items=[...batch.items.values()];if(this.storage.putMany)await this.storage.putMany(items);else await Promise.all(items.map(item=>this.storage.put(item)));}
        else if(this.prunePending){this.prunePending=false;await this.storage.prune(this.now());}
      }catch{/* Optional persistence cannot block a validated chart. */}
      finally{batch?.resolve();this.writing=false;this.scheduleWrites();}
    }
    async block(desc,market,period,read){
      descriptor(desc);const key=desc.sha256;
      const parse=async text=>{if(typeof text!=='string'||await digest(text,this.crypto)!==key)throw new Error('History checksum mismatch');const value=JSON.parse(text);validateBlock(value,desc,market,period);return value;};
      let item=this.memory.get(key);
      if(item){validateBlock(item.value,desc,market,period);this.remember(item);this.stats.memoryHits++;return item.value.bars;}
      let task=this.requests.get(key);
      if(!task){
        task=(async()=>{
          try{const saved=await this.storage?.get(key);if(saved&&saved.sha256===key&&saved.last_time>=sixMonthsAgo(this.now())){const value=await parse(saved.text);item={...saved,value};this.remember(item);this.stats.diskHits++;return item;}}catch{/* Missing, corrupt or evicted local data is replaced from the authoritative block. */}
          let text,value;const remote=publicUrl(desc.oss_url,key);
          if(remote){try{text=await read(remote,{timeout:2500,public:true});value=await parse(text);}catch{this.stats.fallbacks++;}}
          if(!value){text=await read(desc.url,{timeout:8000,public:false});value=await parse(text);}
          this.stats.downloads++;const saved={sha256:key,text,bytes:new TextEncoder().encode(text).byteLength,saved_at:this.now(),last_time:Date.parse(desc.last)};item={...saved,value};this.remember(item);await this.save(saved);return item;
        })();
        this.requests.set(key,task);void task.finally(()=>{if(this.requests.get(key)===task)this.requests.delete(key);}).catch(()=>{});
      }
      item=await task;validateBlock(item.value,desc,market,period);return item.value.bars;
    }
    async assemble(bundle,read,onProgress=()=>{},signal){
      if(!record(bundle)||bundle.schema!==1||typeof bundle.snapshot_id!=='string'||!bundle.snapshot_id||!record(bundle.market)||bundle.market.incremental!==false||!record(bundle.history)||!record(bundle.history_counts))throw new Error('Invalid history bootstrap');
      const market=bundle.market;if(MarketCache.coordinateError(market))throw new Error('Invalid history coordinate basis');
      const periods={},jobs=[];
      for(const period of PERIODS){
        const chunks=bundle.history[period],tail=market.periods?.[period],count=bundle.history_counts[period];
        if(!Array.isArray(chunks)||chunks.length>128||!Array.isArray(tail)||tail.length>2||!Number.isInteger(count)||count<0||!Number.isInteger(market.period_counts?.[period])||market.period_counts[period]>MarketCache.LIMITS[period]||count+tail.length!==market.period_counts[period])throw new Error('Invalid history count');
        if(chunks.reduce((sum,item)=>sum+descriptor(item).count,0)!==count)throw new Error('History chunks are incomplete');
        periods[period]=new Array(chunks.length);
        chunks.forEach((desc,index)=>jobs.push(async()=>{periods[period][index]=await this.block(desc,market,period,read);onProgress();}));
      }
      let released=false;this.activeAssemblies++;
      const release=()=>{if(released)return;released=true;this.activeAssemblies--;signal?.removeEventListener('abort',release);this.scheduleWrites();};
      signal?.addEventListener('abort',release,{once:true});
      try{
        if(signal?.aborted)throw new Error('History restore cancelled');
        let at=0;await Promise.all(Array.from({length:Math.min(4,jobs.length)},async()=>{while(at<jobs.length){if(signal?.aborted)throw new Error('History restore cancelled');const job=jobs[at++];await job();}}));
        if(signal?.aborted)throw new Error('History restore cancelled');
        for(const period of PERIODS){
          const rows=periods[period].flat().concat(market.periods[period]);let previous=-Infinity;
          for(const row of rows){const time=Date.parse(row?.dt);if(!Number.isFinite(time)||time<=previous)throw new Error('History boundary mismatch');previous=time;}
          if(rows.length!==market.period_counts[period])throw new Error('History length mismatch');periods[period]=rows;
        }
        if(this.storage?.prune&&this.now()-this.lastPrune>60000){this.lastPrune=this.now();this.prunePending=true;}
        return{...market,periods};
      }finally{release();}
    }
  }
  class Hub{
    constructor({fetch=globalThis.fetch?.bind(globalThis),blocks=new Blocks(),now=()=>Date.now(),bootstrapTimeout=7000,bootstrapMaxTimeout=20000}={}){this.fetch=fetch;this.blocks=blocks;this.now=now;this.bootstrapTimeout=bootstrapTimeout;this.bootstrapMaxTimeout=bootstrapMaxTimeout;this.cache=new MarketCache.Store({limit:32});this.requests=new Map();this.quoteRequest=null;this.quoteResult=null;this.quoteAt=0;this.timings=[];this.stats={bootstrap:0,full:0,incremental:0,quotes:0};}
    async read(path,{timeout=12000,public:remote=false,json=false,signal}={}){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
      const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)controller.abort();
      try{const response=await this.fetch(path,{credentials:remote?'omit':'same-origin',cache:remote?'default':'no-store',headers:remote?{}:{'X-Workspace-Candle-Protocol':'2'},signal:controller.signal});if(!response.ok){const error=new Error('行情服务暂未响应');error.status=response.status;throw error;}return json?await response.json():await response.text();}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
    }
    async full(symbol){
      const query='?symbol='+encodeURIComponent(symbol),started=this.now(),controller=new AbortController();let market,timer,hardTimer,finished=false,rejectTimeout;
      const timeout=new Promise((_,reject)=>{rejectTimeout=reject;});
      const expire=()=>{if(finished)return;finished=true;controller.abort();rejectTimeout(new Error('History restore timed out'));};
      // A valid completed block is progress, including disk/memory hits. Keep
      // useful work instead of discarding 12 finished blocks at a fixed 7s;
      // a separate hard cap still bounds a slow stream that never finishes.
      const progress=()=>{if(finished||controller.signal.aborted)return;clearTimeout(timer);timer=setTimeout(expire,this.bootstrapTimeout);};
      try{this.stats.bootstrap++;progress();hardTimer=setTimeout(expire,this.bootstrapMaxTimeout);const work=(async()=>{const data=await this.read('/api/workspace/market/bootstrap'+query,{json:true,signal:controller.signal});progress();return this.blocks.assemble(data.bootstrap,(path,options)=>{if(controller.signal.aborted)throw new Error('History restore timed out');return this.read(path,{...options,signal:controller.signal});},progress,controller.signal);})();
        market=await Promise.race([work,timeout]);}
      catch(error){finished=true;controller.abort();clearTimeout(timer);clearTimeout(hardTimer);if(error.status===401)throw error;this.stats.full++;market=(await this.read('/api/workspace/market'+query,{json:true})).market;}
      finally{finished=true;clearTimeout(timer);clearTimeout(hardTimer);}
      // A disk/OSS restore never refreshes the server's observation timestamp.
      const age=this.now()-Date.parse(market?.as_of);
      if(market?.fresh===true&&(this.now()-started>15000||!Number.isFinite(age)||age>20000||age < -60000))market={...market,fresh:false,feed_status:'stale',message:'历史已恢复，正在核对最新行情'};
      return market;
    }
    refresh(symbol){
      if(this.requests.has(symbol))return this.requests.get(symbol);
      const started=this.now(),cache=this.cache,ticket=cache.begin(symbol);
      const task=(async()=>{let token=cache.history(symbol);for(let attempt=0;attempt<2;attempt++){
        let market;if(token){this.stats.incremental++;const response=await this.read('/api/workspace/market?symbol='+encodeURIComponent(symbol)+'&history='+encodeURIComponent(token)+'&history_fallback=bootstrap',{json:true});market=response.status==='bootstrap_required'?await this.full(symbol):response.market;}else market=await this.full(symbol);
        const accepted=cache.accept(symbol,market,ticket,{readOnly:true});if(accepted.accepted)return accepted.market;if(accepted.ignored)return cache.view(symbol);token=null;
      }throw new Error('行情缓存未能对齐，请稍后重试');})();
      this.requests.set(symbol,task);void task.finally(()=>{if(this.requests.get(symbol)===task)this.requests.delete(symbol);this.timings.push({symbol,duration_ms:this.now()-started,at:this.now()});if(this.timings.length>100)this.timings.shift();}).catch(()=>{});return task;
    }
    quotes(){
      if(this.quoteRequest)return this.quoteRequest;
      if(this.quoteResult&&this.now()-this.quoteAt<1000)return Promise.resolve(this.quoteResult);
      this.stats.quotes++;const task=this.read('/api/workspace/quotes',{json:true}).then(result=>{this.quoteResult=result;this.quoteAt=this.now();return result;});this.quoteRequest=task;void task.finally(()=>{if(this.quoteRequest===task)this.quoteRequest=null;}).catch(()=>{});return task;
    }
  }
  return{Blocks,Hub,digest,sixMonthsAgo,MAX_DISK_BYTES,MAX_MEMORY_BYTES,MAX_CHUNKS};
}));
