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
    constructor({limit=16}={}){
      if(!Number.isInteger(limit)||limit<1||limit>16)throw new RangeError('Market cache limit must be 1–16.');
      this.limit=limit;this.entries=new Map();this.sequence=0;this.pinned=null;
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
    clear(symbol){if(symbol===undefined){this.entries.clear();this.pinned=null;}else{this.entries.delete(symbol);if(this.pinned===symbol)this.pinned=null;}}
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
      let periods;
      if(market.incremental){
        const base=entry.market;
        if(!base||!entry.historyValid)return this.reject(entry,'missing_full_history');
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
      const {periods:wirePeriods,...metadata}=market,next=Object.freeze({...immutable(metadata),periods});
      entry.market=next;entry.historyValid=PERIODS.every(period=>next.periods[period].length>0);entry.accepted=ticket;
      this.touch(symbol);
      return{accepted:true,market:readOnly?next:clone(next),ready:entry.historyValid};
    }
  }
  return{Store,PERIODS,LIMITS};
}));
