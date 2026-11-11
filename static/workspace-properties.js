(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceProperties=api;}(globalThis,function(){
  'use strict';
  const positive=value=>String(value).trim()!==''&&Number.isFinite(Number(value))&&Number(value)>0;
  function propertyPatch(object,field,value){
    if(!object||object.locked)return{error:'标记已锁定或已删除。'};
    const keys={name:'name',note:'note',color:'color',type:'zone_type',direction:'direction',effect:'effect',period:'period','text-side':'text_side'};
    if(keys[field])return{patch:{[keys[field]]:value}};
    if(!['low','high'].includes(field))return{error:'未知属性。'};
    if(!positive(value))return{error:'请输入有效的正价格。'};
    const price=Number(value),points=object.points.map(p=>({...p}));
    if(object.kind==='zone'){
      const low=Math.min(...points.map(p=>p.price)),high=Math.max(...points.map(p=>p.price));
      if(field==='low'?price>=high:price<=low)return{error:'下边界须小于上边界。'};
      for(const point of points)if(point.price===(field==='low'?low:high))point.price=price;
    }else{const delta=price-points[0].price;for(const point of points)point.price+=delta;if(points.some(p=>p.price<=0))return{error:'移动后有锚点价格不大于零。'};}
    return{patch:{points}};
  }
  function nodePatch(object,id,field,value){
    if(!object||object.locked)return{error:'标记已锁定或已删除。'};
    const index=object.points.findIndex(p=>(p.id||String(p.time)+':'+String(p.price))===id);if(index<0)return{error:'该点已被协作者删除。'};
    const number=field==='time'?Date.parse(value):Number(value);
    if(!['time','price'].includes(field)||!Number.isFinite(number)||field==='price'&&!positive(value)||field==='time'&&number<0)return{error:'请填写有效时间与正价格。'};
    const points=object.points.map((p,i)=>i===index?{...p,[field]:number}:{...p});return{patch:{points}};
  }
  return{propertyPatch,nodePatch};
}));
