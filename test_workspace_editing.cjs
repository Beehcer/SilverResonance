const {test}=require('node:test');
const assert=require('node:assert/strict');
const E=require('./static/workspace-editing.js'),C=require('./static/workspace-chart.js');
test('undo/redo preserves objects and fields added remotely after my operation',()=>{
  const before={notes:'base',objects:[]},mine={id:'mine',points:[{time:1,price:2}]},peer={id:'peer'};
  const op=E.operation(before,{...before,objects:[mine]});
  const undo=E.apply({notes:'peer notes',objects:[mine,peer]},op,true);
  assert.deepEqual(undo.plan,{notes:'peer notes',objects:[peer]});
  assert.deepEqual(E.apply(undo.plan,op,false).plan,{notes:'peer notes',objects:[peer,mine]});
});
test('undo detects remote changes to same object and refuses locked deletion',()=>{
  const before={objects:[]},after={objects:[{id:'mine',locked:true}]},op=E.operation(before,after);
  assert.deepEqual(E.apply(after,op).conflicts,['locked:mine']);
  assert.deepEqual(E.apply({objects:[{id:'mine',locked:false,name:'peer'}]},op).conflicts,['objects:mine']);
});
test('server JSON key ordering does not turn my own saved object into a conflict',()=>{
  const before={objects:[]},after={objects:[{id:'a',kind:'path',points:[{time:1,price:100}]}]},op=E.operation(before,after);
  const server={objects:[{kind:'path',points:[{price:100,time:1}],id:'a'}]};
  assert.deepEqual(E.apply(server,op).conflicts,[]);assert.deepEqual(E.apply(server,op).plan.objects,[]);
});
test('unlock undo is allowed but compound unlock/move is not',()=>{
  const a={objects:[{id:'wave',locked:true,points:[{time:1,price:100}]}]},b=structuredClone(a);b.objects[0].locked=false;
  const op=E.operation(a,b);assert.deepEqual(E.apply(a,op,false).plan,b);assert.deepEqual(E.apply(b,op,true).plan,a);
});
test('text fields and contenteditable retain typing/paste/text undo',()=>{
  for(const tagName of ['INPUT','TEXTAREA','SELECT'])assert.equal(E.isTextTarget({tagName}),true);
  assert.equal(E.isTextTarget({isContentEditable:true}),true);assert.equal(E.isTextTarget({tagName:'DIV'}),false);
});
test('session gap zoom keeps the exact time and price under the cursor',()=>{
  const bars=[{time:0},{time:180000},{time:360000},{time:1000000}],view={start:0,end:1000000,min:90,max:110},plot={x:0,y:0,w:300,h:100},anchor={time:540000,price:103};
  const before=C.transform(view,plot,bars,'m3'),after=C.transform(C.zoomView(view,'m3',1.25,anchor,bars),plot,bars,'m3');
  assert.ok(Math.abs(before.x(anchor.time)-after.x(anchor.time))<1e-8);assert.ok(Math.abs(before.y(anchor.price)-after.y(anchor.price))<1e-8);
});
test('wave insertion/deletion preserves anchors and IDs while numbering consecutively',()=>{
  const points=C.wavePoints([{time:0,price:100},{time:180000,price:99},{time:360000,price:103},{time:540000,price:95}]);
  assert.deepEqual(points.map(p=>p.label),['1','2','3','4']);
  const inserted=C.wavePoints(C.editPathPoints(points,1,1,1,180000));
  assert.deepEqual(inserted.map(p=>p.label),['1','2','3','4','5']);
  assert.equal(inserted[3].id,points[2].id);
  assert.equal(inserted[3].time,points[2].time);assert.equal(inserted[3].price,points[2].price);
  const removed=C.wavePoints(C.editPathPoints(points,0,-1,1,180000));assert.deepEqual(removed.map(p=>p.label),['1','2','3']);
  assert.deepEqual(removed.map(p=>p.id),[points[0].id,points[2].id,points[3].id]);
});

const wavePlan=()=>({notes:'base',objects:[{id:'wave',kind:'wave',name:'预期走势',color:'#ff0',locked:false,points:[
  {id:'a',time:1,price:100,label:'1'},
  {id:'b',time:2,price:102,label:'2'},
  {id:'c',time:3,price:99,label:'3'},
]}]});
const points=plan=>plan.objects[0].points;
const point=(plan,id)=>points(plan).find(p=>p.id===id);

test('undo changes my property while retaining peer edits on the same object',()=>{
  const before=wavePlan(),after=structuredClone(before);after.objects[0].name='回抽计划';
  const current=structuredClone(after);current.objects[0].color='#0f0';current.objects[0].source_period='d1';current.notes='伙伴备注';
  const result=E.apply(current,E.operation(before,after));
  assert.deepEqual(result.conflicts,[]);
  assert.equal(result.plan.objects[0].name,'预期走势');
  assert.equal(result.plan.objects[0].color,'#0f0');
  assert.equal(result.plan.objects[0].source_period,'d1');
  assert.equal(result.plan.notes,'伙伴备注');
  assert.equal(current.objects[0].name,'回抽计划');
});

