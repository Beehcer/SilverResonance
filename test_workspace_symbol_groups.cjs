'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const Symbols=require('./static/workspace-symbols.js');

const entries=[
  {symbol:'SHFE.ag2612',name:'沪银2612'},
  {symbol:'CZCE.MA701',name:'甲醇701'},
  {symbol:'DCE.m2701',name:'豆粕2701'},
  {symbol:'SHFE.bu2612',name:'沥青2612'},
  {symbol:'GFEX.lc2701',name:'碳酸锂2701'},
  {symbol:'SHFE.rb2701',name:'螺纹钢2701'},
];
const ids=list=>list.map(item=>item.symbol);

test('product categories use exact exchange and code, including the requested lithium carbonate',()=>{
  const examples={precious:['SHFE.ag2612','SHFE.au2612','GFEX.pt2701','GFEX.pd2701'],
    energy:['SHFE.bu2612','DCE.jm2701','INE.sc2612'],
    chemical:['CZCE.SA701','CZCE.FG701','CZCE.MA701','CZCE.TA701','SHFE.sp2705','DCE.l2701','DCE.pp2701','GFEX.lc2701'],
    agriculture:['CZCE.CF701','CZCE.RM701','CZCE.AP701','DCE.m2701','DCE.p2701','DCE.jd2701']};
  for(const [group,symbols]of Object.entries(examples))for(const symbol of symbols)assert.equal(Symbols.category(symbol),group,symbol);
  assert.equal(Symbols.category('gfex.LC2701'),'chemical');
  assert.equal(Symbols.category('KQ.m@SHFE.au'),'precious');
  assert.equal(Symbols.category({symbol:'SHFE.rb2701',name:'黄金模拟'}),'other');
});

test('unknown, option-like and partial identifiers are not misclassified by substring or inherited properties',()=>{
  for(const symbol of ['SHFE.agreement','DCE.ag2612','SHFE.ag2612C7000','GFEX.lc2799','constructor','SHFE.constructor','toString',null,{},42])assert.equal(Symbols.category(symbol),'other');
  assert.equal(Symbols.category('SHFE.ss2701'),'other');
});

test('first use preserves legacy favorites and the visible legacy list order',()=>{
  const groups=Symbols.normalizeGroups(null,entries,['CZCE.MA701','SHFE.ag2612','gone'],['GFEX.lc2701','CZCE.MA701','SHFE.bu2612','SHFE.ag2612']);
  assert.deepEqual(groups,{version:1,default:['CZCE.MA701','SHFE.ag2612','GFEX.lc2701','SHFE.bu2612','DCE.m2701','SHFE.rb2701'],favorites:['CZCE.MA701','SHFE.ag2612']});
});

test('saved empty custom groups stay empty and new products are discoverable without being added to a personal list',()=>{
  const raw={version:1,default:[],favorites:['SHFE.ag2612']};
  const result=Symbols.normalizeGroups(raw,entries,['CZCE.MA701']);
  assert.deepEqual(result,raw);
  assert.deepEqual(Symbols.groupItems(entries,result,'default'),[]);
  assert.deepEqual(ids(Symbols.groupItems(entries,result,'chemical')),['CZCE.MA701','GFEX.lc2701']);
  assert.deepEqual(ids(Symbols.groupItems(entries,result,'all')),ids(entries));
});

test('rollover migrates one matching product on the same exchange while preserving custom membership and order',()=>{
  const raw={version:1,default:['SHFE.ag2612','CZCE.MA701','DCE.m2701'],favorites:['CZCE.MA701','SHFE.ag2612']};
  const current=[{symbol:'GFEX.lc2701'},{symbol:'DCE.m2705'},{symbol:'SHFE.ag2706'},{symbol:'CZCE.MA705'}];
  const result=Symbols.normalizeGroups(raw,current);
  assert.deepEqual(result,{version:1,default:['SHFE.ag2706','CZCE.MA705','DCE.m2705'],favorites:['CZCE.MA705','SHFE.ag2706']});
  assert.deepEqual(raw.default,['SHFE.ag2612','CZCE.MA701','DCE.m2701']);
});

test('rollover never guesses between multiple contracts or crosses exchange, and existing exact contracts win',()=>{
  const groups={version:1,default:['SHFE.ag2612','CZCE.MA701','DCE.m2701'],favorites:['SHFE.ag2612']};
  const current=['SHFE.ag2706','SHFE.ag2712','DCE.MA2701','DCE.m2701','DCE.m2705'];
  assert.deepEqual(Symbols.normalizeGroups(groups,current),{version:1,default:['DCE.m2701'],favorites:[]});
  const exact=Symbols.normalizeGroups(groups,[...current,'SHFE.ag2612']);
  assert.deepEqual(exact.default,['SHFE.ag2612','DCE.m2701']);
  assert.deepEqual(exact.favorites,['SHFE.ag2612']);
});

