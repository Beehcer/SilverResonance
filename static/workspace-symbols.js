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

  const GROUPS=Object.freeze([
    {id:'default',label:'默认',custom:true},
    {id:'favorites',label:'喜爱',custom:true},
    {id:'energy',label:'能源',custom:false},
    {id:'chemical',label:'化工',custom:false},
    {id:'agriculture',label:'农产品',custom:false},
    {id:'precious',label:'贵金属',custom:false},
    {id:'all',label:'全部',custom:false},
  ].map(group=>Object.freeze(group)));
  const CUSTOM_GROUPS=new Set(GROUPS.filter(group=>group.custom).map(group=>group.id));
  // Use exchange + exact product code, never a substring of a name/contract.
  // Lithium carbonate is shown under chemical (new-energy materials) in this board taxonomy.
  const PRODUCTS={
    SHFE:{ag:'precious',au:'precious',bu:'energy',fu:'energy',sp:'chemical',ru:'chemical',br:'chemical'},
    INE:{sc:'energy',lu:'energy',nr:'chemical'},
    DCE:{jm:'energy',j:'energy',pg:'energy',l:'chemical',v:'chemical',pp:'chemical',eg:'chemical',eb:'chemical',bz:'chemical',a:'agriculture',b:'agriculture',m:'agriculture',y:'agriculture',p:'agriculture',c:'agriculture',cs:'agriculture',jd:'agriculture',lh:'agriculture',rr:'agriculture',lg:'agriculture'},
    CZCE:{zc:'energy',sa:'chemical',fg:'chemical',ma:'chemical',ta:'chemical',ur:'chemical',pf:'chemical',px:'chemical',pr:'chemical',cf:'agriculture',cy:'agriculture',rm:'agriculture',oi:'agriculture',rs:'agriculture',ap:'agriculture',cj:'agriculture',sr:'agriculture',pk:'agriculture',wh:'agriculture',pm:'agriculture',ri:'agriculture',jr:'agriculture',lr:'agriculture'},
    GFEX:{lc:'chemical',pt:'precious',pd:'precious'},
  };
  function productOf(value){
    const match=/^(?:KQ\.(?:m|i)@)?(?:([a-z]+)\.)?([a-z]+)(\d{3,4})?$/i.exec(idOf(value));
    if(!match)return null;
    if(match[3]&&(Number(match[3].slice(-2))<1||Number(match[3].slice(-2))>12))return null;
    return{exchange:(match[1]||'').toUpperCase(),code:match[2].toLowerCase(),contract:Boolean(match[3])};
  }
  function category(value){
    const product=productOf(value);if(!product)return'other';
    if(product.exchange)return Object.hasOwn(PRODUCTS,product.exchange)&&Object.hasOwn(PRODUCTS[product.exchange],product.code)?PRODUCTS[product.exchange][product.code]:'other';
    const categories=new Set(Object.values(PRODUCTS).map(exchange=>Object.hasOwn(exchange,product.code)?exchange[product.code]:null).filter(Boolean));
    return categories.size===1?[...categories][0]:'other';
  }
  const uniqueIds=values=>[...new Set(idsOf(values))];
  function contractKey(value){const product=productOf(value);return product?.exchange&&product.contract?product.exchange+'.'+product.code:null;}
  function resolverFor(symbols){
    const known=new Set(symbols.map(idOf)),products=new Map();
    for(const symbol of symbols){const key=contractKey(symbol);if(!key)continue;if(!products.has(key))products.set(key,[]);products.get(key).push(idOf(symbol));}
    return values=>{
      const resolved=[];
      for(const id of idsOf(values)){
        const candidates=products.get(contractKey(id)),current=known.has(id)?id:candidates?.length===1?candidates[0]:null;
        if(current&&!resolved.includes(current))resolved.push(current);
      }
      return resolved;
    };
  }
  // Missing storage migrates the old list once. Persisted empty lists stay empty.
  // A uniquely identifiable contract rollover preserves membership and position, but never adds a new product.
  function normalizeGroups(raw,symbols,legacyFavorites=[],legacyOrder=[]){
    const entries=symbolsOf(symbols),resolve=resolverFor(entries),saved=raw&&typeof raw==='object'&&!Array.isArray(raw)&&raw.version===1;
    if(saved)return{version:1,default:resolve(raw.default),favorites:resolve(raw.favorites)};
    const favorites=resolve(legacyFavorites),order=resolve(legacyOrder);
    return{version:1,default:sorted(entries,favorites,order).map(idOf),favorites:sorted(entries,favorites,order).map(idOf).filter(id=>favorites.includes(id))};
  }
  function groupItems(symbols,groups,groupId='all',query=''){
    const entries=symbolsOf(symbols),normalized=normalizeGroups(groups,entries),byId=new Map(entries.map(entry=>[idOf(entry),entry]));
    const matchesGroup=GROUPS.some(group=>group.id===groupId)?groupId:'all';
    const list=CUSTOM_GROUPS.has(matchesGroup)?normalized[matchesGroup].map(id=>byId.get(id)):
      matchesGroup==='all'?entries:entries.filter(entry=>category(entry)===matchesGroup);
    const search=typeof query==='string'?query.trim().toLowerCase():'';
    return search?list.filter(entry=>`${idOf(entry)} ${typeof entry?.name==='string'?entry.name:''}`.toLowerCase().includes(search)):list;
  }
  function copyGroups(groups){return{version:1,default:uniqueIds(groups?.default),favorites:uniqueIds(groups?.favorites)};}
  function addToGroup(groups,group,symbol){
    const result=copyGroups(groups),id=idOf(symbol);if(CUSTOM_GROUPS.has(group)&&id&&!result[group].includes(id))result[group].push(id);return result;
  }
  function removeFromGroup(groups,group,symbol){
    const result=copyGroups(groups),id=idOf(symbol);if(CUSTOM_GROUPS.has(group))result[group]=result[group].filter(value=>value!==id);return result;
  }
  function moveInGroup(groups,group,source,target,before=true){
    const result=copyGroups(groups),from=idOf(source),to=idOf(target);if(!CUSTOM_GROUPS.has(group))return result;
    const order=result[group];if(!from||!to||from===to||!order.includes(from)||!order.includes(to))return result;
    order.splice(order.indexOf(from),1);order.splice(order.indexOf(to)+(before?0:1),0,from);return result;
  }

  return{sorted,move,GROUPS,category,normalizeGroups,groupItems,addToGroup,removeFromGroup,moveInGroup};
}));
