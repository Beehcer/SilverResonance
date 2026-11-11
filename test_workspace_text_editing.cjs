'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');

function harness(run){
  const oldDocument=global.document,oldObserver=global.ResizeObserver;
  function make(tag){return{tag,style:{},attrs:{},listeners:{},children:[],hidden:false,value:'',setAttribute(key,value){this.attrs[key]=String(value);},getAttribute(key){return this.attrs[key];},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},addEventListener(type,fn){this.listeners[type]=fn;},getBoundingClientRect(){return{left:0,top:0};},focus(){this.focused=true;},select(){this.selectedText=true;},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},remove(){this.removed=true;},setPointerCapture(id){this.capture=id;},hasPointerCapture(id){return this.capture===id;},releasePointerCapture(){this.capture=null;}};}
  try{
    global.document={createElement:make,createElementNS:(_,tag)=>make(tag)};global.ResizeObserver=class{observe(){}};
    const host=Object.assign(make('div'),{clientWidth:500,clientHeight:260}),updates=[],presence=[],notices=[],chart=new C.Chart(host,'m3',{update(id,patch){updates.push({id,patch:structuredClone(patch)});chart.setPlan({...chart.plan,objects:chart.plan.objects.map(object=>object.id===id?{...object,...patch}:object)});},presence:value=>presence.push(value),notice:message=>notices.push(message)});
    chart.render=function(){this.syncTitleEditor();};chart.view={start:0,end:100,min:90,max:110};chart.plot={x:5,y:5,w:400,h:200};chart.tx=C.transform(chart.view,chart.plot);
    const pointer=(x,y,timeStamp=1000)=>({button:0,clientX:x,clientY:y,pointerId:1,timeStamp,preventDefault(){},stopPropagation(){}});
    const key=(value,extra={})=>({key:value,preventDefault(){},stopPropagation(){},...extra});
    const names=(kind='text',extra={})=>({id:kind,kind,period:'m3',name:'等待回抽',color:'#ffcc44',points:[{time:50,price:103},...(kind==='text'||kind==='horizontal'?[]:[{time:80,price:kind==='segment'?103:99}])],...extra});
    const textPoint=object=>{const label=chart.nameLabel(object);return{x:label.left+label.width/2,y:label.y-3};};
    run({chart,host,updates,presence,notices,pointer,key,names,textPoint,make});
  }finally{global.document=oldDocument;global.ResizeObserver=oldObserver;}
}

test('all existing editable names open inline on two presses after SVG replacement, including left-side text',()=>{
  harness(({chart,updates,pointer,key,names,textPoint})=>{
    for(const [kind,extra] of [['text',{}],['text',{text_side:'left'}],['zone',{}],['trend',{}],['reference',{}],['horizontal',{}],['segment',{}]]){
      const object=names(kind,extra);chart.setPlan({objects:[object]});chart.lastTitleClick=null;chart.suppressDoubleClickUntil=0;const point=textPoint(object);
      chart.down(pointer(point.x,point.y,1000));chart.up(pointer(point.x,point.y,1020));chart.down(pointer(point.x,point.y,1200));chart.up(pointer(point.x,point.y,1220));
      assert.equal(chart.titleObjectId,object.id,kind+JSON.stringify(extra));assert.equal(chart.titleEditor.hidden,false);assert.equal(chart.titleEditor.value,object.name);
      assert.equal(chart.titleEditor.attrs['aria-label'],kind==='text'?'文本内容':'图层名称');assert.equal(chart.titleEditor.selectedText,true);assert.equal(updates.length,0);
      chart.titleEditor.listeners.keydown(key('Enter'));assert.equal(chart.titleObjectId,null);
    }
  });
});

test('native doubleclick uses exact rendered text target and opens at its label instead of at the candle',()=>{
  harness(({chart,pointer,names,textPoint})=>{
    const object=names('text',{text_side:'left'});chart.plan={objects:[object]};const label=chart.nameLabel(object),point=textPoint(object);
    chart.doubleClick({...pointer(point.x,point.y),target:{closest(selector){return selector==='[data-edit-name]'?{getAttribute:()=>object.id}:null;}}});
    assert.equal(chart.titleObjectId,object.id);assert.equal(parseFloat(chart.titleEditor.style.left),label.left);
    assert.equal(chart.hit(point).object.id,object.id,'left-side text must also select and drag from its rendered label');
  });
});

