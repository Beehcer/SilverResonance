'use strict';
const assert=require('node:assert/strict'),{test}=require('node:test');
const M=require('./static/workspace-merge.js'),E=require('./static/workspace-editing.js'),P=require('./static/workspace-properties.js');
const copy=value=>JSON.parse(JSON.stringify(value));
const anchor=(offset=2.5)=>({period:'m30',base_time:1794380400000,offset});
const plan=()=>({objects:[{id:'w',kind:'wave',period:'m30',points:[{id:'a',label:'1',time:1794382200000,price:100},{id:'b',label:'2',time:1794384000000,price:110}]}]});

test('serialized anchor-only edits merge without changing peer prices or other nodes',()=>{
  const base=plan(),local=copy(base),peer=copy(base);local.objects[0].points[0].chart_anchor=anchor();peer.objects[0].points[0].price=103;peer.objects[0].points[1].price=112;
  const pending=JSON.parse(JSON.stringify(M.diff(base,local)));assert.deepEqual(pending.object_edits[0].node_updates,[{id:'a',changes:{time:local.objects[0].points[0].time,chart_anchor:anchor()}}]);
  const merged=M.apply(peer,pending);assert.deepEqual(merged.objects[0].points[0],{...peer.objects[0].points[0],chart_anchor:anchor()});assert.deepEqual(merged.objects[0].points[1],peer.objects[0].points[1]);
});

test('anchor clearing survives JSON and missing legacy anchors do not create spurious edits',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor();
  for(const explicit of [false,true]){
    const local=copy(base);if(explicit)local.objects[0].points[0].chart_anchor=null;else delete local.objects[0].points[0].chart_anchor;
    const pending=JSON.parse(JSON.stringify(M.diff(base,local)));assert.deepEqual(pending.object_edits[0].node_updates,[{id:'a',changes:{time:local.objects[0].points[0].time,chart_anchor:null}}]);
    assert.equal(M.apply(base,pending).objects[0].points[0].chart_anchor,null);
  }
  const legacy=plan(),cleared=copy(legacy);cleared.objects[0].points[0].chart_anchor=null;assert.equal(M.hasPending(M.diff(legacy,cleared)),false);
});

test('inserted and in-flight edited anchors survive acknowledgement without duplicate nodes',()=>{
  const base=plan(),local=copy(base);local.objects[0].points.splice(1,0,{id:'new',label:'2',time:1794383100000,price:105,chart_anchor:anchor(1.25)});
  const sent=M.diff(base,local);local.objects[0].points[1].chart_anchor=anchor(1.75);local.objects[0].points[1].price=106;
  const remaining=M.ack(M.diff(base,local),sent,base);assert.deepEqual(remaining.object_edits[0].node_updates,[{id:'new',changes:{time:1794383100000,price:106,chart_anchor:anchor(1.75)}}]);
  const saved=M.apply(M.apply(base,sent),JSON.parse(JSON.stringify(remaining)));assert.deepEqual(saved.objects[0].points.map(point=>point.id),['a','new','b']);assert.deepEqual(saved.objects[0].points[1].chart_anchor,anchor(1.75));
});

test('anchor undo and redo retain peer node changes and reject edits to the same anchor',()=>{
  const base=plan(),local=copy(base);local.objects[0].points[0].chart_anchor=anchor();const op=E.operation(base,local),peer=copy(local);peer.objects[0].points[1].price=120;
  const undone=E.apply(peer,op);assert.deepEqual(undone.conflicts,[]);assert.equal(undone.plan.objects[0].points[0].chart_anchor,undefined);assert.equal(undone.plan.objects[0].points[1].price,120);
  const redone=E.apply(undone.plan,op,false);assert.deepEqual(redone.conflicts,[]);assert.deepEqual(redone.plan.objects[0].points[0].chart_anchor,anchor());assert.equal(redone.plan.objects[0].points[1].price,120);
  peer.objects[0].points[0].chart_anchor=anchor(9);assert.ok(E.apply(peer,op).conflicts.some(key=>key.endsWith('chart_anchor')));
});

test('editing an absolute node time clears the chart anchor while price-only edits retain it',()=>{
  const object=plan().objects[0];object.points[0].chart_anchor=anchor();
  const price=P.nodePatch(object,'a','price','102').patch;assert.deepEqual(price.points[0].chart_anchor,anchor());
  const time=P.nodePatch(object,'a','time','2026-11-12T09:00:00+08:00').patch;assert.equal(time.points[0].chart_anchor,null);assert.deepEqual(time.points[1],object.points[1]);
  const delta=M.diff({objects:[object]},{objects:[{...object,...time}]});assert.equal(delta.object_edits[0].node_updates[0].changes.chart_anchor,null);
  assert.deepEqual(object.points[0].chart_anchor,anchor());
});

