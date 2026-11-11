(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceEditing=api;}(globalThis,function(){
  'use strict';
  const clone=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value??null;
  const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  const forecast=object=>['wave','path'].includes(object?.kind);
  const pointValue=point=>point&&Object.fromEntries(Object.entries(point).filter(([key])=>key!=='label'));
  const identified=points=>Array.isArray(points)&&points.every(point=>typeof point.id==='string'&&point.id)&&new Set(points.map(point=>point.id)).size===points.length;
  const put=(value,key,next)=>{if(next===undefined)delete value[key];else value[key]=clone(next);};
  function fieldChanges(before,after,ignored=[]){
    const changes={};for(const key of new Set([...Object.keys(before||{}),...Object.keys(after||{})]))if(!ignored.includes(key)&&!equal(before?.[key],after?.[key]))changes[key]={before:clone(before?.[key]),after:clone(after?.[key])};return changes;
  }
  function objectChange(before,after){
    const change={before:clone(before),after:clone(after)};
    if(!before||!after)return change;
    const useNodes=forecast(before)&&forecast(after)&&identified(before.points)&&identified(after.points);
    change.fields=fieldChanges(before,after,useNodes?['points']:[]);
    if(useNodes){
      const a=new Map(before.points.map(p=>[p.id,p])),b=new Map(after.points.map(p=>[p.id,p])),changes={};
      for(const id of new Set([...a.keys(),...b.keys()])){
        const old=a.get(id),next=b.get(id);if(equal(pointValue(old),pointValue(next)))continue;
        changes[id]={before:clone(old),after:clone(next),...(old&&next?{fields:fieldChanges(old,next,['id','label'])}:{})};
      }
      const beforeOrder=before.points.map(p=>p.id),afterOrder=after.points.map(p=>p.id),common=beforeOrder.filter(id=>b.has(id));
      change.nodes={changes,beforeOrder,afterOrder,reorder:!equal(common,afterOrder.filter(id=>a.has(id)))};
    }
    return change;
  }
  function operation(before,after){
    const fields=fieldChanges(before,after,['objects']),objects={},a=new Map((before.objects||[]).map(o=>[o.id,o])),b=new Map((after.objects||[]).map(o=>[o.id,o]));
    for(const id of new Set([...a.keys(),...b.keys()]))if(!equal(a.get(id),b.get(id))){const change=objectChange(a.get(id),b.get(id));if(!change.before||!change.after||Object.keys(change.fields||{}).length||Object.keys(change.nodes?.changes||{}).length||change.nodes?.reorder)objects[id]=change;}
    return{fields,objects};
  }
  function applyNodes(current,nodes,expected,target,check=true){
    const points=new Map((current||[]).map(p=>[p.id,clone(p)])),conflicts=[];
    for(const[id,change]of Object.entries(nodes.changes)){
      const actual=points.get(id),from=change[expected],to=change[target];
      if(!from||!to){if(check&&!equal(pointValue(actual),pointValue(from)))conflicts.push(id);else if(to)points.set(id,clone(to));else points.delete(id);}
      else if(!actual)conflicts.push(id);
      else for(const[key,field]of Object.entries(change.fields||fieldChanges(change.before,change.after,['id','label']))){if(check&&!equal(actual[key],field[expected]))conflicts.push(id+':'+key);else put(actual,key,field[target]);}
    }
    let order=(current||[]).map(p=>p.id).filter(id=>points.has(id));
    const desired=nodes[target+'Order'],fromOrder=nodes[expected+'Order'];
    if(nodes.reorder){
      const common=new Set(fromOrder.filter(id=>desired.includes(id)&&points.has(id))),actual=order.filter(id=>common.has(id));
      if(check&&!equal(actual,fromOrder.filter(id=>common.has(id))))conflicts.push('order');
      else{const reordered=desired.filter(id=>common.has(id));let index=0;order=order.map(id=>common.has(id)?reordered[index++]:id);}
    }
    for(let index=0;index<desired.length;index++){
      const id=desired[index];if(!points.has(id)||order.includes(id))continue;
      const next=desired.slice(index+1).find(candidate=>order.includes(candidate));
      const previous=desired.slice(0,index).reverse().find(candidate=>order.includes(candidate));
      const at=next?order.indexOf(next):previous?order.indexOf(previous)+1:order.length;order.splice(at,0,id);
    }
    for(const id of points.keys())if(!order.includes(id))order.push(id);
    return{points:order.map((id,index)=>({...points.get(id),label:String(index+1)})),conflicts};
  }
  function applyObject(current,raw,expected,target,check=true){
    const change=raw.fields||raw.nodes||!raw.before||!raw.after?raw:objectChange(raw.before,raw.after),from=change[expected],to=change[target],conflicts=[];
    if(!from||!to){if(check&&!equal(current,from))return{conflicts:['object']};return{value:clone(to),conflicts};}
    if(!current)return{conflicts:['object']};const value=clone(current);
    for(const[key,field]of Object.entries(change.fields||{})){if(check&&!equal(current[key],field[expected]))conflicts.push(key);else put(value,key,field[target]);}
    if(change.nodes){const result=applyNodes(current.points,change.nodes,expected,target,check);value.points=result.points;conflicts.push(...result.conflicts.map(key=>'points:'+key));if(forecast(value)&&value.points.length<2)conflicts.push('points');}
    return{value,conflicts};
  }
  function apply(current,op,undo=true){
    const expected=undo?'after':'before',target=undo?'before':'after',plan=clone(current),objects=new Map((plan.objects||[]).map(o=>[o.id,o])),conflicts=[];
    for(const[key,change]of Object.entries(op.fields||{})){if(!equal(plan[key],change[expected]))conflicts.push(key);else put(plan,key,change[target]??null);}
    for(const[id,change]of Object.entries(op.objects||{})){
      const actual=objects.get(id),result=applyObject(actual,change,expected,target);
      if(result.conflicts.length){conflicts.push(...result.conflicts.map(key=>key==='object'?'objects:'+id:'objects:'+id+':'+key));continue;}
      if(actual?.locked&&!equal(actual,result.value)&&!equal({...actual,locked:false},result.value)){conflicts.push('locked:'+id);continue;}
      if(result.value)objects.set(id,result.value);else objects.delete(id);
    }
    if(conflicts.length)return{plan:current,conflicts:[...new Set(conflicts)]};plan.objects=[...objects.values()];return{plan,conflicts};
  }
  // Group local deltas, never unrelated peer changes from intervening snapshots.
  function combine(older,newer){
    const fields=clone(older.fields||{}),objects=clone(older.objects||{});
    for(const[key,change]of Object.entries(newer.fields||{})){
      if(fields[key]&&!equal(fields[key].after,change.before))return null;
      fields[key]={before:clone(fields[key]?fields[key].before:change.before),after:clone(change.after)};
      if(equal(fields[key].before,fields[key].after))delete fields[key];
    }
    for(const[id,next]of Object.entries(newer.objects||{})){
      const previous=objects[id];if(!previous){objects[id]=clone(next);continue;}
      // Lifecycle edits stay separate: undoing creation must protect peer edits.
      if(!previous.before||!previous.after||!next.before||!next.after)return null;
      // Validate each newly touched property against our prior local state too.
      // If a peer changed it in between, splitting keeps that new baseline intact.
      const merged=applyObject(previous.after,next,'before','after');if(merged.conflicts.length)return null;
      const change=objectChange(previous.before,merged.value);
      if(equal(change.before,change.after))delete objects[id];else objects[id]=change;
    }
    return{fields,objects};
  }
  function isTextTarget(target){return Boolean(target?.isContentEditable||['INPUT','TEXTAREA','SELECT'].includes(target?.tagName)||target?.closest?.('[contenteditable="true"]'));}
  return{operation,apply,combine,isTextTarget,equal};
}));
