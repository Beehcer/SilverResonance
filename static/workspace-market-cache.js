(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceMarketCache=api;}(globalThis,function(){
  'use strict';
  const PERIODS=['day','m30','m3'],LIMITS={day:180,m30:6000,m3:10000},MAX_APPEND=64;
  const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const clone=value=>{
    if(Array.isArray(value))return value.map(clone);
    if(record(value))return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,clone(item)]));
    return value;
  };
  // Only values received from the wire are copied. Previously accepted bars
  // are already deeply immutable, so a tick can share their entire prefix.
  const immutable=value=>{
    if(Array.isArray(value))return Object.freeze(value.map(immutable));
    if(record(value))return Object.freeze(Object.fromEntries(Object.entries(value).map(([key,item])=>[key,immutable(item)])));
    return value;
  };
  const equal=(left,right)=>{
    if(left===right)return true;
    if(Array.isArray(left)||Array.isArray(right))return Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&left.every((value,index)=>equal(value,right[index]));
    if(!record(left)||!record(right))return false;
    const keys=Object.keys(left);return keys.length===Object.keys(right).length&&keys.every(key=>Object.hasOwn(right,key)&&equal(left[key],right[key]));
  };
  const appendProtocol=market=>Object.hasOwn(market,'base_history_token')||Object.hasOwn(market,'period_drops');
  const coordinateKeys=['candle_basis','anchor_reference_id','anchor_reference'];
  const basisAt=(market,period)=>market?.candle_basis?.[period]||'native_v1';
  const sameCandleBasis=(left,right)=>PERIODS.every(period=>basisAt(left,period)===basisAt(right,period));
  const basisDowngrade=(previous,next)=>PERIODS.some(period=>basisAt(previous,period)!=='native_v1'&&basisAt(next,period)==='native_v1');
  const protectedBasis=market=>Object.fromEntries(PERIODS.filter(period=>basisAt(market,period)!=='native_v1').map(period=>[period,basisAt(market,period)]));
  const referenceId=market=>market?.anchor_reference_id??null;
  const stamp=value=>Number.isSafeInteger(value)&&value>=0&&value<=253402300799000;
  function coordinateError(market,{allowReferenceOmission=false}={}){
    const basis=market.candle_basis,reference=market.anchor_reference,id=referenceId(market);
    if(basis!==undefined&&(!record(basis)||Object.entries(basis).some(([key,value])=>!PERIODS.includes(key)||typeof value!=='string'||!value.length||value.length>80)))return'candle_basis';
    const transformed=PERIODS.filter(period=>basisAt(market,period)!=='native_v1');
    if(id!==null&&(typeof id!=='string'||!id.length||id.length>128))return'anchor_reference_id';
    if(reference===undefined){
      if(id!==null&&!allowReferenceOmission||transformed.length&&id===null)return'anchor_reference_missing';
      return null;
    }
    if(id===null||!record(reference)||!Object.keys(reference).length)return'anchor_reference_missing';
    for(const [period,item]of Object.entries(reference)){
      if(!PERIODS.includes(period)||!record(item)||item.basis!=='native_v1'||item.target_basis!==basisAt(market,period)||item.target_basis==='native_v1'||!stamp(item.target_base_time))return'anchor_reference';
      const times=item.source_times;if(!Array.isArray(times)||!times.length||times.length>10000)return'anchor_reference';
      let previous=-1;for(const value of times){if(!stamp(value)||value<=previous)return'anchor_reference';previous=value;}
    }
    if(transformed.some(period=>!Object.hasOwn(reference,period)))return'anchor_reference_missing';
    return null;
  }
  const copyCoordinates=market=>Object.fromEntries(coordinateKeys.filter(key=>Object.hasOwn(market,key)).map(key=>[key,clone(market[key])]));
  function validBars(rows){
    let previous=-Infinity;
    for(const row of rows){
      if(!record(row)||typeof row.dt!=='string')return false;
      const stamp=Date.parse(row.dt),prices=['open','high','low','close'].map(key=>row[key]);
      if(!Number.isFinite(stamp)||stamp<=previous||prices.some(value=>typeof value!=='number'||!Number.isFinite(value)||value<=0))return false;
      if(!(row.low<=Math.min(row.open,row.close)&&Math.max(row.open,row.close)<=row.high))return false;
      previous=stamp;
    }
    return true;
  }
  function validate(market){
    if(!record(market)||typeof market.history_token!=='string'||!market.history_token.length||typeof market.incremental!=='boolean')return'history_metadata';
    if(!record(market.periods)||!record(market.period_counts))return'period_metadata';
    const coordinates=coordinateError(market,{allowReferenceOmission:market.incremental});if(coordinates)return coordinates;
    const append=market.incremental&&appendProtocol(market);
    if(append&&(typeof market.base_history_token!=='string'||!market.base_history_token.length||!record(market.period_drops)))return'append_metadata';
    for(const period of PERIODS){
      const rows=market.periods[period],count=market.period_counts[period];
      if(!Number.isInteger(count)||count<0||count>LIMITS[period]||!Array.isArray(rows))return'period_count';
      if(append){
        const drop=market.period_drops[period];
        if(!Number.isInteger(drop)||drop<0||drop>MAX_APPEND||!rows.length||rows.length>MAX_APPEND+2)return'period_count';
      }else if(rows.length!==(market.incremental?Math.min(2,count):count))return'period_count';
      if(!validBars(rows))return'invalid_bars';
    }
    return null;
  }
  class Store{
    constructor({limit=32}={}){
      if(!Number.isInteger(limit)||limit<1||limit>32)throw new RangeError('Market cache limit must be 1–32.');
      this.limit=limit;this.entries=new Map();this.sequence=0;this.pinned=null;this.coordinateBases=new Map();
    }
    touch(symbol,create=false){
      let entry=this.entries.get(symbol);
      if(!entry&&create){entry={market:null,historyValid:false,latest:0,accepted:0};}
      if(entry){this.entries.delete(symbol);this.entries.set(symbol,entry);while(this.entries.size>this.limit){const oldest=[...this.entries.keys()].find(key=>key!==this.pinned);this.entries.delete(oldest);}}
      return entry;
    }
    pin(symbol){
      if(symbol===null||symbol===undefined){this.pinned=null;return;}
      if(typeof symbol!=='string'||!symbol.length)throw new TypeError('A contract symbol is required.');
      this.pinned=symbol;this.touch(symbol,true);
    }
    begin(symbol){
      if(typeof symbol!=='string'||!symbol.length)throw new TypeError('A contract symbol is required.');
      const entry=this.touch(symbol,true);entry.latest=++this.sequence;return entry.latest;
    }
    get(symbol){const entry=this.touch(symbol);return entry?.market?clone(entry.market):null;}
    // For rendering only: recursively frozen, stable until a later accepted
    // response. get() remains the independent, mutable snapshot API.
    view(symbol){return this.touch(symbol)?.market||null;}
    ready(symbol){const market=this.entries.get(symbol)?.market;return Boolean(market&&PERIODS.every(period=>market.periods[period].length));}
    history(symbol){const entry=this.touch(symbol);return entry?.historyValid?entry.market.history_token:null;}
    clear(symbol){if(symbol===undefined){this.entries.clear();this.coordinateBases.clear();this.pinned=null;}else{this.entries.delete(symbol);this.coordinateBases.delete(symbol);if(this.pinned===symbol)this.pinned=null;}}
    reject(entry,reason){
      // Retain the last complete picture, but stop requesting deltas until a
      // verified full response arrives. No rejected tail/metadata is applied.
      if(entry)entry.historyValid=false;
      return{accepted:false,retryFull:true,reason};
    }
    accept(symbol,market,requestSequence,{readOnly=false}={}){
      const ticket=requestSequence===undefined?this.begin(symbol):requestSequence,entry=this.entries.get(symbol);
      // An evicted request, an older response, or a repeated acknowledgement
      // cannot resurrect stale history after a later request or process reset.
      if(!entry||ticket!==entry.latest||ticket<=entry.accepted)return{accepted:false,ignored:true,reason:'superseded'};
      if(!record(market)||market.symbol!==symbol)return this.reject(entry,'symbol_mismatch');
      const invalid=validate(market);if(invalid)return this.reject(entry,invalid);
      // Keep only a tiny basis ledger when large histories are evicted. LRU
      // pressure must not make an obsolete native response safe again.
      if(basisDowngrade({candle_basis:this.coordinateBases.get(symbol)},market))return this.reject(entry,'candle_basis_downgrade');
      let periods;
      if(market.incremental){
        const base=entry.market;
        if(!base||!entry.historyValid)return this.reject(entry,'missing_full_history');
        if(!sameCandleBasis(base,market))return this.reject(entry,'candle_basis_mismatch');
        if(referenceId(base)!==referenceId(market)||Object.hasOwn(market,'anchor_reference')&&!equal(base.anchor_reference,market.anchor_reference))return this.reject(entry,'anchor_reference_mismatch');
        const append=appendProtocol(market);
        if(base.history_token!==(append?market.base_history_token:market.history_token))return this.reject(entry,'history_mismatch');
        periods={};
        for(const period of PERIODS){
          const old=base.periods[period],tail=market.periods[period],count=market.period_counts[period];
          const overlap=Math.min(2,old.length),offset=old.length-overlap,drop=append?market.period_drops[period]:0,added=tail.length-overlap;
          if(added<0||added>MAX_APPEND||drop>added||drop>offset||count!==old.length-drop+added)return this.reject(entry,'period_count');
          if(!append&&added!==0)return this.reject(entry,'period_count');
          if(tail.slice(0,overlap).some((bar,index)=>bar.dt!==old[offset+index].dt))return this.reject(entry,'tail_mismatch');
          const nextTail=tail.map((bar,index)=>index<overlap&&equal(old[offset+index],bar)?old[offset+index]:immutable(bar));
          const unchanged=!drop&&!added&&nextTail.every((bar,index)=>bar===old[offset+index]);
          periods[period]=unchanged?old:Object.freeze(old.slice(drop,offset).concat(nextTail));
        }
        periods=Object.freeze(periods);
      }else periods=immutable(market.periods);
      // Metadata comes entirely from the newest response: false, zero, null,
      // unavailable status and freshness details must never inherit older truth.
      const {periods:wirePeriods,anchor_reference:wireReference,...metadata}=market;
      // The reference is frozen at the basis switch, not regenerated every
      // tick. A delta carries its ID only and shares the verified full value.
      const reference=market.incremental?entry.market.anchor_reference:wireReference;
      const next=Object.freeze({...immutable(metadata),...(reference===undefined?{}:{anchor_reference:market.incremental?reference:immutable(reference)}),periods});
      entry.market=next;entry.historyValid=PERIODS.every(period=>next.periods[period].length>0);entry.accepted=ticket;
      const basis=protectedBasis(next);if(Object.keys(basis).length)this.coordinateBases.set(symbol,basis);
      this.touch(symbol);
      return{accepted:true,market:readOnly?next:clone(next),ready:entry.historyValid};
    }
  }
  return{Store,PERIODS,LIMITS,coordinateError,copyCoordinates,sameCandleBasis,basisDowngrade,protectedBasis,referenceId};
}));