test('same-property peer edit refuses undo atomically without reverting unrelated fields',()=>{
  const before=wavePlan(),after=structuredClone(before);after.notes='我的备注';after.objects[0].name='我的标题';
  const current=structuredClone(after);current.objects[0].name='伙伴标题';
  const result=E.apply(current,E.operation(before,after));
  assert.deepEqual(result.conflicts,['objects:wave:name']);
  assert.strictEqual(result.plan,current);assert.equal(result.plan.notes,'我的备注');
});

test('locked objects reject edits and compound unlock plus move',()=>{
  const before=wavePlan(),after=structuredClone(before);point(after,'a').price=101;
  const current=structuredClone(after);current.objects[0].locked=true;
  assert.deepEqual(E.apply(current,E.operation(before,after)).conflicts,['locked:wave']);
  const locked=structuredClone(before);locked.objects[0].locked=true;
  const unlocked=structuredClone(locked);unlocked.objects[0].locked=false;point(unlocked,'a').price=101;
  assert.deepEqual(E.apply(locked,E.operation(locked,unlocked),false).conflicts,['locked:wave']);
});

test('node undo preserves peer edits to another node and another coordinate of my node',()=>{
  const before=wavePlan(),after=structuredClone(before);point(after,'a').price=104;
  const current=structuredClone(after);point(current,'a').time=7;point(current,'b').price=108;
  points(current).splice(2,0,{id:'peer',time:2.5,price:103,label:'3'});
  const op=E.operation(before,after),undo=E.apply(current,op);
  assert.deepEqual(undo.conflicts,[]);
  assert.equal(point(undo.plan,'a').price,100);assert.equal(point(undo.plan,'a').time,7);
  assert.equal(point(undo.plan,'b').price,108);
  assert.deepEqual(points(undo.plan).map(p=>p.id),['a','b','peer','c']);
  assert.deepEqual(points(undo.plan).map(p=>p.label),['1','2','3','4']);
  const redo=E.apply(undo.plan,op,false);assert.deepEqual(redo.conflicts,[]);
  assert.equal(point(redo.plan,'a').price,104);assert.equal(point(redo.plan,'a').time,7);
});

test('node undo refuses overwriting a peer edit to the same coordinate',()=>{
  const before=wavePlan(),after=structuredClone(before);point(after,'a').price=104;
  const current=structuredClone(after);point(current,'a').price=106;
  const result=E.apply(current,E.operation(before,after));
  assert.deepEqual(result.conflicts,['objects:wave:points:a:price']);assert.strictEqual(result.plan,current);
});

test('undo inserted node keeps peer insertion and redo restores only my node',()=>{
  const before=wavePlan(),after=structuredClone(before);
  points(after).splice(1,0,{id:'mine',time:1.5,price:101,label:'2'});
  const current=structuredClone(after);points(current).splice(2,0,{id:'peer',time:1.7,price:100,label:'3'});
  const op=E.operation(before,after),undo=E.apply(current,op);
  assert.deepEqual(undo.conflicts,[]);assert.deepEqual(points(undo.plan).map(p=>p.id),['a','peer','b','c']);
  const redo=E.apply(undo.plan,op,false);assert.deepEqual(redo.conflicts,[]);
  assert.deepEqual(points(redo.plan).map(p=>p.id),['a','peer','mine','b','c']);
  assert.deepEqual(points(redo.plan).map(p=>p.label),['1','2','3','4','5']);
});

test('undo node deletion uses surviving anchors and does not resurrect a peer deletion',()=>{
  const before=wavePlan();points(before).push({id:'d',time:4,price:98,label:'4'});
  const after=structuredClone(before);after.objects[0].points=points(after).filter(p=>p.id!=='b');
  const current=structuredClone(after);current.objects[0].points=points(current).filter(p=>p.id!=='a');
  point(current,'c').price=97;
  const result=E.apply(current,E.operation(before,after));
  assert.deepEqual(result.conflicts,[]);assert.deepEqual(points(result.plan).map(p=>p.id),['b','c','d']);
  assert.equal(point(result.plan,'c').price,97);assert.deepEqual(points(result.plan).map(p=>p.label),['1','2','3']);
});

test('undo does not erase a peer-modified inserted node or leave fewer than two nodes',()=>{
  const before=wavePlan(),after=structuredClone(before);
  points(after).push({id:'mine',time:4,price:98,label:'4'});
  const changed=structuredClone(after);point(changed,'mine').price=95;
  assert.deepEqual(E.apply(changed,E.operation(before,after)).conflicts,['objects:wave:points:mine']);
  const thinned=structuredClone(after);thinned.objects[0].points=points(thinned).filter(p=>['a','mine'].includes(p.id));
  assert.deepEqual(E.apply(thinned,E.operation(before,after)).conflicts,['objects:wave:points']);
});

