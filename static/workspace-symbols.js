(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceSymbols=api;}(globalThis,function(){
  'use strict';

  const idOf=value=>typeof value==='string'?value:typeof value?.symbol==='string'?value.symbol:'';
  const idsOf=value=>Array.isArray(value)||value instanceof Set?Array.from(value,idOf).filter(Boolean):[];
  function symbolsOf(symbols){
    const seen=new Set();
    return(Array.isArray(symbols)?symbols:[]).filter(symbol=>{const id=idOf(symbol);if(!id||seen.has(id))return false;seen.add(id);return true;});
  }

  // Favorites are a fixed first group. Unknown/new contracts retain their provider order at the end of their group.
  function sorted(symbols,favoriteIds=[],orderIds=[]){
    const favorites=new Set(idsOf(favoriteIds)),rank=new Map();
    for(const id of idsOf(orderIds))if(!rank.has(id))rank.set(id,rank.size);
    return symbolsOf(symbols).map((symbol,index)=>({symbol,index,id:idOf(symbol)})).sort((a,b)=>
      Number(favorites.has(b.id))-Number(favorites.has(a.id))||
      (rank.get(a.id)??Infinity)-(rank.get(b.id)??Infinity)||a.index-b.index
    ).map(item=>item.symbol);
  }

  // Always pass the complete symbol list, even when the UI is filtered. Return IDs only for local persistence.
  // Cross-group drops are a no-op: dragging never adds/removes a favorite or silently moves it to a group boundary.
  function move(symbols,favoriteIds=[],orderIds=[],source,target,before=true){
    const order=sorted(symbols,favoriteIds,orderIds).map(idOf),from=idOf(source),to=idOf(target),favorites=new Set(idsOf(favoriteIds));
    if(!from||!to||from===to||!order.includes(from)||!order.includes(to)||favorites.has(from)!==favorites.has(to))return order;
    order.splice(order.indexOf(from),1);
    order.splice(order.indexOf(to)+(before?0:1),0,from);
    return order;
  }

  return{sorted,move};
}));
