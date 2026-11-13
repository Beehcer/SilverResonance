"""Exercise the real shell startup request helper with deterministic timers."""
from pathlib import Path
import subprocess
import unittest


class ShellStartupTests(unittest.TestCase):
    def test_success_timeout_recovery_retry_bound_and_terminal_http_failure(self):
        source = Path(__file__).with_name('shell.js').read_text(encoding='utf-8')
        start = source.index('  async function startupRequest(path)')
        end = source.index('  async function start()', start)
        script = r"""
const assert=require('node:assert/strict');
const status={textContent:''},$=()=>status;
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