test('undo node reordering keeps peer additions but refuses an intervening peer reorder',()=>{
  const before=wavePlan(),after=structuredClone(before);after.objects[0].points=[point(after,'b'),point(after,'a'),point(after,'c')];
  const current=structuredClone(after);points(current).splice(1,0,{id:'peer',time:9,price:99,label:'2'});
  const op=E.operation(before,after),undo=E.apply(current,op);
  assert.deepEqual(undo.conflicts,[]);assert.deepEqual(points(undo.plan).map(p=>p.id),['a','peer','b','c']);
  current.objects[0].points=[point(current,'c'),point(current,'a'),point(current,'b'),point(current,'peer')];
  assert.deepEqual(E.apply(current,op).conflicts,['objects:wave:points:order']);
});

test('grouped typing retains earliest local value and latest result without absorbing peers',()=>{
  const before=wavePlan(),one=structuredClone(before);one.objects[0].name='回';
  const peer=structuredClone(one);peer.objects[0].color='#0f0';point(peer,'b').price=108;
  const two=structuredClone(peer);two.objects[0].name='回抽';
  const combined=E.combine(E.operation(before,one),E.operation(peer,two));assert.ok(combined);
  const undo=E.apply(two,combined);assert.deepEqual(undo.conflicts,[]);
  assert.equal(undo.plan.objects[0].name,'预期走势');assert.equal(undo.plan.objects[0].color,'#0f0');assert.equal(point(undo.plan,'b').price,108);
  const redo=E.apply(undo.plan,combined,false);assert.equal(redo.plan.objects[0].name,'回抽');
  assert.equal(redo.plan.objects[0].color,'#0f0');
});

test('grouped node moves preserve independently edited coordinates and peer nodes',()=>{
  const before=wavePlan(),one=structuredClone(before);point(one,'a').price=101;
  const peer=structuredClone(one);point(peer,'a').time=8;points(peer).push({id:'peer',time:9,price:105,label:'4'});
  const two=structuredClone(peer);point(two,'a').price=103;
  const combined=E.combine(E.operation(before,one),E.operation(peer,two)),undo=E.apply(two,combined);
  assert.deepEqual(undo.conflicts,[]);assert.equal(point(undo.plan,'a').price,100);assert.equal(point(undo.plan,'a').time,8);
  assert.equal(point(undo.plan,'peer').price,105);
});

test('grouping splits when a peer edits a shared or newly touched property between steps',()=>{
  const before=wavePlan(),one=structuredClone(before);one.objects[0].name='回';
  const peer=structuredClone(one);peer.objects[0].name='伙伴';peer.objects[0].color='#0f0';
  const two=structuredClone(peer);two.objects[0].name='回抽';
  assert.equal(E.combine(E.operation(before,one),E.operation(peer,two)),null);
  const newField=structuredClone(peer);newField.objects[0].color='#f00';
  assert.equal(E.combine(E.operation(before,one),E.operation(peer,newField)),null);
  const nodeBefore=wavePlan(),nodeOne=structuredClone(nodeBefore);point(nodeOne,'a').price=101;
  const nodePeer=structuredClone(nodeOne);point(nodePeer,'a').time=8;
  const nodeTwo=structuredClone(nodePeer);point(nodeTwo,'a').time=9;
  assert.equal(E.combine(E.operation(nodeBefore,nodeOne),E.operation(nodePeer,nodeTwo)),null);
});

test('grouping local plan fields removes net-zero changes and respects peer boundaries',()=>{
  const start={notes:'原',objects:[]},one={...start,notes:'原一'},two={...one,notes:'原一二'};
  const combined=E.combine(E.operation(start,one),E.operation(one,two));
  assert.equal(E.apply(two,combined).plan.notes,'原');
  const back=E.combine(combined,E.operation(two,start));assert.deepEqual(back,{fields:{},objects:{}});
  assert.equal(E.combine(E.operation(start,one),E.operation({...one,notes:'伙伴'},two)),null);
});

test('old whole-object undo records remain compatible with granular peer preservation',()=>{
  const before=wavePlan(),after=structuredClone(before);after.objects[0].name='回抽';
  const legacy={fields:{},objects:{wave:{before:before.objects[0],after:after.objects[0]}}};
  const current=structuredClone(after);current.objects[0].color='#0f0';
  const undo=E.apply(current,legacy);assert.deepEqual(undo.conflicts,[]);
  assert.equal(undo.plan.objects[0].name,'预期走势');assert.equal(undo.plan.objects[0].color,'#0f0');
});

test('derived sequence labels do not create an operation',()=>{
  const before=wavePlan(),after=structuredClone(before);point(after,'b').label='99';
  assert.deepEqual(E.operation(before,after),{fields:{},objects:{}});
});
