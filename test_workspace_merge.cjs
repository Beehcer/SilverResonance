'use strict';
const assert=require('node:assert/strict');
const M=require('./static/workspace-merge.js');
const copy=value=>JSON.parse(JSON.stringify(value));
const point=(id,price)=>({id,time:1700000000000+price*60000,price,label:id});
const wave={id:'wave',kind:'wave',period:'m30',color:'#ff0000',points:[point('a',100),point('b',110),point('c',105)]};
const base={notes:'initial',objects:[wave]};
{
  const mine=copy(base),peer=copy(base);mine.objects[0].color='#00ff00';peer.objects[0].name='Peer';peer.objects[0].points[1].price=120;
  const merged=M.apply(peer,M.diff(base,mine));assert.equal(merged.objects[0].name,'Peer');assert.equal(merged.objects[0].color,'#00ff00');assert.equal(merged.objects[0].points[1].price,120);
}
{
  const mine=copy(base),peer=copy(base);mine.objects[0].points[0].price=99;peer.objects[0].points[1].price=121;
  const merged=M.apply(peer,M.diff(base,mine));assert.deepEqual(merged.objects[0].points.map(p=>p.price),[99,121,105]);
  peer.objects[0].points.splice(0,1);assert.equal(M.apply(peer,M.diff(base,mine)).objects[0].points.some(p=>p.id==='a'),false);
  peer.objects=[];assert.deepEqual(M.apply(peer,M.diff(base,mine)).objects,[]);
}
{
  const mine=copy(base),peer=copy(base);mine.objects[0].points.splice(1,0,point('mine',108));peer.objects[0].points.splice(1,0,point('peer',107));
  const merged=M.apply(peer,M.diff(base,mine));assert.deepEqual(merged.objects[0].points.map(p=>p.id),['a','peer','mine','b','c']);assert.deepEqual(merged.objects[0].points.map(p=>p.label),['1','2','3','4','5']);
}
{
  const sentLocal=copy(base);sentLocal.objects[0].points[0].price=99;const sent=M.diff(base,sentLocal),currentLocal=copy(sentLocal);currentLocal.objects[0].points[0].price=98;currentLocal.objects[0].color='#00ff00';
  const remaining=M.ack(M.diff(base,currentLocal),sent,base);assert.equal(remaining.object_edits[0].node_updates[0].changes.price,98);assert.equal(remaining.object_edits[0].changes.color,'#00ff00');assert.ok(M.equal(M.apply(M.apply(base,sent),remaining),M.apply(base,M.diff(base,currentLocal))));
}
{
  const created=copy(base);created.objects.push({...copy(wave),id:'new'});const sent=M.diff(base,created),remaining=M.ack(M.diff(base,base),sent,base);assert.deepEqual(remaining.delete_object_ids,['new']);assert.equal(M.apply(created,remaining).objects.length,1);
  const edited=copy(created);edited.objects[1].color='#00ff00';const residual=M.ack(M.diff(base,edited),sent,base);assert.equal(residual.upsert_objects.length,0);assert.equal(residual.object_edits[0].changes.color,'#00ff00');
}
{
  const created=copy(base);created.objects[0].points.splice(1,0,point('new',107));const sent=M.diff(base,created);const cancel=M.ack(M.diff(base,base),sent,base);assert.deepEqual(cancel.object_edits[0].node_deletes,['new']);
  const edited=copy(created);edited.objects[0].points[1].price=109;const residual=M.ack(M.diff(base,edited),sent,base);assert.equal(residual.object_edits[0].node_inserts.length,0);assert.equal(residual.object_edits[0].node_updates[0].changes.price,109);
}
{
  const mine=copy(base);mine.objects[0].z_index=3.5;mine.objects[0].source_period='day';const pending=M.diff(base,mine);assert.equal(pending.object_edits[0].changes.z_index,3.5);assert.equal(pending.object_edits[0].changes.source_period,'day');assert.equal(M.hasPending(M.ack(pending,pending,base)),false);
}
console.log('workspace merge: property/node merging, delete-wins, concurrent insertion and in-flight acknowledgements passed');
