(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceProperties=api;}(globalThis,function(){
  'use strict';
  const positive=value=>String(value).trim()!==''&&Number.isFinite(Number(value))&&Number(value)>0;
  const opacity=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1?value:1;
  function opacityPatch(value,key='opacity'){
    if(typeof value==='boolean'||value==null||String(value).trim()===''||!Number.isFinite(Number(value))||Number(value)<0||Number(value)>100)return{error:'不透明度须为 0–100%。'};
    return{patch:{[key]:Number(value)/100}};
  }
  function namePatch(object,value){
    if(typeof value!=='string')return{error:'内容必须是文本。'};
    const multiline=object?.kind==='text',limit=multiline?2000:80;
    let name=value.replace(/\r\n?/g,'\n');if(!multiline)name=name.replace(/\n/g,' ');
    if(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(name))return{error:'内容包含无效控制字符。'};
    if((multiline?Array.from(name).length:name.length)>limit)return{error:(multiline?'文本内容':'图层名称')+'最多 '+limit+' 个字符。'};
    return{patch:{name}};
  }
  function propertyPatch(object,field,value){
    if(!object||object.locked)return{error:'标记已锁定或已删除。'};
    if(field==='opacity'||field==='opacity-value')return opacityPatch(value);
    if(field==='name')return namePatch(object,value);
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
    const points=object.points.map((p,i)=>i===index?{...p,[field]:number,...(field==='time'&&p.chart_anchor?{chart_anchor:null}:{})}:{...p});return{patch:{points}};
  }
  return{propertyPatch,nodePatch,opacity,opacityPatch,namePatch};
}));
