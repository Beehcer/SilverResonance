"""Exercise the real shell's focus lifecycle with live frame stand-ins."""
from pathlib import Path
import subprocess
import unittest


class ShellFocusTests(unittest.TestCase):
    def run_scenario(self, scenario, *, delayed_ready=False):
        source = (Path(__file__).parent / 'static/workspace-dual-shell.js').read_text(encoding='utf-8')
        core = source[source.index('  const M ='):source.index('  function makeFrame(')]
        transition = source[source.index('  async function transition('):source.index('  function targetAt(')]
        make_frame = source[source.index('  function makeFrame('):source.index('  async function waitReady(')] if delayed_ready else ''
        messages = source[source.index("  window.addEventListener('message'"):source.index("  $('drop-shield').addEventListener")]
        model = (Path(__file__).parent / 'static/workspace-dual-model.js').as_posix()
        harness = r"""
const assert = require('node:assert/strict');
const events={}, saved=[], notices=[], raf=[], appended=[], readyWaits=[];
const node=()=>({hidden:false,textContent:'',style:{},attributes:{},setAttribute(key,value){this.attributes[key]=value;},classList:{add(){},remove(){},toggle(){}},getBoundingClientRect:()=>({left:0,top:25,width:1400,height:1000})});
const nodes={stage:node(),toast:node(),startup:node(),'sidebar-left-toggle':node(),'sidebar-right-toggle':node()};
nodes.stage.append=frame=>appended.push({frame,hidden:frame.hidden,inert:frame.inert});
const document={getElementById:id=>nodes[id],body:node(),createElement:tag=>{assert.equal(tag,'iframe');return record('pane-b').frame;}};
const window={DualWorkspaceModel:require(MODEL),addEventListener:(type,fn)=>events[type]=fn};
const location={origin:'http://localhost'};
const localStorage={setItem:(key,value)=>saved.push(JSON.parse(value))};
const requestAnimationFrame=fn=>(raf.push(fn),raf.length);
const setTimeout=()=>1,clearTimeout=()=>{};
""".replace('MODEL', repr(model))
        fixtures = r"""
function record(id) {
  const attrs={}, calls={prepare:0,deactivate:0,focus:0,switches:[]}, ui={dockOpen:true,dockWidth:204,group:'all',panel:'quick-plan-panel'};
  const api={ui,mode:{active:id==='pane-a',maximized:false},allowed:true,closeAllowed:true,hideAllowed:true,
    geometry:()=>({slots:{left:200,top:api.mode.maximized?48:80,width:996,height:api.mode.maximized?932:900}}),
    snapshot:()=>({ui:{...ui}}),
    setMode:value=>{api.mode=value;if(value.maximized&&value.sidebars)ui.dockOpen=value.sidebars.rightOpen;},
    applyUI:value=>Object.assign(ui,value),
    canChangeFocus:()=>api.allowed,
    canHideSidebar:()=>api.hideAllowed,
    prepareFocusSwitch:()=>{calls.prepare++;return api.allowed;},
    prepareDeactivate:async()=>{calls.deactivate++;return api.closeAllowed;},
    focusMaximizeButton:()=>{calls.focus++;},
    switchSymbol:async(...args)=>{calls.switches.push(args);return true;}};
  const frame={hidden:false,inert:false,style:{cssText:''},className:'',title:'',dataset:{},
    setAttribute:(key,value)=>attrs[key]=value,removeAttribute:key=>delete attrs[key],
    getBoundingClientRect:()=>({left:0,top:25,width:1400,height:1000}),
    remove:()=>{frame.removed=true;},contentWindow:{FullWorkspace:api,focus(){}}};
  return {frame,api,calls,attrs,ready:true,symbol:id==='pane-a'?'MA':'SP'};
}
ids=['MA','SP','AU'];symbols=ids;
state={panes:[{id:'pane-a',symbol:'MA'},{id:'pane-b',symbol:'SP'}],activeId:'pane-a'};
const a=record('pane-a'),b=record('pane-b');frames.set('pane-a',a);frames.set('pane-b',b);
layout();
"""
        if delayed_ready:
            frame_lifecycle = make_frame + r"""
function waitReady(record){return new Promise((resolve,reject)=>readyWaits.push({record,resolve,reject}));}
async function pendingLoad(){
  for(let tick=0;tick<20&&!readyWaits.length;tick++)await Promise.resolve();
  assert.equal(readyWaits.length,1,'The new pane must be waiting for its first ready message');
  return readyWaits[0];
}
function onlyOriginalPane(){
  frames.delete('pane-b');state={panes:[{id:'pane-a',symbol:'MA'}],activeId:'pane-a'};layout();
}
"""
        else:
            frame_lifecycle = r"""
function makeFrame(pane){const result=record(pane.id);result.symbol=pane.symbol;frames.set(pane.id,result);return result;}
async function waitReady(){return true;}
"""
        script = harness + core + transition + messages + fixtures + frame_lifecycle + '\n(async()=>{\n' + scenario + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_workspace_fullscreen_preserves_both_panes_sidebars_and_active_draft(self):
        self.run_scenario(r"""
const priorStyle={...b.frame.style};a.api.allowed=false;
maximizeWorkspace('pane-b');
assert.equal(workspaceMaximized,true);assert.equal(state.activeId,'pane-a');
assert.equal(a.calls.prepare,0);assert.equal(a.calls.deactivate,0);assert.equal(a.calls.focus,0);
for(const r of [a,b]){assert.equal(r.api.mode.maximized,true);assert.equal(r.frame.hidden,false);assert.equal(r.frame.inert,false);assert.equal(r.attrs['aria-hidden'],undefined);assert.doesNotMatch(r.frame.className,/is-background/);assert.equal(r.api.ui.dockOpen,false);assert.equal(r.api.ui.dockWidth,204);assert.equal(r.api.ui.group,'all');}
assert.equal(frames.size,2);assert.equal(a.frame.removed,undefined);assert.equal(b.frame.removed,undefined);
assert.equal(saved.length,0);layout();assert.equal(b.api.mode.maximized,true);
restoreMaximized();assert.equal(workspaceMaximized,false);
assert.equal(a.api.mode.maximized,false);assert.equal(b.api.mode.maximized,false);
assert.equal(a.api.ui.dockOpen,true);
assert.equal(a.api.mode.count,2);assert.equal(b.api.mode.count,2);assert.deepEqual(b.frame.style,priorStyle);
""")

    def test_switching_panes_and_fullscreen_never_reopens_or_resets_selected_boards(self):
        self.run_scenario(r"""
a.api.board='named-ma-plan';b.api.board='';
await transition(M.activate(state,'pane-b'));
maximizeWorkspace('pane-a');restoreMaximized();
await transition(M.activate(state,'pane-a'));
assert.deepEqual(a.calls.switches,[]);assert.deepEqual(b.calls.switches,[]);
assert.equal(a.api.board,'named-ma-plan');assert.equal(b.api.board,'');
""")

    def test_either_pane_can_toggle_and_switching_preserves_fullscreen(self):
        self.run_scenario(r"""
events.message({origin:location.origin,source:b.frame.contentWindow,data:{type:'full-workspace:maximize',paneId:'pane-b'}});
assert.equal(workspaceMaximized,true);assert.equal(state.activeId,'pane-a');
await transition(M.activate(state,'pane-b'));
assert.equal(state.activeId,'pane-b');assert.equal(workspaceMaximized,true);assert.equal(a.calls.prepare,1);assert.equal(a.calls.deactivate,0);
assert.deepEqual(Object.keys(saved.at(-1)).sort(),['activeId','panes']);
events.message({origin:location.origin,source:a.frame.contentWindow,data:{type:'full-workspace:restore',paneId:'pane-a'}});
assert.equal(workspaceMaximized,false);assert.equal(state.activeId,'pane-b');
""")

    def test_failed_close_preserves_both_and_successful_close_stays_fullscreen(self):
        self.run_scenario(r"""
maximizeWorkspace('pane-a');a.api.closeAllowed=false;
await transition(M.closePane(state,'pane-a'));
assert.equal(workspaceMaximized,true);assert.equal(frames.size,2);assert.equal(a.frame.removed,undefined);
a.api.closeAllowed=true;await transition(M.closePane(state,'pane-a'));
assert.equal(workspaceMaximized,true);assert.equal(frames.size,1);assert.equal(a.frame.removed,true);
assert.equal(state.activeId,'pane-b');assert.equal(b.frame.inert,false);assert.equal(b.api.mode.count,1);
assert.equal(b.api.ui.dockOpen,false);assert.equal(b.api.ui.dockWidth,204);
assert.equal(b.api.mode.maximized,true);
restoreMaximized();assert.equal(b.api.mode.maximized,false);assert.equal(b.api.mode.count,1);
assert.equal(b.api.ui.dockOpen,true);
""")

    def test_closing_other_pane_does_not_touch_active_draft(self):
        self.run_scenario(r"""
maximizeWorkspace('pane-a');a.api.allowed=false;
await transition(M.closePane(state,'pane-b'));
assert.equal(frames.size,1);assert.equal(state.activeId,'pane-a');assert.equal(workspaceMaximized,true);
assert.equal(a.calls.prepare,0);assert.equal(a.calls.deactivate,0);assert.equal(a.calls.focus,0);
assert.equal(b.calls.deactivate,1);assert.equal(a.api.mode.count,1);
""")

    def test_all_affected_panes_are_checked_before_any_mutation(self):
        self.run_scenario(r"""
maximizeWorkspace('pane-b');b.api.allowed=false;
const before=JSON.stringify(state);
await transition(M.dropSymbol(state,'AU','pane-b',ids));
assert.equal(JSON.stringify(state),before);assert.equal(workspaceMaximized,true);
assert.equal(a.calls.prepare,0);assert.equal(a.calls.deactivate,0);assert.equal(b.calls.deactivate,0);
assert.equal(frames.size,2);assert.equal(saved.length,0);
""")

    def test_dragging_second_symbol_in_fullscreen_preserves_scope(self):
        self.run_scenario(r"""
maximizeWorkspace('pane-a');await transition(M.closePane(state,'pane-b'));
await transition(M.dropSymbol(state,'SP','pane-a',ids));
assert.equal(frames.size,2);assert.equal(workspaceMaximized,true);
assert.equal(state.activeId,'pane-b');assert.equal(a.api.mode.count,2);
assert.equal(frames.get('pane-b').api.mode.maximized,true);assert.equal(a.frame.inert,false);
assert.deepEqual(state.panes.map(p=>p.symbol),['MA','SP']);
""")

    def test_new_draft_during_slow_save_prevents_closing(self):
        self.run_scenario(r"""
maximizeWorkspace('pane-a');
a.api.prepareDeactivate=async()=>{a.calls.deactivate++;await Promise.resolve();a.api.allowed=false;return true;};
await transition(M.closePane(state,'pane-a'));
assert.equal(workspaceMaximized,true);assert.equal(frames.size,2);assert.equal(state.activeId,'pane-a');
assert.equal(a.frame.removed,undefined);assert.equal(a.calls.deactivate,1);assert.equal(saved.length,0);
""")

    def test_sidebars_default_closed_and_follow_workspace_across_switch_close_restore(self):
        self.run_scenario(r"""
a.api.ui.dockWidth=320;
maximizeWorkspace('pane-b');
assert.deepEqual(fullscreenSidebars,{leftOpen:false,rightOpen:false});
assert.equal(nodes['sidebar-left-toggle'].hidden,false);assert.equal(nodes['sidebar-right-toggle'].hidden,false);
setFullscreenSidebar('left',true);setFullscreenSidebar('right',true);
assert.equal(nodes['sidebar-left-toggle'].attributes['aria-expanded'],'true');
await transition(M.activate(state,'pane-b'));
assert.deepEqual(fullscreenSidebars,{leftOpen:true,rightOpen:true});assert.equal(b.api.ui.dockOpen,true);
await transition(M.closePane(state,'pane-a'));assert.equal(workspaceMaximized,true);
setFullscreenSidebar('right',false);assert.equal(b.api.ui.dockOpen,false);
restoreMaximized();assert.equal(b.api.ui.dockOpen,true);assert.equal(b.api.ui.dockWidth,320);
assert.equal(nodes['sidebar-left-toggle'].hidden,true);assert.equal(nodes['sidebar-right-toggle'].hidden,true);
maximizeWorkspace('pane-b');assert.deepEqual(fullscreenSidebars,{leftOpen:false,rightOpen:false});
""")

    def test_sidebar_draft_guards_and_late_dock_events(self):
        self.run_scenario(r"""
a.api.hideAllowed=false;maximizeWorkspace('pane-a');assert.equal(workspaceMaximized,false);
a.api.hideAllowed=true;a.api.ui.dockOpen=false;maximizeWorkspace('pane-a');
events.message({origin:location.origin,source:a.frame.contentWindow,data:{type:'full-workspace:dock-toggle',paneId:'pane-a',open:true}});
assert.equal(fullscreenSidebars.rightOpen,true);
a.api.hideAllowed=false;setFullscreenSidebar('right',false);assert.equal(fullscreenSidebars.rightOpen,true);
restoreMaximized();assert.equal(workspaceMaximized,true);
a.api.hideAllowed=true;restoreMaximized();assert.equal(workspaceMaximized,false);assert.equal(a.api.ui.dockOpen,false);
events.message({origin:location.origin,source:a.frame.contentWindow,data:{type:'full-workspace:dock-toggle',paneId:'pane-a',open:true}});
assert.equal(a.api.ui.dockOpen,false);
""")

    def test_slow_second_pane_load_keeps_original_workspace_visible_until_ready(self):
        self.run_scenario(r"""
onlyOriginalPane();const before=JSON.stringify(state),originalWindow=a.frame.contentWindow;
const pending=transition(M.dropSymbol(state,'SP','pane-a',ids));
const waiting=await pendingLoad(),newFrame=waiting.record.frame;
assert.equal(appended[0].hidden,true,'The loading iframe must be concealed before it enters the DOM');
assert.equal(appended[0].inert,true);
const opening=new URL(newFrame.src,location.origin).searchParams;
assert.equal(opening.get('active'),'false');assert.equal(opening.get('count'),'2');
assert.equal(opening.get('slot'),'bottom');assert.equal(opening.get('symbol'),'SP');
assert.equal(JSON.stringify(state),before,'Loading a second pane must not activate it yet');
assert.equal(a.frame.hidden,false);assert.equal(a.frame.inert,false);assert.equal(a.api.mode.count,1);
assert.equal(a.frame.contentWindow,originalWindow);assert.equal(a.frame.removed,undefined);
assert.equal(newFrame.hidden,true,'A new connection screen must never cover the loaded pane');
assert.equal(newFrame.inert,true);assert.equal(saved.length,0);assert.equal(busy,true);
waiting.record.ready=true;waiting.resolve(true);await pending;
assert.deepEqual(state.panes.map(p=>p.symbol),['MA','SP']);assert.equal(state.activeId,'pane-b');
assert.equal(frames.size,2);assert.equal(a.frame.hidden,false);assert.equal(newFrame.hidden,false);
assert.equal(a.api.mode.count,2);assert.equal(saved.length,1);assert.equal(busy,false);
""", delayed_ready=True)

    def test_second_pane_timeout_discards_only_loading_frame_and_preserves_fullscreen(self):
        self.run_scenario(r"""
onlyOriginalPane();maximizeWorkspace('pane-a');setFullscreenSidebar('left',true);
const before=JSON.stringify(state),sidebars=JSON.stringify(fullscreenSidebars);
const pending=transition(M.dropSymbol(state,'SP','pane-a',ids));
const waiting=await pendingLoad();waiting.resolve(false);await pending;
assert.equal(JSON.stringify(state),before);assert.equal(frames.size,1);assert.equal(frames.get('pane-a'),a);
assert.equal(waiting.record.frame.removed,true);assert.equal(a.frame.removed,undefined);
assert.equal(a.frame.hidden,false);assert.equal(a.frame.inert,false);assert.equal(a.api.mode.count,1);
assert.equal(workspaceMaximized,true);assert.equal(JSON.stringify(fullscreenSidebars),sidebars);
assert.equal(saved.length,0);assert.equal(busy,false);
assert.match(nodes.toast.textContent,/加载|连接|原窗口|保留/);
events.message({origin:location.origin,source:waiting.record.frame.contentWindow,data:{type:'full-workspace:ready',paneId:'pane-b'}});
assert.equal(JSON.stringify(state),before);assert.equal(frames.size,1,'Late readiness cannot resurrect a discarded pane');
""", delayed_ready=True)

    def test_second_pane_load_exception_cleans_up_uncommitted_frame(self):
        self.run_scenario(r"""
onlyOriginalPane();const before=JSON.stringify(state);
const pending=transition(M.dropSymbol(state,'SP','pane-a',ids));
const waiting=await pendingLoad();waiting.reject(new Error('Simulated iframe readiness failure'));await pending;
assert.equal(JSON.stringify(state),before);assert.equal(frames.size,1);assert.equal(frames.get('pane-a'),a);
assert.equal(waiting.record.frame.removed,true);assert.equal(a.frame.hidden,false);assert.equal(a.frame.inert,false);
assert.equal(a.api.mode.count,1);assert.equal(saved.length,0);assert.equal(busy,false);
""", delayed_ready=True)

    def test_new_draft_while_second_pane_loads_cancels_activation_without_touching_draft(self):
        self.run_scenario(r"""
onlyOriginalPane();const before=JSON.stringify(state);
const pending=transition(M.dropSymbol(state,'SP','pane-a',ids));
const waiting=await pendingLoad();a.api.allowed=false;
waiting.record.ready=true;waiting.resolve(true);await pending;
assert.equal(JSON.stringify(state),before);assert.equal(frames.size,1);assert.equal(frames.get('pane-a'),a);
assert.equal(waiting.record.frame.removed,true);assert.equal(a.frame.hidden,false);assert.equal(a.frame.inert,false);
assert.equal(a.calls.deactivate,0);assert.equal(a.api.allowed,false);assert.equal(a.api.mode.count,1);
assert.equal(saved.length,0);assert.equal(busy,false);
assert.match(nodes.toast.textContent,/编辑|保留/);
""", delayed_ready=True)

    def test_second_pane_adopts_sidebar_changes_made_during_loading(self):
        self.run_scenario(r"""
onlyOriginalPane();const pending=transition(M.dropSymbol(state,'SP','pane-a',ids));
const waiting=await pendingLoad();
Object.assign(a.api.ui,{group:'favorites',search:'银',panel:'objects-details',dockOpen:false,dockWidth:240});
waiting.record.ready=true;waiting.resolve(true);await pending;
const newUI=waiting.record.frame.contentWindow.FullWorkspace.snapshot().ui;
assert.equal(newUI.group,'favorites');assert.equal(newUI.search,'银');assert.equal(newUI.panel,'objects-details');
assert.equal(newUI.dockOpen,false);assert.equal(newUI.dockWidth,240);
assert.equal(a.frame.removed,undefined);assert.equal(frames.size,2);assert.equal(saved.length,1);
""", delayed_ready=True)


if __name__ == '__main__':
    unittest.main()