test('typing publishes only name immediately and preserves remote style, geometry and other layers',()=>{
  harness(({chart,updates,presence,key,names,textPoint})=>{
    const object=names(),point=textPoint(object);chart.plan={objects:[object]};chart.openTitleEditor(object,point);
    chart.titleEditor.value='反弹受阻';chart.titleEditor.listeners.input({isComposing:false});
    assert.deepEqual(updates,[{id:object.id,patch:{name:'反弹受阻'}}]);assert.equal(chart.plan.objects[0].name,'反弹受阻');assert.equal(chart.titleEditor.hidden,false);
    const peer={...chart.plan.objects[0],color:'#00ff88',points:[{time:60,price:105}]};chart.setPlan({objects:[peer,{...object,id:'peer'}]});
    chart.titleEditor.value='反弹受阻再考虑';chart.titleEditor.listeners.input({isComposing:false});
    assert.deepEqual(updates.at(-1).patch,{name:'反弹受阻再考虑'});assert.equal(chart.plan.objects[0].color,'#00ff88');assert.deepEqual(chart.plan.objects[0].points,peer.points);assert.equal(chart.plan.objects.length,2);
    chart.titleEditor.listeners.keydown(key('Escape'));assert.equal(chart.titleEditor.hidden,true);assert.equal(chart.plan.objects[0].name,'反弹受阻再考虑','Escape ends editing without rolling back text already shared');
    assert.equal(presence.at(-1).editing,false);
  });
});

test('Chinese composition is not saved halfway and Enter during composition cannot close the editor',()=>{
  harness(({chart,updates,key,names,textPoint})=>{
    const object=names();chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));
    chart.titleEditor.listeners.compositionstart({});chart.titleEditor.value='fan';chart.titleEditor.listeners.input({isComposing:true});chart.titleEditor.listeners.keydown(key('Enter',{isComposing:true,keyCode:229}));
    assert.equal(updates.length,0);assert.equal(chart.titleObjectId,object.id);
    chart.titleEditor.value='反弹';chart.titleEditor.listeners.compositionend({isComposing:false});assert.deepEqual(updates,[{id:object.id,patch:{name:'反弹'}}]);
    chart.titleEditor.listeners.keydown(key('Enter'));assert.equal(chart.titleObjectId,null);assert.equal(updates.length,1);
  });
});

test('blur flushes a final value once but untouched text never overwrites a collaborator rename',()=>{
  harness(({chart,updates,names,textPoint})=>{
    const object=names();chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));chart.titleEditor.value='开盘后观察';chart.titleEditor.listeners.blur({});
    assert.deepEqual(updates,[{id:object.id,patch:{name:'开盘后观察'}}]);assert.equal(chart.titleObjectId,null);
    const current=chart.plan.objects[0];chart.openTitleEditor(current,textPoint(current));chart.setPlan({objects:[{...current,name:'伙伴刚刚改了'}]});chart.titleEditor.listeners.blur({});
    assert.equal(updates.length,1);assert.equal(chart.plan.objects[0].name,'伙伴刚刚改了');
  });
});

test('locked, deleted, hidden or deselected objects cannot receive stale inline edits',()=>{
  harness(({chart,updates,notices,names,textPoint})=>{
    const object=names('zone');chart.plan={objects:[{...object,locked:true}]};chart.openTitleEditor(object,textPoint(object));assert.equal(chart.titleObjectId,undefined);assert.equal(notices.length,1);
    for(const change of ['delete','lock','hide','select-other']){
      chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));const input=chart.titleEditor;
      if(change==='select-other'){chart.selected='other';chart.render();}else chart.setPlan({objects:change==='delete'?[]:[{...object,...(change==='lock'?{locked:true}:{visible:false})}]});
      assert.equal(chart.titleObjectId,null,change);assert.equal(input.hidden,true);input.value='不得重新创建';input.listeners.input({});input.listeners.blur({});
      assert.equal(updates.length,0,change);
    }
  });
});