test('normalization removes stale and duplicate values including duplicates created by rollover',()=>{
  const raw={version:1,default:['SHFE.ag2612','expired','SHFE.ag2706','SHFE.ag2612',null],favorites:'invalid'};
  assert.deepEqual(Symbols.normalizeGroups(raw,['SHFE.ag2706']),{version:1,default:['SHFE.ag2706'],favorites:[]});
  assert.deepEqual(Symbols.normalizeGroups(raw,null),{version:1,default:[],favorites:[]});
});

test('search stays within the selected group, preserves order and original quote objects',()=>{
  const groups={version:1,default:['GFEX.lc2701','CZCE.MA701','SHFE.ag2612'],favorites:['SHFE.ag2612','CZCE.MA701']};
  const shown=Symbols.groupItems(entries,groups,'default','  701 ');
  assert.deepEqual(ids(shown),['GFEX.lc2701','CZCE.MA701']);assert.equal(shown[0],entries[4]);
  assert.deepEqual(Symbols.groupItems(entries,groups,'favorites','碳酸锂'),[]);
  assert.deepEqual(Symbols.groupItems(entries,groups,'chemical','碳酸锂'),[entries[4]]);
  assert.deepEqual(Symbols.groupItems(entries,groups,'precious','sHfE.AG'),[entries[0]]);
  assert.deepEqual(Symbols.groupItems(entries,groups,'all','螺纹钢'),[entries[5]]);
});

test('adding and removing is independent for the two custom groups and does not mutate the saved input',()=>{
  const groups=Object.freeze({version:1,default:Object.freeze(['SHFE.ag2612','CZCE.MA701']),favorites:Object.freeze(['SHFE.ag2612'])});
  const favorite=Symbols.addToGroup(groups,'favorites','CZCE.MA701');
  assert.deepEqual(favorite,{version:1,default:['SHFE.ag2612','CZCE.MA701'],favorites:['SHFE.ag2612','CZCE.MA701']});
  assert.deepEqual(Symbols.addToGroup(favorite,'favorites','CZCE.MA701'),favorite);
  assert.deepEqual(Symbols.removeFromGroup(favorite,'default','CZCE.MA701'),{version:1,default:['SHFE.ag2612'],favorites:['SHFE.ag2612','CZCE.MA701']});
  assert.deepEqual(groups.favorites,['SHFE.ag2612']);
});

test('reordering a filtered custom list preserves hidden members and never changes the other list',()=>{
  const groups={version:1,default:['SHFE.ag2612','CZCE.MA701','DCE.m2701','GFEX.lc2701'],favorites:['CZCE.MA701','SHFE.ag2612']};
  const changed=Symbols.moveInGroup(groups,'default','GFEX.lc2701','CZCE.MA701',true);
  assert.deepEqual(changed.default,['SHFE.ag2612','GFEX.lc2701','CZCE.MA701','DCE.m2701']);
  assert.deepEqual(changed.favorites,groups.favorites);
  const refreshed=entries.map(entry=>({...entry,price:10})).reverse();
  assert.deepEqual(ids(Symbols.groupItems(refreshed,changed,'default')),changed.default);
  const last=Symbols.moveInGroup(changed,'default','SHFE.ag2612','DCE.m2701',false);
  assert.deepEqual(last.default,['GFEX.lc2701','CZCE.MA701','DCE.m2701','SHFE.ag2612']);
});

test('invalid group mutations and missing drag targets cannot alter fixed categories or create phantom members',()=>{
  const groups={version:1,default:['SHFE.ag2612','CZCE.MA701'],favorites:[]};
  for(const group of ['all','energy','chemical','agriculture','precious','__proto__']){
    assert.deepEqual(Symbols.addToGroup(groups,group,'GFEX.lc2701'),groups);
    assert.deepEqual(Symbols.removeFromGroup(groups,group,'SHFE.ag2612'),groups);
    assert.deepEqual(Symbols.moveInGroup(groups,group,'CZCE.MA701','SHFE.ag2612'),groups);
  }
  for(const [from,to]of [['missing','SHFE.ag2612'],['SHFE.ag2612','missing'],['SHFE.ag2612','SHFE.ag2612']])assert.deepEqual(Symbols.moveInGroup(groups,'default',from,to),groups);
});
