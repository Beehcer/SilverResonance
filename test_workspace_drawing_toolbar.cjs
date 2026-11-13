'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/workspace.js','utf8');
function helper(name){const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));assert.ok(line);if(line.trimEnd().endsWith('}'))return line;const start=source.indexOf(line);return source.slice(start,source.indexOf('\n  }',start)+4);}
function harness(){const fields={'draw-color':{value:'#12abef'},'draw-width':{value:'4'},'draw-style':{value:'dashed'},'chart-help':{}},tools=[],styles=[],context=vm.createContext({state:{},$:id=>{assert.notEqual(id,'draw-opacity','removed input must never be read');return fields[id];},charts:{day:{setTool:(...args)=>tools.push(args),setDrawingStyle:patch=>styles.push(patch)}},document:{querySelectorAll:()=>[]},finishDrawings(){},stopFollowing(){},renderQuickPlan(){}});for(const name of ['drawingOpacity','chooseTool','syncDrawingStyle'])vm.runInContext(helper(name),context);return{context,fields,tools,styles};}

test('new drawing uses the visible toolbar style and full opacity without reading the removed input',()=>{
  const h=harness();h.context.chooseTool('reference');assert.equal(h.tools[0][0],'reference');assert.deepEqual(JSON.parse(JSON.stringify(h.tools[0][1])),{color:'#12abef',width:4,line_style:'dashed',opacity:1});
  h.context.chooseTool('wave',{color:'#22c55e',name:'预期走势'});assert.equal(h.tools[1][1].color,'#22c55e');assert.equal(h.tools[1][1].name,'预期走势');
});

test('changing one toolbar style does not overwrite another option or modify existing drawing objects',()=>{
  const h=harness();h.context.syncDrawingStyle({target:{id:'draw-width',value:'6'}});h.context.syncDrawingStyle({target:{id:'draw-style',value:'dotted'}});h.context.syncDrawingStyle({target:{id:'draw-color',value:'#ffaa00'}});
  assert.deepEqual(JSON.parse(JSON.stringify(h.styles)),[{width:6},{line_style:'dotted'},{color:'#ffaa00'}]);assert.equal(h.tools.length,0);
  h.context.state.viewOnly=true;h.context.syncDrawingStyle({target:{id:'draw-width',value:'2'}});assert.equal(h.styles.length,3);
});