test('switching chart tools closes editing and single click on a name never mutates coordinates',()=>{
  harness(({chart,updates,pointer,names,textPoint})=>{
    const object=names('trend');chart.plan={objects:[object]};const original=structuredClone(object),point=textPoint(object);
    chart.down(pointer(point.x,point.y));chart.up(pointer(point.x,point.y));assert.deepEqual(object,original);assert.equal(updates.length,0);
    chart.openTitleEditor(object,point);chart.setTool('wave');assert.equal(chart.titleObjectId,null);assert.equal(chart.titleEditor.hidden,true);
  });
});

test('automatic price labels and wave numbers stay outside the name editor',()=>{
  harness(({chart,pointer,names,textPoint,make})=>{
    const segment=names('segment',{name:'水平线段'}),wave={id:'wave',kind:'wave',period:'m3',points:C.wavePoints([{time:10,price:107},{time:30,price:103},{time:70,price:106}])};
    chart.plan={objects:[segment,wave]};assert.equal(chart.nameLabel(segment),null);
    const point={x:chart.tx.x(80)+10,y:chart.tx.y(103)-6};chart.doubleClick({...pointer(point.x,point.y),target:{closest:selector=>selector==='[data-layer-price],[data-segment-price]'?{}:null}});
    assert.equal(chart.titleObjectId,undefined);
    const updates=[];chart.callbacks.update=(id,patch)=>updates.push({id,patch});chart.doubleClick(pointer(chart.tx.x(30),chart.tx.y(103),2000));
    assert.equal(updates[0].patch.points.length,2);assert.equal(chart.titleObjectId,undefined,'doubleclick still deletes a selected wave node');
    const layer=make('g');chart.drawObject(layer,segment);const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer);assert.equal(nodes.filter(node=>node.attrs['data-edit-name']).length,0);assert.ok(nodes.some(node=>node.attrs['data-segment-price']===segment.id));
  });
});

test('rendered editable labels expose name targets while prices remain separate and default segments stay uncluttered',()=>{
  harness(({chart,names,make})=>{
    for(const kind of ['text','zone','trend','reference','horizontal','segment']){
      const object=names(kind),layer=make('g');chart.drawObject(layer,object);const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer),labels=nodes.filter(node=>node.attrs['data-edit-name']===object.id);
      assert.equal(labels.length,1,kind);assert.equal(kind==='text'?labels[0].children.map(line=>line.textContent).join('\n'):labels[0].textContent,object.name);
      if(kind==='horizontal')assert.equal(nodes.find(node=>node.attrs['data-layer-price']===object.id).textContent,'103');
      if(kind==='segment')assert.equal(nodes.find(node=>node.attrs['data-segment-price']===object.id).textContent,'103');
    }
  });
});

test('multiline text renders left-aligned tspans around a vertically centered point on either side',()=>{
  harness(({chart,names,make})=>{
    for(const side of ['right','left']){
      const object=names('text',{text_side:side,name:'观察反弹\r\n\r\n受阻再空，等待确认'}),layer=make('g');chart.plan={objects:[object]};chart.selected=object.id;chart.drawObject(layer,object);
      const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer),label=chart.nameLabel(object),text=nodes.find(node=>node.attrs['data-edit-name']===object.id),lines=text.children;
      assert.deepEqual(lines.map(line=>line.textContent),['观察反弹','','受阻再空，等待确认']);assert.equal(lines.every(line=>line.tag==='tspan'),true);assert.equal(text.textContent,undefined,'no stray null text before tspans');
      assert.equal(text.attrs['text-anchor'],'start');assert.equal(text.attrs['xml:space'],'preserve');assert.equal(new Set(lines.map(line=>line.attrs.x)).size,1,'shorter lines remain left-aligned');
      assert.equal(Number(lines[1].attrs.y)-Number(lines[0].attrs.y),16);assert.equal(label.top+label.height/2,chart.tx.y(103));
      if(side==='left')assert.equal(label.left+label.width,chart.tx.x(50)-7);else assert.equal(label.left,chart.tx.x(50)+7);
      const outline=nodes.find(node=>node.attrs['data-text-selection']===object.id);assert.equal(Number(outline.attrs.height),label.height+6);assert.equal(Number(outline.attrs.width),label.width+6);
      for(const row of [0,1,2]){const at={x:label.left+label.width/2,y:label.top+row*16+8};assert.equal(chart.hit(at)?.object.id,object.id,'all rows including blank lines select the whole text');}
      assert.equal(chart.titleContains(object,{x:label.left+5,y:label.top+label.height+5}),false);
    }
  });
});

