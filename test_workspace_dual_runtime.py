"""Production pane integration preserves data and runtime interaction safety."""

import json
from pathlib import Path
import subprocess
import unittest

import workspace_runtime as runtime_adapter


ROOT = Path(__file__).resolve().parent


def run_javascript(source):
    result = subprocess.run(["node", "-"], input=source, text=True, encoding="utf-8", capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)


class RuntimeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
        cls.adapted = runtime_adapter.adapt_workspace_js(cls.source)

    def test_production_storage_namespace_and_market_semantics_are_preserved(self):
        self.assertIn("const PREFIX='resonance-workspace-v1:'", self.adapted)
        self.assertNotIn('resonance-full-dual-preview-v1:', self.adapted)
        self.assertNotIn('migration:empty-custom-groups', self.adapted)
        self.assertNotIn('GROUPS_BOOTSTRAP', Path(runtime_adapter.__file__).read_text(encoding='utf-8'))
        self.assertIn("m.source==='mock'?'模拟 · 5秒刷新'", self.adapted)
        self.assertIn("e.source==='live'&&m?.source==='live'", self.adapted)

    def test_unknown_source_shape_fails_instead_of_serving_partial_hooks(self):
        with self.assertRaisesRegex(ValueError, 'dual-pane integration anchor changed'):
            runtime_adapter.adapt_workspace_js(self.source.replace('  const state={', '  const state = {'))
        chart = (ROOT / 'static' / 'workspace-chart.js').read_text(encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'dual-pane anchor changed'):
            runtime_adapter.adapt_workspace_chart_js(chart.replace("if(this.tool==='pan'){", "if (this.tool==='pan') {"))

    def test_pane_layout_keys_do_not_move_existing_drafts_or_personal_groups(self):
        functions = '\n'.join(line for line in self.adapted.splitlines() if line.strip().startswith(('function storageGet(', 'function storageSet(')))
        run_javascript("""
const assert=require('node:assert/strict');
const PREFIX='resonance-workspace-v1:',data=new Map();
const localStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};
const make=new Function('WORKSPACE_PANE','localStorage','PREFIX','notice',""" + json.dumps(functions + '\nreturn {get:storageGet,set:storageSet};') + """);
const a=make('pane-a',localStorage,PREFIX,()=>{}),b=make('pane-b',localStorage,PREFIX,()=>{});
const draft={pending:{changes:{notes:'待同步'}},plan:{symbol:'CZCE.MA701'}};
const groups={version:1,default:['CZCE.MA701'],favorites:['SHFE.ag2702']};
data.set(PREFIX+'layout',JSON.stringify('daily'));
data.set(PREFIX+'draft:user:CZCE.MA701:',JSON.stringify(draft));
data.set(PREFIX+'symbol-groups:user',JSON.stringify(groups));
a.set('layout','main');b.set('layout','columns');
assert.equal(JSON.parse(data.get(PREFIX+'layout')),'daily');
assert.deepEqual(a.get('draft:user:CZCE.MA701:'),draft);
assert.deepEqual(b.get('symbol-groups:user'),groups);
assert.equal(a.get('layout'),'main');assert.equal(b.get('layout'),'columns');
assert.equal(data.size,5);
""")

    def test_only_active_pane_emits_presence_without_stopping_passive_market_poll(self):
        start = self.adapted.index('  function sendPresence(')
        end = self.adapted.index('  function emptyPending(', start)
        function = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
