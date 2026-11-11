(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.WorkspaceLayers=api;}(globalThis,function(){
  'use strict';
  const PERIODS={day:'日线',m30:'30分',m3:'3分'},RANK={day:0,m30:1,m3:2};
  const KINDS={wave:'预期走势',path:'预期走势',zone:'区域',horizontal:'水平线',segment:'水平线段',trend:'趋势线',reference:'参考线',text:'文本'};
  const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
  const rankOf=(object,index)=>typeof object.z_index==='number'&&Number.isFinite(object.z_index)?object.z_index:index;
  function ranked(objects){return(objects||[]).map((object,index)=>({object,index,rank:rankOf(object,index)})).sort((a,b)=>a.rank-b.rank||a.index-b.index);}
  function ordered(objects){return ranked(objects).map(item=>item.object);}
  function nextZ(objects){const list=ranked(objects);return list.length?list[list.length-1].rank+1:0;}
  function isForecast(object){return object.kind==='wave'||object.kind==='path';}
  function visibleOnPeriod(object,period){
    if(!object.period||object.period==='all')return true;
    if(object.period===period)return true;
    return isForecast(object)&&own(RANK,object.period)&&own(RANK,period)&&RANK[period]>RANK[object.period];
  }
  function provenanceInfo(object,period){
    const originPeriod=own(PERIODS,object.source_period)?object.source_period:(own(PERIODS,object.period)?object.period:null);
    const drawingPeriod=own(PERIODS,object.period)?object.period:'all';
    const visible=visibleOnPeriod(object,period),mapped=drawingPeriod!=='all'&&drawingPeriod!==period&&visible;
    const opacity=mapped?Math.pow(.4,RANK[period]-RANK[drawingPeriod]):1;
    let text=drawingPeriod==='all'?(originPeriod?PERIODS[originPeriod]+'绘制 · 全周期':'来源周期未记录 · 全周期'):PERIODS[drawingPeriod]+'原图';
    if(mapped)text+=' → '+PERIODS[period]+'映射 · '+Math.round(opacity*100)+'%';
    else if(period&&own(PERIODS,period)&&!visible)text+=' · 当前图不显示';
    let detail=originPeriod?'最初在'+PERIODS[originPeriod]+'绘制':'旧图层未记录绘制周期';
    if(drawingPeriod!=='all'&&originPeriod!==drawingPeriod)detail+='，现位于'+PERIODS[drawingPeriod];
    if(mapped)detail+='；映射到'+PERIODS[period]+'，不透明度 '+Math.round(opacity*100)+'%，编辑会同步原图';
    else if(drawingPeriod==='all')detail+='；在所有周期显示，编辑会同步所有盘面';
    return{text,detail,originPeriod,drawingPeriod,mapped,opacity,visible};
  }
  function provenance(object,period){return provenanceInfo(object,period);}
  function filterObjects(objects,{query='',period='all'}={}){
    const needle=String(query).trim().toLocaleLowerCase();
    return ordered(objects).reverse().filter(object=>{
      if(period!=='all'&&!visibleOnPeriod(object,period))return false;
      if(!needle)return true;
      return[object.name,object.note,KINDS[object.kind]||object.kind,provenance(object,period).text].join(' ').toLocaleLowerCase().includes(needle);
    });
  }
  // A move changes only the dragged layer, leaving peer edits and locked layers intact.
  // "before" means above the target in the sidebar, hence painted after it on the chart.
  function reorder(objects,id,targetId,before=true){
    const list=ranked(objects),moving=list.find(item=>item.object.id===id),target=list.find(item=>item.object.id===targetId);
    if(!moving||!target||moving.object.locked||id===targetId)return[];
    const rest=list.filter(item=>item!==moving),targetIndex=rest.indexOf(target),insert=targetIndex+(before?1:0);
    const result=rest.slice();result.splice(insert,0,moving);
    if(result.every((item,index)=>item===list[index]))return[];
    const lower=rest[insert-1],upper=rest[insert];
    const value=lower&&upper?lower.rank+(upper.rank-lower.rank)/2:lower?lower.rank+1:upper.rank-1;
    if(!Number.isFinite(value)||(lower&&value<=lower.rank)||(upper&&value>=upper.rank))return[];
    return[{id,patch:{z_index:value}}];
  }
  class Controller{
    constructor({list,callbacks={}}){
      if(!list)throw new Error('Layer list is required');
      this.list=list;this.document=list.ownerDocument;this.callbacks=callbacks;this.rows=new Map();this.objects=[];this.selected=null;this.period='m30';this.peers=[];this.query='';this.filter='all';
      list.replaceChildren();
      this.controls=this.element('div','layer-controls');
      this.search=this.element('input','layer-search');this.search.type='search';this.search.placeholder='搜索图层';this.search.setAttribute('aria-label','搜索图层');
      this.periodSelect=this.element('select','layer-period');this.periodSelect.setAttribute('aria-label','按显示周期筛选图层');this.periodSelect.title='包括从大周期映射过来的图层';
      for(const[value,label]of Object.entries({all:'全部周期',day:'显示在日线',m30:'显示在30分',m3:'显示在3分'})){const option=this.element('option',null,label);option.value=value;this.periodSelect.append(option);}
      this.result=this.element('div','layer-result');this.result.setAttribute('aria-live','polite');
      this.controls.append(this.search,this.periodSelect);list.before(this.controls,this.result);list.setAttribute('aria-label','绘图图层');
      this.onSearch=()=>{this.query=this.search.value;this.render();};this.onFilter=()=>{this.filter=this.periodSelect.value;this.render();};
      this.search.addEventListener('input',this.onSearch);this.periodSelect.addEventListener('change',this.onFilter);
    }
    element(tag,className,text){const node=this.document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
    object(id){return this.objects.find(object=>object.id===id);}
    update({objects=[],selected=null,period=this.period,peers=[]}={}){
      const selectionChanged=selected!==this.selected;this.objects=objects;this.selected=selected;this.period=period;this.peers=peers;
      if(selectionChanged&&selected&&this.object(selected)&&!filterObjects(objects,{query:this.query,period:this.filter}).some(object=>object.id===selected))this.clearFilters();
      this.render();if(selectionChanged&&selected)this.reveal(selected);
    }
    clearFilters(){this.query='';this.filter='all';this.search.value='';this.periodSelect.value='all';}
    row(id){
      const row=this.element('div','object-row layer-row');row.dataset.layerId=id;
      const drag=this.element('button','layer-drag','⋮⋮'),main=this.element('button','layer-main'),title=this.element('span','layer-name'),meta=this.element('small','layer-provenance'),peer=this.element('small','layer-peer');
      const locate=this.element('button','layer-locate','⌖'),visibility=this.element('button','object-visibility layer-visibility'),lock=this.element('button','object-visibility layer-lock');
      for(const button of[drag,main,locate,visibility,lock])button.type='button';main.append(title,meta,peer);row.append(drag,main,locate,visibility,lock);
      const context=event=>{event.preventDefault();event.stopPropagation();this.callbacks.context?.(id,event);};
      row.addEventListener('contextmenu',context);
      row.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey&&!event.ctrlKey&&!event.altKey&&!event.metaKey)context(event);});
      main.addEventListener('click',()=>this.callbacks.select?.(id));
      locate.addEventListener('click',()=>this.callbacks.locate?.(id));
      visibility.addEventListener('click',()=>{const object=this.object(id);if(object&&!object.locked)this.callbacks.update?.(id,{visible:object.visible===false});});
      lock.addEventListener('click',()=>{const object=this.object(id);if(object)this.callbacks.update?.(id,{locked:!object.locked});});
      drag.addEventListener('keydown',event=>this.keyMove(event,id));
      row.addEventListener('dragstart',event=>{if(this.object(id)?.locked){event.preventDefault();return;}this.dragged=id;row.classList.add('dragging');event.dataTransfer?.setData('text/plain',id);if(event.dataTransfer)event.dataTransfer.effectAllowed='move';});
      row.addEventListener('dragover',event=>{if(!this.dragged||this.dragged===id)return;event.preventDefault();const before=event.clientY<row.getBoundingClientRect().top+row.getBoundingClientRect().height/2;this.clearDropTargets();row.dataset.drop=before?'before':'after';if(event.dataTransfer)event.dataTransfer.dropEffect='move';});
      row.addEventListener('dragleave',event=>{if(!row.contains(event.relatedTarget))delete row.dataset.drop;});
      row.addEventListener('drop',event=>{if(!this.dragged)return;event.preventDefault();const from=this.dragged,before=row.dataset.drop!=='after';this.finishDrag();this.move(from,id,before);});
      row.addEventListener('dragend',()=>this.finishDrag());
      return{row,drag,main,title,meta,peer,locate,visibility,lock};
    }
    clearDropTargets(){for(const{row}of this.rows.values())delete row.dataset.drop;}
    finishDrag(){this.dragged=null;this.clearDropTargets();for(const{row}of this.rows.values())row.classList.remove('dragging');}
    move(id,target,before){const changes=reorder(this.objects,id,target,before);if(changes.length)this.callbacks.reorder?.(changes);}
    keyMove(event,id){
      if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();if(this.object(id)?.locked)return;
      const visible=filterObjects(this.objects,{query:this.query,period:this.filter}),index=visible.findIndex(object=>object.id===id);
      const all=ordered(this.objects).reverse();
      if(event.key==='ArrowUp'&&index>0)this.move(id,visible[index-1].id,true);
      else if(event.key==='ArrowDown'&&index>=0&&index<visible.length-1)this.move(id,visible[index+1].id,false);
      else if(event.key==='Home'&&all.length)this.move(id,all[0].id,true);
      else if(event.key==='End'&&all.length)this.move(id,all[all.length-1].id,false);
    }
    render(){
      const visible=filterObjects(this.objects,{query:this.query,period:this.filter}),ids=new Set(visible.map(object=>object.id));
      for(const[id,item]of this.rows)if(!ids.has(id)){item.row.remove();this.rows.delete(id);}
      this.empty?.remove();this.empty=null;
      let position=this.list.firstElementChild;
      for(const object of visible){
        let item=this.rows.get(object.id);if(!item){item=this.row(object.id);this.rows.set(object.id,item);}
        if(item.row!==position)this.list.insertBefore(item.row,position);position=item.row.nextElementSibling;
        const name=object.name||KINDS[object.kind]||'未命名图层',info=provenanceInfo(object,this.period);
        item.row.classList.toggle('selected',object.id===this.selected);item.row.classList.toggle('is-hidden',object.visible===false);item.row.classList.toggle('is-locked',Boolean(object.locked));item.row.draggable=!object.locked;
        item.main.setAttribute('aria-pressed',String(object.id===this.selected));item.title.textContent=(KINDS[object.kind]||'图层')+' · '+name;
        item.meta.textContent=info.text+(object.visible===false?' · 已隐藏':'')+(object.locked?' · 已锁定':'');item.meta.title=info.detail;
        item.drag.disabled=Boolean(object.locked);item.drag.title=object.locked?'解锁后可调整顺序':'拖动排序；也可用上下方向键调整';item.drag.setAttribute('aria-label','调整'+name+'的图层顺序');
        item.locate.title='在盘面定位 '+name;item.locate.setAttribute('aria-label','在盘面定位'+name);item.locate.hidden=typeof this.callbacks.locate!=='function';
        item.visibility.textContent=object.visible===false?'显示':'隐藏';item.visibility.disabled=Boolean(object.locked);item.visibility.setAttribute('aria-label',(object.visible===false?'显示':'隐藏')+name);
        item.lock.textContent=object.locked?'解锁':'锁定';item.lock.setAttribute('aria-label',(object.locked?'解锁':'锁定')+name);
        const editors=this.peers.filter(peer=>peer.object_id===object.id).map(peer=>peer.name||peer.user?.name||'伙伴');item.peer.textContent=editors.length?editors.join('、')+'正在编辑':'';item.peer.hidden=!editors.length;
      }
      this.result.textContent=visible.length===this.objects.length?`${this.objects.length} 个图层 · 拖动手柄排序`:`显示 ${visible.length} / ${this.objects.length} 个图层`;
      if(!visible.length){this.empty=this.element('p','empty',this.objects.length?'没有匹配的图层':'暂无绘图，在图上标注后会显示在这里。');this.list.append(this.empty);}
    }
    reveal(id,{clearFilters=true}={}){
      if(!this.object(id))return;
      if(!this.rows.has(id)&&clearFilters){this.clearFilters();this.render();}
      const row=this.rows.get(id)?.row;if(!row||!this.list.clientHeight)return;
      // Scroll only the sidebar list; selecting a drawing must never move a chart or page.
      const bounds=this.list.getBoundingClientRect(),rect=row.getBoundingClientRect();
      if(rect.top<bounds.top)this.list.scrollTop-=bounds.top-rect.top;
      else if(rect.bottom>bounds.bottom)this.list.scrollTop+=rect.bottom-bounds.bottom;
    }
    destroy(){this.finishDrag();this.search.removeEventListener('input',this.onSearch);this.periodSelect.removeEventListener('change',this.onFilter);this.controls.remove();this.result.remove();this.list.replaceChildren();this.rows.clear();}
  }
  return{Controller,ordered,nextZ,reorder,visibleOnPeriod,provenance,provenanceInfo,filterObjects};
}));
