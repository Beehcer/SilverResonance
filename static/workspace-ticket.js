(function(root,factory){'use strict';const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceTicket=api;}(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const price=v=>v!==null&&v!==undefined&&String(v).trim()!==''&&Number.isFinite(Number(v))&&Number(v)>0?Number(v):null;
  function directionOf(plan){const entry=price(plan.planned_entry_price),stop=price(plan.stop_price);return entry&&stop&&entry!==stop?(stop>entry?'short':'long'):null;}
  function withLevels(plan,changes){const next={...plan,...changes,enabled:false,context_confirmed:false},direction=directionOf(next);if(direction)next.direction=direction;return next;}
  function nextTool(field,plan,sequential){return sequential&&field==='planned_entry_price'?'planned-stop':null;}
  return{directionOf,withLevels,nextTool};
}));