let active=false;const sent=[];
const window={FullWorkspace:{isActive:()=>active}},state={symbol:'CZCE.MA701',session:{user:{id:'shared'}},active:'m30'};
const charts={m30:{period:'m30',view:{start:1,end:10}}},presenceSender={push:(value,force)=>sent.push({value,force})};
const renderEvaluation=()=>{};
""" + function + """
sendPresence({cursor:{time:1,price:3000}},true);assert.equal(sent.length,0);assert.equal(state.localPresence,undefined);
active=true;sendPresence({cursor:{time:2,price:3100}},true);
assert.equal(sent.length,1);assert.equal(sent[0].value.symbol,'CZCE.MA701');
assert.equal(sent[0].value.cursor.price,3100);
""")
        start = self.adapted.index('  async function poll(')
        end = self.adapted.index('  function renderMarket(', start)
        self.assertNotIn('isActive()', self.adapted[start:end])

    def test_entry_defaults_all_and_empty_groups_but_preserves_later_membership(self):
        start = self.adapted.index("  function symbolGroups(){")
        end = self.adapted.index("  function customSymbolGroup", start)
        function = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const WorkspaceSymbols=require(""" + json.dumps(str(ROOT / "static" / "workspace-symbols.js")) + """);
const data=new Map([['symbol-group:user','favorites']]);
const state={session:{user:{id:'user'}},symbols:[{symbol:'CZCE.MA701'},{symbol:'SHFE.ag2702'}],symbolGroup:null,viewOnly:false};
const storageGet=(key,fallback=null)=>data.has(key)?data.get(key):fallback;
const storageSet=(key,value)=>data.set(key,value);
const symbolGroupsKey=()=>'symbol-groups:user';
""" + function + """
assert.deepEqual(symbolGroups(),{version:1,default:[],favorites:[]});
assert.equal(state.symbolGroup,'all');
data.set('symbol-groups:user',{version:1,default:['CZCE.MA701'],favorites:['SHFE.ag2702']});
state.symbolGroups=null;state.symbolGroupScope=null;state.symbolGroup=null;
assert.deepEqual(symbolGroups(),{version:1,default:['CZCE.MA701'],favorites:['SHFE.ag2702']});
assert.equal(state.symbolGroup,'all');
state.symbolGroup='favorites';state.symbolGroups=null;state.symbolGroupScope=null;
symbolGroups();assert.equal(state.symbolGroup,'favorites');
""")

    def test_empty_group_has_no_redundant_all_button_and_runtime_still_parses(self):
        self.assertNotIn("quote-empty-action", self.adapted)
        result = subprocess.run(["node", "--check", "-"], input=self.adapted, text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deactivation_finishes_paths_before_using_the_original_tool_selector(self):
        start = self.adapted.index("    prepareDeactivate:async")
        end = self.adapted.index("    becameActive:", start)
        method = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const calls=[];
const state={viewOnly:false,planFormDirty:true,quickTool:'wave',spaceTool:{}};
const charts={day:{cancelGesture:()=>calls.push('cancel-day')},m30:{cancelGesture:()=>calls.push('cancel-m30')}};
const savePlanForm=()=>calls.push('save-form');
const finishDrawings=()=>calls.push('finish-path');
const chooseTool=tool=>calls.push('choose-'+tool);
const closeObjectContext=()=>{},saveViews=()=>{},persistDraft=()=>{},renderQuickPlan=()=>{},renderDrawingState=()=>{},sendPresence=()=>{};
const ensureSaved=async()=>{calls.push('ensure-saved');return false;};
const bridge={
""" + method + """
};
(async()=>{
  assert.equal(await bridge.prepareDeactivate({closing:true}),false);
  assert.deepEqual(calls,['save-form','finish-path','cancel-day','cancel-m30','choose-select','ensure-saved']);
  assert.equal(state.quickTool,null);
  assert.equal(state.spaceTool,null);
  calls.length=0;state.planFormDirty=false;
  assert.equal(await bridge.prepareDeactivate(),true);
  assert.deepEqual(calls,['finish-path','cancel-day','cancel-m30','choose-select']);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_unsubmitted_text_or_composition_blocks_switch_without_touching_the_draft(self):
        start = self.adapted.index("    prepareDeactivate:async")
        end = self.adapted.index("    becameActive:", start)
        method = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const calls=[];
const state={viewOnly:false,planFormDirty:true,quickTool:'text'};
const chart={textEditor:{hidden:false},textInput:{value:'尚未添加的文本',focus:()=>calls.push('focus')},textComposing:false,cancelGesture:()=>calls.push('cancel')};
const charts={m30:chart};
const notice=text=>calls.push(text);
const savePlanForm=()=>calls.push('save-form'),finishDrawings=()=>calls.push('finish-path'),chooseTool=()=>calls.push('choose-tool');
const closeObjectContext=()=>{},saveViews=()=>{},persistDraft=()=>{},renderQuickPlan=()=>{},renderDrawingState=()=>{},sendPresence=()=>{};
const ensureSaved=async()=>{calls.push('ensure-saved');return true;};
const bridge={
""" + method + """
};
(async()=>{
  assert.equal(await bridge.prepareDeactivate({closing:true}),false);
  assert.deepEqual(calls,['这段文本尚未添加，请先添加或取消。','focus']);
  assert.equal(chart.textInput.value,'尚未添加的文本');
  assert.equal(chart.textEditor.hidden,false);
  assert.equal(state.quickTool,'text');
  calls.length=0;chart.textInput.value='';chart.textComposing=true;
  assert.equal(await bridge.prepareDeactivate(),false);
  assert.deepEqual(calls,['这段文本尚未添加，请先添加或取消。','focus']);
  calls.length=0;chart.textComposing=false;
  assert.equal(await bridge.prepareDeactivate(),true);
  assert.deepEqual(calls,['save-form','finish-path','cancel','choose-tool']);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_click_and_pointer_jitter_keep_following_but_a_real_pan_fixes_the_view(self):
        chart_source = (ROOT / "static" / "workspace-chart.js").read_text(encoding="utf-8")
        adapted = runtime_adapter.adapt_workspace_chart_js(chart_source)
        run_javascript("""
const assert=require('node:assert/strict'),vm=require('node:vm');
const sandbox={module:{exports:{}}};vm.runInNewContext(""" + json.dumps(adapted) + """,sandbox);
const Chart=sandbox.module.exports.Chart;
function chart(tool='select'){
  const value=Object.create(Chart.prototype);
  Object.assign(value,{tool,view:{start:0,end:100,min:0,max:100},tx:{},plot:{x:0,y:0,w:100,h:100},period:'m30',bars:[],tick:1,followLatest:true,viewCustomized:false,autoPrice:true,plan:{objects:[]},path:[],changes:0,
    host:{style:{},clientWidth:100,focus(){}},svg:{setPointerCapture(){}},callbacks:{viewChanged:()=>value.changes++},
    point:e=>({x:e.clientX,y:e.clientY}),levelGeometry:()=>[],toValue:p=>({time:p.x,price:p.y}),hit:()=>null,hitPathControl:()=>null,editableTitleAt:()=>null,render(){},scheduleRender(){},releasePointer(){},clearPresence(){},applyPendingMarket(){},priceFit:v=>v,getTimeScale:()=>({times:[],position:v=>v,time:v=>v}),zoomLimit:()=>10000});
  return value;
}
const down={button:0,pointerId:1,clientX:50,clientY:50,timeStamp:100,preventDefault(){}};
for(const tool of ['select','pan']){
  const c=chart(tool);c.down(down);assert.equal(c.followLatest,true);
  c.move({...down,clientX:51});assert.equal(c.followLatest,true);assert.equal(c.changes,0);
  c.up({...down,clientX:51});assert.equal(c.followLatest,true);assert.equal(c.changes,0);
  c.down({...down,timeStamp:1000});c.move({...down,clientX:56});
  assert.equal(c.followLatest,false);assert.equal(c.changes,1);
  c.cancelGesture();assert.equal(c.followLatest,true);assert.equal(c.viewCustomized,false);assert.equal(c.autoPrice,true);
  c.down({...down,timeStamp:2000});c.move({...down,clientX:56});c.up({...down,clientX:56});assert.equal(c.followLatest,false);
}
const zoomed=chart();zoomed.zoom(.9);assert.equal(zoomed.viewCustomized,true);
""")

    def test_panes_keep_separate_layouts_and_restore_symbol_view_controls(self):
        storage_functions = "\n".join(line for line in self.adapted.splitlines() if line.strip().startswith(("function storageGet(", "function storageSet(")))
        start = self.adapted.index("  function dualViewKey()")
        end = self.adapted.index("  function renderFollowState()", start)
        view_functions = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const data=new Map(),localStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};
const makeStorage=new Function('WORKSPACE_PANE','localStorage','PREFIX','notice',""" + json.dumps(storage_functions + "\nreturn {get:storageGet,set:storageSet};") + """);
const a=makeStorage('pane-a',localStorage,'preview:',()=>{}),b=makeStorage('pane-b',localStorage,'preview:',()=>{});
a.set('layout','columns');b.set('layout','main');assert.equal(a.get('layout'),'columns');assert.equal(b.get('layout'),'main');
const WorkspaceLayouts={get:()=>({periods:['day','m30','m3']})},E={equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b)};
const create=new Function('WORKSPACE_PANE','state','document','storageGet','storageSet','E','WorkspaceLayouts','marketComplete','activate','renderFollowState',""" + json.dumps(view_functions + "\nreturn {key:dualViewKey,save:saveDualViewUI,restore:restoreDualViewUI};") + """);
function session(pane,storage,symbol){const state={session:{user:{id:'user'}},symbol,active:'m30',layout:storage.get('layout'),loading:false},grid={dataset:{}};const api=create(pane,state,{querySelector:()=>grid},storage.get,storage.set,E,WorkspaceLayouts,()=>true,period=>state.active=period,()=>{});return{state,grid,api};}
const first=session('pane-a',a,'CZCE.MA701');
a.set(first.api.key(),{activePeriod:'day',expanded:'day'});
first.api.save();assert.deepEqual(a.get(first.api.key()),{activePeriod:'day',expanded:'day'});
first.api.restore();assert.equal(first.state.active,'day');assert.equal(first.grid.dataset.expanded,'day');
const second=session('pane-b',b,'CZCE.MA701');second.api.restore();assert.equal(second.state.active,'m30');assert.equal(second.grid.dataset.expanded,undefined);
first.state.active='m3';delete first.grid.dataset.expanded;first.api.save();
const refreshed=session('pane-a',a,'CZCE.MA701');refreshed.api.restore();assert.equal(refreshed.state.active,'m3');assert.equal(refreshed.grid.dataset.expanded,undefined);
const other=session('pane-a',a,'SHFE.ag2702');other.api.restore();assert.equal(other.state.active,'m30');
""")

    def test_text_cancel_and_click_tools_release_pointer_before_switching_panes(self):
        chart_source = (ROOT / "static" / "workspace-chart.js").read_text(encoding="utf-8")
        adapted_chart = runtime_adapter.adapt_workspace_chart_js(chart_source)
        start = self.adapted.index("  function dualTextDraft()")
        end = self.adapted.index("  function cancelActiveTool()", start)
        helpers = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict'),vm=require('node:vm');
function element(tag){return{tagName:tag.toUpperCase(),style:{},hidden:false,value:'',children:[],listeners:{},append(...nodes){this.children.push(...nodes);},setAttribute(){},addEventListener(name,fn){this.listeners[name]=fn;},focus(){}};}
const document={createElement:element,querySelector:()=>null,activeElement:null},sandbox={module:{exports:{}},document};
vm.runInNewContext(""" + json.dumps(adapted_chart) + """,sandbox);
const Chart=sandbox.module.exports.Chart,added=[];
function chart(tool){
  const value=Object.create(Chart.prototype),captured=new Set();
  Object.assign(value,{tool,view:{start:0,end:100,min:0,max:100},tx:{},plot:{x:0,y:0,w:100,h:100},period:'m30',bars:[],tick:1,followLatest:true,viewCustomized:false,autoPrice:true,plan:{objects:[]},path:[],drag:null,
    host:{style:{},clientWidth:100,clientHeight:100,focus(){},append(){}},
    svg:{setPointerCapture:id=>captured.add(id),hasPointerCapture:id=>captured.has(id),releasePointerCapture:id=>captured.delete(id)},captured,
    callbacks:{add:object=>added.push(object)},point:e=>({x:e.clientX,y:e.clientY}),levelGeometry:()=>[],toValue:p=>({time:p.x,price:p.y}),hit:()=>null,hitPathControl:()=>null,editableTitleAt:()=>null,
    render(){},scheduleRender(){},clearPresence(){},applyPendingMarket(){},clearTouchGesture(){}});
  return value;
}
const state={viewOnly:false},charts={m30:chart('text')},E={isTextTarget:()=>false};
""" + helpers + """
const down={button:0,pointerId:7,clientX:50,clientY:50,timeStamp:100,preventDefault(){}};
const text=charts.m30;text.down(down);
assert.equal(text.textEditor.hidden,false);assert.equal(text.capturedPointerId,null);assert.equal(text.captured.size,0);
text.textInput.value='尚未提交的标记';assert.equal(dualCanChangeFocus(),false);
text.textEditor.children.find(node=>node.textContent==='取消').listeners.click();
text.setTool('select');assert.equal(text.textEditor.hidden,true);assert.equal(added.length,0);
assert.equal(dualCanChangeFocus(),true,'Canceling the text must permit adding or switching a pane, even if pointerup went to the editor');
text.up(down);assert.equal(dualCanChangeFocus(),true);
const horizontal=chart('horizontal');horizontal.down(down);
assert.equal(added.length,1);assert.equal(horizontal.capturedPointerId,null);assert.equal(horizontal.captured.size,0);
// A non-drag click must clear only its own pointer-up record.
const clicked=chart('select');clicked.hit=()=>({object:{id:'locked',kind:'horizontal',locked:true}});clicked.down(down);
assert.equal(clicked.drag,null);assert.equal(clicked.capturedPointerId,7);
clicked.up({...down,pointerId:8});assert.equal(clicked.capturedPointerId,7);assert.equal(clicked.captured.has(7),true);
clicked.up(down);assert.equal(clicked.capturedPointerId,null);assert.equal(clicked.captured.size,0);
// Explicit cancellation also recovers a stale, already released browser capture.
clicked.capturedPointerId=7;clicked.cancelGesture();assert.equal(clicked.capturedPointerId,null);
""")

    def test_slow_saves_keep_market_updates_without_promoting_review_freshness(self):
        start = self.adapted.index("  async function refreshWhileSaving(")
        end = self.adapted.index("  async function poll(", start)
        helper = self.adapted[start:end]
        self.assertIn("if(state.saving){await refreshWhileSaving(generation);return;}", self.adapted)
        run_javascript("""
const assert=require('node:assert/strict');
const state={symbol:'CZCE.MA701',generation:1,lastSuccess:123,market:{symbol:'CZCE.MA701'}},updates=[];
let finish,requests=0;
const refreshMarket=()=>{requests++;return new Promise(resolve=>finish=resolve);};
const applyActiveMarket=market=>updates.push(market),renderLoadState=()=>{},setUnavailable=()=>{};
""" + helper + """
(async()=>{
  const first=refreshWhileSaving(1);await refreshWhileSaving(1);assert.equal(requests,1);
  finish({symbol:'CZCE.MA701',price:3184});await first;assert.equal(updates.length,1);assert.equal(state.lastSuccess,123);assert.equal(state.dualMarketRequest,null);
  const stale=refreshWhileSaving(1);state.symbol='SHFE.ag2702';state.generation=2;
  finish({symbol:'CZCE.MA701',price:3190});await stale;assert.equal(updates.length,1);assert.equal(state.lastSuccess,123);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_follow_button_distinguishes_a_fixed_view_from_stopped_market_updates(self):
        start = self.adapted.index("  function renderFollowState()")
        end = self.adapted.index("  function syncView(", start)
        helper = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const button={setAttribute(){}},$=()=>button,state={active:'m30'},charts={m30:{followLatest:true,viewCustomized:false}};
""" + helper + """
renderFollowState();assert.equal(button.textContent,'跟随最新');
charts.m30.followLatest=false;renderFollowState();assert.equal(button.textContent,'回到最新');assert.ok(button.title.includes('行情仍在更新'));
charts.m30.followLatest=true;charts.m30.viewCustomized=true;renderFollowState();assert.equal(button.textContent,'回到最新');
""")

    def test_return_to_latest_exits_peer_following_before_fitting(self):
        listener = next(line for line in self.adapted.splitlines() if "$('follow-latest').addEventListener('click'" in line)
        run_javascript("""
const assert=require('node:assert/strict');
const calls=[],state={active:'m30'};let click;
const $=()=>({addEventListener:(name,fn)=>click=fn});
const stopFollowing=restore=>calls.push(['stop',restore]),charts={m30:{fit:()=>calls.push(['fit'])}},saveViews=()=>calls.push(['save']),renderFollowState=()=>calls.push(['render']);
""" + listener + """
click();assert.deepEqual(calls,[['stop',false],['fit'],['save'],['render']]);
""")

    def test_focus_switch_rejects_incomplete_work_without_finishing_or_submitting_it(self):
        start = self.adapted.index("  function dualTextDraft()")
        end = self.adapted.index("  function cancelActiveTool()", start)
        helpers = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const calls=[],state={viewOnly:false},charts={m30:{tool:'select',path:[]}};
let dialog=false;
const document={querySelector:()=>dialog?{}:null,activeElement:null},E={isTextTarget:()=>false};
const notice=value=>calls.push(['notice',value]),closeObjectContext=()=>calls.push(['close-menu']);
const saveViews=()=>calls.push(['save-view']),persistDraft=()=>calls.push(['keep-draft']),sendPresence=value=>calls.push(['presence',value]);
const finishDrawings=()=>{throw Error('Must not finish a drawing');},savePlanForm=()=>{throw Error('Must not submit a form');},chooseTool=()=>{throw Error('Must not change tools');};
""" + helpers + """
const draftCases=[
  [{},{path:[{time:1,price:100},{time:2,price:110}]}],
  [{},{drag:{kind:'object',preview:{price:110}}}],
  [{},{pinch:{distance:10}}],
  [{},{capturedPointerId:0}],
  [{},{insertWave:'wave-id'}],
  [{},{placementPreview:{price:100}}],
  [{quickTool:'planned-stop'},{}],[{quickSequence:true},{}],
  [{levelPreview:{price:100}},{}],[{levelDrag:{field:'stop_price'}},{}],
  [{symbolDrag:'CZCE.MA701'},{}],[{spaceTool:{m30:'wave'}},{}],
  [{planFormDirty:true},{}],[{objectDraft:true},{}],[{nodeDraft:true},{}],
  [{riskDirty:true},{}],[{previewDirty:true},{}],
  [{},{textEditor:{hidden:false},textInput:{value:'未添加文字',focus:()=>calls.push(['focus'])}}],
  [{},{textEditor:{hidden:false},textInput:{value:'',focus:()=>calls.push(['focus'])},textComposing:true}],
  [{},{titleEditor:{hidden:false,value:'新标题',focus:()=>calls.push(['focus'])},titleLastValue:'旧标题'}],
  [{},{titleEditor:{hidden:false,value:'标题',focus:()=>calls.push(['focus'])},titleLastValue:'标题',titleComposing:true}],
];
for(const [draftState,draftChart] of draftCases){
  for(const key of Object.keys(state))delete state[key];Object.assign(state,{viewOnly:false},draftState);
  charts.m30={tool:'select',path:[],...draftChart};calls.length=0;
  const before=JSON.stringify({state,charts});
  assert.equal(dualCanChangeFocus(),false);assert.equal(calls.length,0);
  assert.equal(dualPrepareFocusSwitch(),false);assert.equal(calls[0][0],'notice');
  assert.ok(calls.every(call=>['notice','focus'].includes(call[0])));
  assert.equal(JSON.stringify({state,charts}),before);
}
for(const key of Object.keys(state))delete state[key];Object.assign(state,{viewOnly:false,dirty:true,saving:true,outbox:{id:'already-submitted'}});
charts.m30={tool:'wave',path:[],followLatest:false,view:{start:1,end:20}};calls.length=0;
const before=JSON.stringify({state,charts});assert.equal(dualPrepareFocusSwitch(),true);
assert.deepEqual(calls.map(call=>call[0]),['close-menu','save-view','keep-draft','presence']);
assert.equal(JSON.stringify({state,charts}),before);
dialog=true;calls.length=0;assert.equal(dualPrepareFocusSwitch(),false);assert.equal(calls[0][0],'notice');
""")

    def test_escape_keeps_inner_operations_ahead_of_page_restore(self):
        start = self.adapted.index("  function dualTextDraft()")
        end = self.adapted.index("  function cancelActiveTool()", start)
        helpers = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const state={},charts={m30:{tool:'select',path:[]}};
let popup=false,input=false;
const document={querySelector:()=>popup?{}:null,activeElement:null},E={isTextTarget:()=>input};
""" + helpers + """
assert.equal(dualEscapeHasPriority(),false);
popup=true;assert.equal(dualEscapeHasPriority(),true);popup=false;
input=true;assert.equal(dualEscapeHasPriority(),true);input=false;
charts.m30.path=[{time:1,price:100}];assert.equal(dualEscapeHasPriority(),true);charts.m30.path=[];
charts.m30.tool='wave';assert.equal(dualEscapeHasPriority(),true);charts.m30.tool='select';
state.quickSequence=true;assert.equal(dualEscapeHasPriority(),true);delete state.quickSequence;
assert.equal(dualEscapeHasPriority(),false);
""")

    def test_maximized_escape_cannot_discard_an_unsubmitted_text_draft(self):
        condition = self.adapted.index("    if(event.key!=='Escape'||document.querySelector('#cases-dialog[open]')")
        start = self.adapted.rfind("  document.addEventListener('keydown',event=>{", 0, condition)
        end = self.adapted.index("  document.addEventListener('keydown',event=>{const menu", condition)
        listener = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
let handler,maximized=true,isText=false,draft;
const calls=[],document={addEventListener:(name,fn)=>handler=fn,querySelector:()=>null};
const $=()=>({hidden:true}),window={FullWorkspace:{isMaximized:()=>maximized}},E={isTextTarget:()=>isText};
const notice=value=>calls.push('notice'),dualTextDraft=()=>draft,cancelActiveTool=()=>{calls.push('cancel');return true;};
""" + listener + """
const event={key:'Escape',target:{},preventDefault:()=>calls.push('prevent'),stopImmediatePropagation:()=>calls.push('stop')};
draft={textEditor:{hidden:false},textInput:{value:'尚未添加',focus:()=>calls.push('focus')}};
handler(event);assert.deepEqual(calls,['prevent','stop','notice','focus']);assert.equal(draft.textInput.value,'尚未添加');assert.equal(draft.textEditor.hidden,false);
calls.length=0;draft=null;isText=true;handler(event);assert.deepEqual(calls,[]);
calls.length=0;isText=false;handler({...event,isComposing:true});assert.deepEqual(calls,[]);
calls.length=0;handler(event);assert.deepEqual(calls,['cancel','prevent','stop']);
""")

    def test_fullscreen_dock_toggles_delegate_without_changing_dom_or_saved_preference(self):
        function = next(line for line in self.adapted.splitlines() if line.strip().startswith("function setDockVisible("))
        open_panel = next(line for line in self.adapted.splitlines() if line.strip().startswith("function openPanel("))
        run_javascript("""
const assert=require('node:assert/strict');
let maximized=false;
const writes=[],requests=[],window={FullWorkspace:{isMaximized:()=>maximized,requestDockVisible:open=>requests.push(open)}};
const dock={hidden:false},label={},button={setAttribute:(key,value)=>button[key]=value};
const panel={tagName:'SECTION',hidden:true},state={viewOnly:false},PANEL_IDS=['quick-plan-panel'],document={querySelectorAll:()=>[]};
const nodes={'workspace-dock':dock,'layers-toggle-label':label,'objects-toggle':button,'quick-plan-panel':panel},$=id=>nodes[id],storageSet=(key,value)=>writes.push([key,value]);
""" + function + "\n" + open_panel + """
setDockVisible(false);assert.deepEqual(writes,[['dock-open',false]]);assert.equal(dock.hidden,true);
maximized=true;setDockVisible(true);assert.equal(dock.hidden,true);assert.equal(label.textContent,'展开图层');assert.equal(button['aria-expanded'],'false');
setDockVisible(false);assert.equal(dock.hidden,true);assert.deepEqual(requests,[true,false]);assert.deepEqual(writes,[['dock-open',false]]);
openPanel('quick-plan-panel');assert.deepEqual(requests,[true,false,true]);assert.equal(panel.hidden,false);assert.equal(state.panel,'quick-plan-panel');assert.equal(dock.hidden,true);
maximized=false;setDockVisible(true);assert.equal(dock.hidden,false);assert.deepEqual(writes,[['dock-open',false],['dock-open',true]]);
""")

    def test_sidebar_hiding_checks_edits_without_blurring_or_submitting(self):
        start = self.adapted.index("  function dualTextDraft()")
        end = self.adapted.index("  function cancelActiveTool()", start)
        helpers = self.adapted[start:end]
        run_javascript("""
const assert=require('node:assert/strict');
const state={},charts={},calls=[],handlers={};
const document={querySelector:()=>null,activeElement:null,addEventListener:(name,fn)=>handlers[name]=fn};
const notice=message=>calls.push(message),E={isTextTarget:()=>false};
""" + helpers + """
assert.equal(dualCanHideSidebar('right'),true);assert.equal(dualCanHideSidebar('left'),true);
for(const key of ['planFormDirty','objectDraft','nodeDraft','riskDirty']){
  state[key]=true;calls.length=0;const before=JSON.stringify(state);
  assert.equal(dualCanHideSidebar('right'),false);assert.equal(calls.length,0);
  assert.equal(dualCanHideSidebar('right',{notify:true}),false);assert.equal(calls.length,1);
  assert.equal(dualCanHideSidebar('left'),true);assert.equal(JSON.stringify(state),before);delete state[key];
}
const input={value:'尚未完成的输入',closest:selector=>selector==='#workspace-dock'?{}:null};
handlers.compositionstart({target:input});assert.equal(dualCanHideSidebar('right'),false);assert.equal(dualCanChangeFocus(),false);assert.equal(input.value,'尚未完成的输入');
handlers.compositionend({target:input});assert.equal(dualCanHideSidebar('right'),true);
state.dirty=true;state.saving=true;assert.equal(dualCanHideSidebar('right'),true,'A submitted save must not block hiding');
""")

    def test_hidden_dock_snapshot_keeps_manual_width_for_restore_and_pane_switch(self):
        start = self.adapted.index("  function dualDockWidth()")
        end = self.adapted.index("  function dualTextDraft()", start)
        function = self.adapted[start:end]
        self.assertIn("dockWidth:dualDockWidth()", self.adapted)
        run_javascript("""
const assert=require('node:assert/strict');
const dock={hidden:true,style:{width:'286px'},getBoundingClientRect:()=>({width:301})},$=()=>dock;
let computed='204px';const getComputedStyle=()=>({width:computed});
""" + function + """
assert.equal(dualDockWidth(),286);
dock.style.width='';computed='248px';assert.equal(dualDockWidth(),248);
computed='auto';assert.equal(dualDockWidth(),204);
dock.hidden=false;assert.equal(dualDockWidth(),301);
""")


if __name__ == "__main__":
    unittest.main()
