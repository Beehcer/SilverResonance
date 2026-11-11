'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const Symbols=require('./static/workspace-symbols.js');

test('favorites stay first and both groups follow the saved order independently',()=>{
  const symbols=['rb','ag','au','rm','fg'];
  assert.deepEqual(Symbols.sorted(symbols,['ag','rm'],['fg','rm','rb','ag','au']),['rm','ag','fg','rb','au']);
  assert.deepEqual(Symbols.sorted(symbols,new Set(['ag','rm']),[]),['ag','rm','rb','au','fg']);
});

test('new contracts append stably inside their group and stale or repeated saved IDs are harmless',()=>{
  assert.deepEqual(Symbols.sorted(['rb','ag','au','rm','fg','sa'],['ag','sa'],['expired','fg','ag','fg','rb']),['ag','sa','fg','rb','au','rm']);
  assert.deepEqual(Symbols.sorted(['rb','ag','au'],[],[]),['rb','ag','au']);
});

test('sorting keeps original object references and does not mutate symbols, favorites or saved order',()=>{
  const rb=Object.freeze({symbol:'rb',name:'螺纹'}),ag=Object.freeze({symbol:'ag',name:'白银'}),au=Object.freeze({symbol:'au',name:'黄金'});
  const symbols=Object.freeze([rb,ag,au]),favorites=Object.freeze(['ag']),order=Object.freeze(['au','rb','ag']);
  const result=Symbols.sorted(symbols,favorites,order);assert.deepEqual(result,[ag,au,rb]);assert.equal(result[0],ag);assert.equal(result[1],au);
  assert.deepEqual(Symbols.move(symbols,favorites,order,'rb','au',true),['ag','rb','au']);
  assert.deepEqual(symbols,[rb,ag,au]);assert.deepEqual(favorites,['ag']);assert.deepEqual(order,['au','rb','ag']);
});

test('move handles before and after in both directions without off-by-one target shifts',()=>{
  const symbols=['a','b','c','d'];
  assert.deepEqual(Symbols.move(symbols,[],[],'a','c',true),['b','a','c','d']);
  assert.deepEqual(Symbols.move(symbols,[],[],'a','c',false),['b','c','a','d']);
  assert.deepEqual(Symbols.move(symbols,[],[],'d','b',true),['a','d','b','c']);
  assert.deepEqual(Symbols.move(symbols,[],[],'d','b',false),['a','b','d','c']);
  assert.deepEqual(Symbols.move(symbols,[],[],'c','d',false),['a','b','d','c']);
});

test('favorite rows can reorder while nonfavorite rows and membership remain unchanged',()=>{
  const symbols=['rb','ag','au','rm','fg'],favorites=['ag','rm','au'];
  const order=Symbols.move(symbols,favorites,[],'rm','ag',true);
  assert.deepEqual(order,['rm','ag','au','rb','fg']);assert.deepEqual(favorites,['ag','rm','au']);
  assert.deepEqual(Symbols.sorted(symbols,favorites,order),order);
  assert.deepEqual(Symbols.move(symbols,favorites,order,'rb','fg',false),['rm','ag','au','fg','rb']);
});

test('cross-group drops do not change favorites or reorder either group',()=>{
  const symbols=['rb','ag','au','rm'],favorites=['ag','rm'],order=['rm','ag','au','rb'];
  for(const before of [true,false]){
    assert.deepEqual(Symbols.move(symbols,favorites,order,'rb','ag',before),order);
    assert.deepEqual(Symbols.move(symbols,favorites,order,'rm','au',before),order);
  }
  assert.deepEqual(favorites,['ag','rm']);
});

test('dragging visible search matches preserves hidden contracts and survives quote replacement',()=>{
  const symbols=[{symbol:'ag',price:10},{symbol:'au',price:20},{symbol:'rb',price:30},{symbol:'rm',price:40}],favorites=['ag'];
  // Only rb and au might be shown by a filter; move still receives the complete list.
  const order=Symbols.move(symbols,favorites,[],'rb','au',true);
  assert.deepEqual(order,['ag','rb','au','rm']);
  const fresh=[{symbol:'rm',price:41},{symbol:'rb',price:31},{symbol:'au',price:21},{symbol:'ag',price:11}];
  const shown=Symbols.sorted(fresh,favorites,order);assert.deepEqual(shown.map(item=>item.symbol),order);assert.deepEqual(shown.map(item=>item.price),[11,31,21,41]);
});

test('favoriting after a manual reorder keeps favorite priority without discarding stored relative order',()=>{
  const symbols=['rb','ag','au','rm'],order=Symbols.move(symbols,[],[],'rm','rb',true);
  assert.deepEqual(order,['rm','rb','ag','au']);
  assert.deepEqual(Symbols.sorted(symbols,['au','rm'],order),['rm','au','rb','ag']);
  assert.deepEqual(Symbols.sorted(symbols,[],order),order);
});

test('missing drag targets are safe and malformed or duplicate symbol records never add phantom rows',()=>{
  const rb={symbol:'rb',name:'first'},symbols=[rb,'ag',{symbol:'rb',name:'duplicate'},null,{},42,''];
  assert.deepEqual(Symbols.sorted(symbols,['missing'],['ghost','rb','rb']),[rb,'ag']);
  for(const [source,target]of [['missing','rb'],['rb','missing'],['rb','rb'],[null,'ag']])assert.deepEqual(Symbols.move(symbols,[],[],source,target),['rb','ag']);
  assert.deepEqual(Symbols.sorted(null),[]);assert.deepEqual(Symbols.move([],[],[],'a','b'),[]);
});
