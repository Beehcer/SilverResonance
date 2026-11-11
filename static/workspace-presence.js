(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspacePresence=api;}(globalThis,function(){
  'use strict';
  // One request at a time, with a trailing sample so the final mouse position is never lost.
  class Sender{
    constructor(send,onResult=()=>{},options={}){this.send=send;this.onResult=onResult;this.interval=options.interval??50;this.now=options.now||Date.now;this.setTimer=options.setTimer||((fn,delay)=>globalThis.setTimeout(fn,delay));this.clearTimer=options.clearTimer||(timer=>globalThis.clearTimeout(timer));this.epoch=0;this.lastSent=-Infinity;this.pending=null;this.timer=null;this.inFlight=false;}
    push(payload,immediate=false){this.pending=JSON.parse(JSON.stringify(payload));if(immediate&&this.timer){this.clearTimer(this.timer);this.timer=null;}this.schedule(immediate);}
    schedule(immediate=false){if(this.inFlight||this.timer||!this.pending)return;const delay=immediate?0:Math.max(0,this.interval-(this.now()-this.lastSent));if(delay){this.timer=this.setTimer(()=>{this.timer=null;this.flush();},delay);}else this.flush();}
    async flush(){if(this.inFlight||!this.pending)return;const payload=this.pending,epoch=this.epoch;this.pending=null;this.inFlight=true;this.lastSent=this.now();try{const result=await this.send(payload);if(epoch===this.epoch)this.onResult(result);}catch{/* The connection indicator and heartbeat handle transient failures. */}finally{if(epoch===this.epoch){this.inFlight=false;this.schedule();}}}
    reset(){this.epoch++;if(this.timer)this.clearTimer(this.timer);this.timer=null;this.pending=null;this.inFlight=false;this.lastSent=-Infinity;}
  }
  return{Sender};
}));
