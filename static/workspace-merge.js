(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceMerge=api;}(globalThis,function(){
  'use strict';
  const clone=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
  const fields=new Set(['archived','title','enabled','rule_version','direction','mode','session_label','expires_at','context_confirmed','context_note','main_level','main_after','main_buffer_ticks','entry_low','entry_high','trigger_price','trigger_buffer_ticks','stop_price','obstacle_price','planned_entry_price','notes']);
  const empty=()=>({changes:{},upsert_objects:[],delete_object_ids:[],object_edits:[]});
  const byId=items=>new Map((items||[]).map(item=>[item.id,item]));
  const identified=points=>Array.isArray(points)&&points.every(point=>typeof point.id==='string'&&point.id)&&new Set(points.map(point=>point.id)).size===points.length;
  const isForecast=object=>['wave','path'].includes(object?.kind);
  const edited=edit=>Object.keys(edit.changes||{}).length||(edit.unset||[]).length||(edit.node_updates||[]).length||(edit.node_inserts||[]).length||(edit.node_deletes||[]).length;
  const hasPending=pending=>!!(pending&&(Object.keys(pending.changes||{}).length||(pending.upsert_objects||[]).length||(pending.delete_object_ids||[]).length||(pending.object_edits||[]).some(edited)));

  function objectDiff(before,after){
    const edit={id:after.id,changes:{},unset:[],node_updates:[],node_inserts:[],node_deletes:[]};
    const nodes=isForecast(before)&&isForecast(after)&&identified(before.points)&&identified(after.points);
    for(const key of new Set([...Object.keys(before),...Object.keys(after)])){
      if(['id','kind'].includes(key)||(nodes&&key==='points')||equal(before[key],after[key]))continue;
      if(after[key]===undefined)edit.unset.push(key);else edit.changes[key]=clone(after[key]);
    }
    if(nodes){
      const previous=byId(before.points),next=byId(after.points);
      for(const point of before.points)if(!next.has(point.id))edit.node_deletes.push(point.id);
      after.points.forEach((point,index)=>{
        const old=previous.get(point.id);
        if(!old){
          edit.node_inserts.push({point:clone(point),after:after.points[index-1]?.id||null,before:after.points.slice(index+1).find(p=>previous.has(p.id))?.id||null});
        }else{
          const changes={};for(const key of ['time','price'])if(!equal(old[key],point[key]))changes[key]=point[key];
          if(Object.keys(changes).length)edit.node_updates.push({id:point.id,changes});
        }
      });
    }
    return edit;
  }

  function diff(base={},local={}){
    base=base||{};local=local||{};const pending=empty();
    for(const key of fields)if(!equal(base[key],local[key]))pending.changes[key]=clone(local[key]===undefined?null:local[key]);
    const previous=byId(base.objects),next=byId(local.objects);
    for(const [id]of previous)if(!next.has(id))pending.delete_object_ids.push(id);
    for(const [id,object]of next){
      if(!previous.has(id))pending.upsert_objects.push(clone(object));
      else{const edit=objectDiff(previous.get(id),object);if(edited(edit))pending.object_edits.push(edit);}
    }
    return pending;
  }

  function applyObject(object,edit){
    const result=clone(object);Object.assign(result,clone(edit.changes||{}));
    for(const key of edit.unset||[])delete result[key];
    if((edit.node_updates||[]).length||(edit.node_inserts||[]).length||(edit.node_deletes||[]).length){
      const deleted=new Set(edit.node_deletes||[]),points=(result.points||[]).filter(point=>!deleted.has(point.id)),nodes=byId(points);
      for(const update of edit.node_updates||[])if(nodes.has(update.id))Object.assign(nodes.get(update.id),clone(update.changes));
      for(const insert of edit.node_inserts||[]){
        if(nodes.has(insert.point.id))continue;
        const before=points.findIndex(point=>point.id===insert.before),after=points.findIndex(point=>point.id===insert.after);
        const point=clone(insert.point);points.splice(before>=0?before:after>=0?after+1:points.length,0,point);nodes.set(point.id,point);
      }
      result.points=points;
    }
    if(result.kind==='wave')result.points=(result.points||[]).map((point,index)=>({...point,label:String(index+1)}));
    return result;
  }

  function apply(remote={},pending=empty()){
    const result=clone(remote||{});Object.assign(result,clone(pending.changes||{}));
    const objects=byId(result.objects);
    for(const object of pending.upsert_objects||[])objects.set(object.id,clone(object));
    for(const edit of pending.object_edits||[])if(objects.has(edit.id))objects.set(edit.id,applyObject(objects.get(edit.id),edit));
    for(const id of pending.delete_object_ids||[])objects.delete(id);
    result.objects=[...objects.values()];return result;
  }

  function ack(current,sent,base){
    // Compare desired local state to the acknowledged state. This also preserves
    // add-then-delete and edits made to a newly inserted node during an HTTP save.
    if(base!==undefined)return diff(apply(base,sent),apply(base,current));
    // Legacy callers can acknowledge individual properties, but should pass base
    // when new objects/nodes may be edited while the request is in flight.
    const pending=clone(current||empty());pending.object_edits=pending.object_edits||[];
    for(const[key,value]of Object.entries(sent?.changes||{}))if(equal(pending.changes?.[key],value))delete pending.changes[key];
    pending.upsert_objects=(pending.upsert_objects||[]).filter(object=>!(sent?.upsert_objects||[]).some(saved=>saved.id===object.id&&equal(saved,object)));
    pending.delete_object_ids=(pending.delete_object_ids||[]).filter(id=>!(sent?.delete_object_ids||[]).includes(id));
    for(const edit of pending.object_edits){
      const saved=(sent?.object_edits||[]).find(item=>item.id===edit.id);if(!saved)continue;
      for(const[key,value]of Object.entries(saved.changes||{}))if(equal(edit.changes?.[key],value))delete edit.changes[key];
      edit.unset=(edit.unset||[]).filter(key=>!(saved.unset||[]).includes(key));
      for(const update of edit.node_updates||[]){const old=(saved.node_updates||[]).find(item=>item.id===update.id);if(old)for(const[key,value]of Object.entries(old.changes))if(equal(update.changes[key],value))delete update.changes[key];}
      edit.node_updates=(edit.node_updates||[]).filter(update=>Object.keys(update.changes).length);
      edit.node_inserts=(edit.node_inserts||[]).filter(insert=>!(saved.node_inserts||[]).some(old=>equal(insert,old)));
      edit.node_deletes=(edit.node_deletes||[]).filter(id=>!(saved.node_deletes||[]).includes(id));
    }
    pending.object_edits=pending.object_edits.filter(edited);return pending;
  }
  return{empty,hasPending,diff,apply,ack,equal};
}));