test('double click on the final line opens a textarea sized for the complete block, while other layer names use inputs',()=>{
  harness(({chart,names,pointer})=>{
    const object=names('text',{name:'第一行\n第二行\n第三行'});chart.plan={objects:[object]};const label=chart.nameLabel(object),point={x:label.left+20,y:label.top+label.height-7};
    chart.down(pointer(point.x,point.y,1000));chart.up(pointer(point.x,point.y,1020));chart.down(pointer(point.x,point.y,1200));
    assert.equal(chart.titleObjectId,object.id);assert.equal(chart.titleEditor.tag,'textarea');assert.equal(chart.titleEditor.maxLength,2000);assert.equal(chart.titleEditor.wrap,'off');assert.ok(parseFloat(chart.titleEditor.style.height)>label.height);assert.equal(parseFloat(chart.titleEditor.style.top),label.top-7);
    const textarea=chart.titleEditor,other=names('zone');chart.setPlan({objects:[object,other]});chart.openTitleEditor(other,point);assert.equal(chart.titleEditor.tag,'input');assert.equal(chart.titleEditor.maxLength,80);assert.equal(textarea.removed,true);
    textarea.value='旧输入框不能污染区域名';textarea.listeners.input({});textarea.listeners.blur({});assert.equal(chart.titleObjectId,other.id);assert.equal(chart.plan.objects[1].name,other.name);
  });
});

test('Alt+Enter replaces the selection with a real newline and shares exactly one name-only edit',()=>{
  harness(({chart,names,key,updates})=>{
    const object=names('text',{name:'等待反弹受阻'});chart.plan={objects:[object]};chart.openTitleEditor(object);const input=chart.titleEditor;input.setSelectionRange(2,4);let prevented=0;
    input.listeners.keydown(key('Enter',{altKey:true,preventDefault(){prevented++;}}));
    assert.equal(prevented,1);assert.equal(input.value,'等待\n受阻');assert.equal(input.selectionStart,3);assert.equal(chart.titleObjectId,object.id);assert.deepEqual(updates,[{id:object.id,patch:{name:'等待\n受阻'}}]);
    input.value+='，再考虑';input.listeners.input({});assert.equal(chart.plan.objects[0].name,'等待\n受阻，再考虑');input.listeners.keydown(key('Enter'));assert.equal(chart.titleObjectId,null);assert.equal(updates.length,2);
  });
});

test('IME confirmation ignores both Enter variants until composition has ended and does not publish pinyin',()=>{
  harness(({chart,names,key,updates})=>{
    const object=names('text',{name:'等待\n确认'});chart.plan={objects:[object]};chart.openTitleEditor(object);const input=chart.titleEditor;
    input.listeners.compositionstart({});input.value='等待\nshouzu';input.listeners.input({isComposing:true});input.listeners.keydown(key('Enter',{altKey:true}));input.listeners.keydown(key('Enter',{keyCode:229}));assert.equal(updates.length,0);assert.equal(chart.titleObjectId,object.id);assert.equal(input.value,'等待\nshouzu');
    input.value='等待\n受阻';input.listeners.compositionend({});assert.deepEqual(updates,[{id:object.id,patch:{name:'等待\n受阻'}}]);
  });
});

