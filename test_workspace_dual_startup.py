"""Exercise the real shell startup request helper with deterministic timers."""
from pathlib import Path
import subprocess
import unittest


class ShellStartupTests(unittest.TestCase):
    def test_frame_link_distinguishes_default_plan_from_no_board_override(self):
        source = (Path(__file__).parent / 'static/workspace-dual-shell.js').read_text(encoding='utf-8')
        core = source[source.index('  function makeFrame('):source.index('  async function waitReady(')]
        script = r"""
const assert=require('node:assert/strict');
const state={activeId:'pane-a',panes:[{id:'pane-a',symbol:'MA'}]},frames=new Map();
const location={origin:'https://example.test'},name=symbol=>symbol;
const document={createElement:()=>({dataset:{}})},$=()=>({append(){}});
""" + core + r"""
for(const [board,present] of [[undefined,false],['',true],['named-plan',true]]){
  const record=makeFrame({id:'pane-a',symbol:'MA',...(board===undefined?{}:{board})});
  const url=new URL(record.frame.src,location.origin);
  assert.equal(url.pathname,'/workspace');assert.equal(url.searchParams.get('pane'),'pane-a');
  assert.equal(url.searchParams.has('board'),present);
  assert.equal(url.searchParams.get('board'),present?board:null);
  // Mirror the existing runtime's explicit-URL-then-user-preference rule.
  let savedBoard='previous-named-plan';
  if(url.searchParams.has('board'))savedBoard=url.searchParams.get('board');
  assert.equal(savedBoard,present?board:'previous-named-plan');
}
"""
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def run_startup(self, scenario):
        source = (Path(__file__).parent / 'static/workspace-dual-shell.js').read_text(encoding='utf-8')
        core = source[source.index('  function mobileDevice()'):source.index('\n  start();')]
        model = (Path(__file__).parent / 'static/workspace-dual-model.js').as_posix()
        harness = r"""
const assert=require('node:assert/strict');
const M=require(MODEL),KEY='resonance-workspace-dual-layout-v1:';
let layoutKey=KEY,symbols=[],ids=[],state,session={user:{id:'owner'}},response={symbols:[{symbol:'MA',name:'甲醇'},{symbol:'SP',name:'纸浆'}]},ready=true,startupOpened=false;
const calls=[],created=[],saved=new Map(),urlChanges=[];
const navigator={userAgent:'Desktop',platform:'Win32',maxTouchPoints:0};
const window={innerWidth:1400,matchMedia:()=>({matches:false})};
const location={href:'https://example.test/workspace',replace:url=>urlChanges.push(url),reload:()=>{}};
const localStorage={getItem:key=>saved.get(key)??null};
const status={hidden:false,textContent:'',children:[],replaceChildren(){this.children=[];},append(...items){this.children.push(...items);}};
const startupNodes={startup:status,'startup-actions':{hidden:true},'startup-label':{textContent:''},'startup-detail':{textContent:''}};
const $=id=>startupNodes[id],document={createElement:tag=>({tag,addEventListener(){}})};
const startupProgress=(_completed,label,detail)=>{startupNodes['startup-label'].textContent=label;startupNodes['startup-detail'].textContent=detail;};
const history={replaceState:(_state,_name,url)=>urlChanges.push(url)};
const startupRequest=async path=>{calls.push(path);return path==='/session'?session:response;};
const readLayout=()=>JSON.parse(localStorage.getItem(layoutKey)),persist=()=>calls.push('persist'),layout=()=>{};
const makeFrame=pane=>created.push({...pane}),activeFrame=()=>({}),waitVisible=async()=>{if(ready){startupOpened=true;status.hidden=true;}return ready;};
""".replace('MODEL', repr(model))
        script = harness + core + '\n(async()=>{\n' + scenario + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_mobile_and_small_viewport_keep_original_single_view_before_session(self):
        for selector in ["window.innerWidth=800;", "navigator.userAgent='iPhone';", "navigator.platform='MacIntel';navigator.maxTouchPoints=5;"]:
            self.run_startup(selector + r"""
location.href='https://example.test/?symbol=MA&board=abc';
await start();assert.deepEqual(calls,[]);assert.equal(created.length,0);
const destination=new URL(urlChanges[0]);assert.equal(destination.pathname,'/workspace');
assert.equal(destination.searchParams.get('view'),'single');assert.equal(destination.searchParams.get('symbol'),'MA');assert.equal(destination.searchParams.get('board'),'abc');
""")

    def test_server_mobile_flag_redirects_without_loading_symbols_or_frames(self):
        self.run_startup(r"""
session.mobile_device=true;await start();assert.deepEqual(calls,['/session']);assert.equal(created.length,0);assert.match(urlChanges[0],/view=single/);
""")

    def test_session_precedes_frames_and_restores_user_specific_layout(self):
        self.run_startup(r"""
saved.set(KEY+'owner',JSON.stringify({panes:[{id:'pane-a',symbol:'MA'},{id:'pane-b',symbol:'SP'}],activeId:'pane-b'}));
saved.set(KEY+'other',JSON.stringify({panes:[{id:'pane-a',symbol:'SP'}],activeId:'pane-a'}));
await start();assert.deepEqual(calls,['/session','/symbols','persist']);assert.equal(layoutKey,KEY+'owner');
assert.deepEqual(created.map(p=>p.symbol),['MA','SP']);assert.equal(state.activeId,'pane-b');assert.equal(status.hidden,true);
""")

    def test_first_upgrade_preserves_former_symbol_and_shared_link_overrides_it(self):
        self.run_startup(r"""
saved.set('resonance-workspace-v1:symbol:owner',JSON.stringify('SP'));
await start();assert.equal(created[0].symbol,'SP');
created.length=0;location.href='https://example.test/workspace?symbol=MA&board=shared';
await start();assert.deepEqual(created,[{id:'pane-a',symbol:'MA',board:'shared'}]);assert.equal(urlChanges.at(-1),'/workspace');
""")

    def test_empty_catalogue_has_clear_recovery_without_creating_broken_frame(self):
        self.run_startup(r"""
response={symbols:[]};await start();assert.equal(created.length,0);assert.equal(status.hidden,false);
assert.match(startupNodes['startup-label'].textContent,/暂无可用品种/);assert.equal(startupNodes['startup-actions'].hidden,false);
""")

    def test_success_timeout_recovery_retry_bound_and_terminal_http_failure(self):
        source = (Path(__file__).parent / 'static/workspace-dual-shell.js').read_text(encoding='utf-8')
        start = source.index('  async function startupRequest(path)')
        end = source.index('  async function start()', start)
        script = r"""
const assert=require('node:assert/strict');
const status={textContent:''},$=()=>status,startupProgress=(_completed,label)=>{status.textContent=label;};
let timers=new Map(),next=0,waits=[],fetch,calls;
const setTimeout=(fn,ms)=>{const id=++next;timers.set(id,{fn,ms});waits.push(ms);if(ms<12000)queueMicrotask(()=>{if(timers.has(id)){timers.delete(id);fn();}});return id;};
const clearTimeout=id=>timers.delete(id);
const ok=()=>({ok:true,status:200,json:async()=>({status:'ok',symbols:['MA']})});
const reset=()=>{calls=0;timers.clear();waits=[];status.textContent='';};
""" + source[start:end] + r"""
(async()=>{
  reset();fetch=async(url,options)=>{calls++;assert.equal(url,'/api/workspace/symbols');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');return ok();};
  assert.deepEqual(await startupRequest('/symbols'),{status:'ok',symbols:['MA']});assert.equal(calls,1);assert.deepEqual(waits,[12000]);assert.equal(timers.size,0);

  reset();let aborted=false;
  fetch=async(url,{signal})=>{calls++;if(calls>1)return ok();return new Promise((resolve,reject)=>{
    signal.addEventListener('abort',()=>{aborted=true;reject(Object.assign(new Error('timeout'),{name:'AbortError'}));});
    queueMicrotask(()=>[...timers.values()].find(timer=>timer.ms===12000).fn());
  });};
  assert.equal((await startupRequest('/session')).status,'ok');assert.equal(aborted,true);assert.equal(calls,2);assert.match(status.textContent,/2\/3/);assert.equal(timers.size,0);

  reset();fetch=async()=>{calls++;throw new Error('offline');};
  await assert.rejects(startupRequest('/session'),/offline/);assert.equal(calls,3);assert.deepEqual(waits,[12000,700,12000,1400,12000]);assert.equal(timers.size,0);

  reset();fetch=async()=>{calls++;return{ok:false,status:403};};
  await assert.rejects(startupRequest('/session'));assert.equal(calls,1);assert.equal(timers.size,0);

  reset();fetch=async()=>{calls++;return calls<3?{ok:false,status:503}:ok();};
  assert.equal((await startupRequest('/session')).status,'ok');assert.equal(calls,3);assert.equal(timers.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8',
                                capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
