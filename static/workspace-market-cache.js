(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceMarketCache=api;}(globalThis,function(){
  'use strict';
  const PERIODS=['day','m30','m3'],LIMITS={day:180,m30:6000,m3:10000};
  const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const clone=value=>{
    if(Array.isArray(value))return value.map(clone);
    if(record(value))return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,clone(item)]));
    return value;
  };
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
    for(const period of PERIODS){
      const rows=market.periods[period],count=market.period_counts[period];
      if(!Number.isInteger(count)||count<0||count>LIMITS[period]||!Array.isArray(rows))return'period_count';
      if(rows.length!==(market.incremental?Math.min(2,count):count))return'period_count';
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
    ready(symbol){const market=this.entries.get(symbol)?.market;return Boolean(market&&PERIODS.every(period=>market.periods[period].length));}
    history(symbol){const entry=this.touch(symbol);return entry?.historyValid?entry.market.history_token:null;}
    clear(symbol){if(symbol===undefined){this.entries.clear();this.pinned=null;}else{this.entries.delete(symbol);if(this.pinned===symbol)this.pinned=null;}}
    reject(entry,reason){
      // Retain the last complete picture, but stop requesting deltas until a
      // verified full response arrives. No rejected tail/metadata is applied.
      if(entry)entry.historyValid=false;
      return{accepted:false,retryFull:true,reason};
    }
    accept(symbol,market,requestSequence){
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
        if(base.history_token!==market.history_token)return this.reject(entry,'history_mismatch');
        periods={};
        for(const period of PERIODS){
          const old=base.periods[period],tail=market.periods[period],count=market.period_counts[period];
          if(count!==old.length)return this.reject(entry,'period_count');
          const offset=count-tail.length;
          if(tail.some((bar,index)=>bar.dt!==old[offset+index].dt))return this.reject(entry,'tail_mismatch');
          periods[period]=old.slice(0,offset).concat(tail);
        }
      }else periods=market.periods;
      // Metadata comes entirely from the newest response: false, zero, null,
      // unavailable status and freshness details must never inherit older truth.
      const next=clone({...market,periods});
      entry.market=next;entry.historyValid=PERIODS.every(period=>next.periods[period].length>0);entry.accepted=ticket;
      this.touch(symbol);
      return{accepted:true,market:clone(next),ready:entry.historyValid};
    }
  }
  return{Store,PERIODS,LIMITS};
}));