test('concurrent horizontal edits never combine one editor time with another editor anchor',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor(1);
  const local=copy(base),peer=copy(base);local.objects[0].points[0].chart_anchor=anchor(2);peer.objects[0].points[0].time+=3600000;peer.objects[0].points[0].chart_anchor=anchor(4);
  const first=M.diff(base,local),second=M.diff(base,peer);
  assert.deepEqual(M.apply(M.apply(base,second),first).objects[0].points[0],local.objects[0].points[0]);
  assert.deepEqual(M.apply(M.apply(base,first),second).objects[0].points[0],peer.objects[0].points[0]);
  const timeOnly=copy(base);timeOnly.objects[0].points[0].time+=1800000;
  assert.deepEqual(M.diff(base,timeOnly).object_edits[0].node_updates[0].changes,{time:timeOnly.objects[0].points[0].time,chart_anchor:anchor(1)});
});

test('concurrent price-only edit preserves complete peer horizontal coordinate in either arrival order',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor(1);
  const vertical=copy(base),horizontal=copy(base);vertical.objects[0].points[0].price=120;horizontal.objects[0].points[0].time+=3600000;horizontal.objects[0].points[0].chart_anchor=anchor(4.25);
  const price=M.diff(base,vertical),time=M.diff(base,horizontal);
  assert.deepEqual(price.object_edits[0].node_updates[0].changes,{price:120});
  for(const [first,second] of [[price,time],[time,price]])assert.deepEqual(M.apply(M.apply(base,first),second).objects[0].points[0],{...horizontal.objects[0].points[0],price:120});
});

test('legacy absolute-time delta clears an existing anchor while a legacy price delta retains it',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor();
  const result=M.apply(base,{object_edits:[{id:'w',node_updates:[{id:'a',changes:{time:1794389999000}}]}]});
  assert.equal(result.objects[0].points[0].chart_anchor,undefined);
  assert.equal(result.objects[0].points[0].time,1794389999000);
  assert.deepEqual(M.apply(base,{object_edits:[{id:'w',node_updates:[{id:'a',changes:{price:125}}]}]}).objects[0].points[0].chart_anchor,anchor());
});

test('undo and redo refuse half-coordinate restoration after peer changes the other horizontal component',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor(1);
  for(const field of ['time','chart_anchor']){
    const local=copy(base);if(field==='time')local.objects[0].points[0].time+=1800000;else local.objects[0].points[0].chart_anchor=anchor(2);
    const op=E.operation(base,local),other=field==='time'?'chart_anchor':'time';
    for(const undo of [true,false]){
      const peer=copy(undo?local:base);if(other==='time')peer.objects[0].points[0].time+=7200000;else peer.objects[0].points[0].chart_anchor=anchor(8);
      const result=E.apply(peer,op,undo);assert.ok(result.conflicts.some(key=>key.endsWith(other)));assert.deepEqual(result.plan,peer);
    }
  }
});

test('grouped anchor then time edits undo as one horizontal coordinate and retain independent peer price',()=>{
  const base=plan();base.objects[0].points[0].chart_anchor=anchor(1);
  const first=copy(base);first.objects[0].points[0].chart_anchor=anchor(2);
  const second=copy(first);second.objects[0].points[0].time+=1800000;
  const grouped=E.combine(E.operation(base,first),E.operation(first,second));assert.ok(grouped);
  const peer=copy(second);peer.objects[0].points[0].price=125;
  const undone=E.apply(peer,grouped);assert.deepEqual(undone.conflicts,[]);assert.deepEqual(undone.plan.objects[0].points[0],{...base.objects[0].points[0],price:125});
  const redone=E.apply(undone.plan,grouped,false);assert.deepEqual(redone.conflicts,[]);assert.deepEqual(redone.plan.objects[0].points[0],peer.objects[0].points[0]);
});

test('legacy acknowledgement also retains or removes the full horizontal pair',()=>{
  const base=plan(),first=copy(base);first.objects[0].points[0].chart_anchor=anchor(1);
  const sent=M.diff(base,first),latest=copy(first);latest.objects[0].points[0].chart_anchor=anchor(2);
  const remaining=M.ack(M.diff(base,latest),sent);
  assert.deepEqual(remaining.object_edits[0].node_updates[0].changes,{time:latest.objects[0].points[0].time,chart_anchor:anchor(2)});
  assert.equal(M.hasPending(M.ack(sent,sent)),false);
});