test('new text uses a textarea with Alt+Enter, ordinary Enter adds all lines, and composition never submits early',()=>{
  harness(({chart,key})=>{
    const added=[];chart.callbacks.add=object=>added.push(object);chart.openTextEditor({time:50,price:103},{x:490,y:250});const input=chart.textInput,form=chart.textEditor;
    assert.equal(input.tag,'textarea');assert.equal(input.maxLength,2000);assert.equal(input.wrap,'off');assert.ok(parseFloat(form.style.left)+parseFloat(form.style.width)<=chart.host.clientWidth-4);assert.ok(parseFloat(form.style.top)+parseFloat(form.style.height)<=chart.host.clientHeight-4);
    input.value='等待';input.setSelectionRange(2,2);form.listeners.keydown(key('Enter',{altKey:true,target:input}));assert.equal(input.value,'等待\n');assert.equal(added.length,0);
    input.listeners.compositionstart({});input.value='等待\nshouzu';form.listeners.keydown(key('Enter',{target:input}));form.listeners.submit({preventDefault(){}});assert.equal(added.length,0);assert.equal(form.hidden,false);
    input.value='等待\n受阻';input.listeners.compositionend({});form.listeners.keydown(key('Enter',{target:input}));assert.equal(added.length,1);assert.equal(added[0].name,'等待\n受阻');assert.deepEqual(added[0].points,[{time:50,price:103}]);assert.equal(form.hidden,true);
    chart.openTextEditor({time:60,price:100},{x:250,y:150});input.value='取消\n草稿';form.listeners.keydown(key('Escape',{target:input}));assert.equal(added.length,1);assert.equal(form.hidden,true);
  });
});

test('multiline text drags from its final line while preserving all text and collaborator style',()=>{
  harness(({chart,names,pointer,updates})=>{
    const object=names('text',{name:'等反弹\n再看受阻\n确认后做空'});chart.plan={objects:[object]};const label=chart.nameLabel(object),point={x:label.left+20,y:label.top+label.height-8};
    chart.down(pointer(point.x,point.y,1000));assert.equal(chart.drag.kind,'object');assert.equal(chart.selected,object.id);assert.equal(chart.drag.anchor,null);
    chart.move(pointer(point.x+20,point.y+10,1100));chart.setPlan({objects:[{...object,color:'#00ff88',name:'伙伴补充\n等反弹\n确认后做空'}]});chart.up(pointer(point.x+20,point.y+10,1200));
    assert.equal(updates.length,1);assert.deepEqual(Object.keys(updates[0].patch),['points']);assert.equal(chart.plan.objects[0].name,'伙伴补充\n等反弹\n确认后做空');assert.equal(chart.plan.objects[0].color,'#00ff88');assert.notDeepEqual(chart.plan.objects[0].points,object.points);
    const movedLabel=chart.nameLabel(chart.plan.objects[0]);assert.equal(movedLabel.top+movedLabel.height/2,chart.tx.y(chart.plan.objects[0].points[0].price));
  });
});

test('zero opacity text retains a visible whole-block selection outline without exposing artwork',()=>{
  harness(({chart,names,make})=>{
    const object=names('text',{opacity:0,name:'第一行\n第二行\n第三行'});chart.plan={objects:[object]};chart.selected=object.id;const layer=make('g');chart.drawObject(layer,object);const artwork=layer.children.find(node=>node.attrs['data-object-artwork']===object.id),selection=layer.children.find(node=>node.attrs['data-object-selection']===object.id),bounds=selection.children.find(node=>node.tag==='rect'),label=chart.nameLabel(object);
    assert.equal(artwork.attrs.opacity,'0');assert.ok(Number(bounds.attrs.height)>=label.height);assert.ok(Number(bounds.attrs.width)>=label.width);assert.equal(selection.attrs.opacity,'0.9');
  });
});

test('Alt+Enter respects the text content limit and does not apply it to ordinary layer names',()=>{
  harness(({chart,names,key,updates})=>{
    const object=names('text',{name:'字'.repeat(2000)});chart.plan={objects:[object]};chart.openTitleEditor(object);chart.titleEditor.setSelectionRange(2000,2000);chart.titleEditor.listeners.keydown(key('Enter',{altKey:true}));assert.equal(chart.titleEditor.value.length,2000);assert.equal(updates.length,0);assert.equal(chart.titleObjectId,object.id);
    chart.titleEditor.setSelectionRange(1999,2000);chart.titleEditor.listeners.keydown(key('Enter',{altKey:true}));assert.equal(chart.titleEditor.value.length,2000);assert.equal(chart.titleEditor.value.at(-1),'\n');assert.equal(updates.length,1);
    const zone=names('zone');chart.plan={objects:[zone]};chart.openTitleEditor(zone);assert.equal(chart.titleEditor.maxLength,80);chart.titleEditor.listeners.keydown(key('Enter',{altKey:true}));assert.equal(chart.titleObjectId,null);
  });
});
