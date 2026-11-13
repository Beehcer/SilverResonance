"""Validate parent bootstrap reuse without browser or remote service access."""

import json
from pathlib import Path
import subprocess
import unittest

from workspace_runtime import adapt_workspace_js


class ParentBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).parent / 'static/workspace.js').read_text(encoding='utf-8')
        cls.adapted = adapt_workspace_js(source)
        start = cls.adapted.index('  const dualBootstrapUsed=')
        end = cls.adapted.index('  async function boot()', start)
        cls.helper = cls.adapted[start:end]

    def run_scenario(self, scenario):
        harness = r"""
const assert=require('node:assert/strict');
const origin='https://example.test',now=100000;
const seed=()=>({createdAt:now,session:{user:{id:'owner',name:'Original'},csrf_token:'token'},symbols:{symbols:[{symbol:'MA',name:'甲醇'}]}});
function make(value=seed()){
  const parent={location:{origin},DualWorkspaceBootstrap:value},window={parent},state={session:null},calls=[];
  const api=async path=>{calls.push(path);return {network:true,path};};
  const bootstrap=new Function('window','state','location','api','Date',HELPER+'\nreturn dualBootstrap;')(window,state,{origin},api,{now:()=>now});
  return {bootstrap,parent,window,state,calls};
}
""".replace('HELPER', json.dumps(self.helper))
        script = harness + '\n(async()=>{\n' + scenario + '\n})().catch(error=>{console.error(error);process.exitCode=1;});'
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_same_origin_parent_reused_once_per_path_and_values_are_isolated(self):
        self.run_scenario(r"""
const original=seed(),h=make(original);
const session=await h.bootstrap('/session');assert.equal(session.user.id,'owner');assert.deepEqual(h.calls,[]);
session.user.name='Changed in child';assert.equal(original.session.user.name,'Original');h.state.session=session;
const symbols=await h.bootstrap('/symbols');symbols.symbols[0].name='Child name';
assert.equal(original.symbols.symbols[0].name,'甲醇');assert.deepEqual(h.calls,[]);
assert.equal((await h.bootstrap('/session')).network,true);
assert.equal((await h.bootstrap('/symbols')).network,true);
assert.deepEqual(h.calls,['/session','/symbols']);
const other=make(original);assert.equal((await other.bootstrap('/session')).user.id,'owner');
assert.deepEqual(other.calls,[],'Each pane may consume its own bootstrap copy');
""")

    def test_expired_future_cross_origin_and_unavailable_parent_use_api(self):
        self.run_scenario(r"""
for(const offset of [-60000,-60001,1]){
  const value=seed();value.createdAt=now+offset;const h=make(value);
  assert.equal((await h.bootstrap('/session')).network,true);assert.deepEqual(h.calls,['/session']);
}
const cross=make();cross.parent.location.origin='https://other.test';
assert.equal((await cross.bootstrap('/session')).network,true);
const blocked=make();Object.defineProperty(blocked.parent,'location',{get(){throw new Error('cross-origin access denied');}});
assert.equal((await blocked.bootstrap('/session')).network,true);
const single=make();single.window.parent=single.window;
assert.equal((await single.bootstrap('/session')).network,true);
const absent=make(null);assert.equal((await absent.bootstrap('/session')).network,true);
""")

    def test_catalogue_requires_matching_identity_and_only_supported_reads_are_reused(self):
        self.run_scenario(r"""
const h=make();h.state.session={user:{id:'different'}};
assert.equal((await h.bootstrap('/symbols')).network,true);
h.state.session={user:{id:'owner'}};
assert.equal((await h.bootstrap('/symbols')).symbols[0].symbol,'MA');
assert.equal((await h.bootstrap('/rules')).network,true);
assert.equal((await h.bootstrap('/risk')).network,true);
assert.deepEqual(h.calls,['/symbols','/rules','/risk']);
const invalid=make();invalid.state.session={user:{id:'owner'}};invalid.parent.DualWorkspaceBootstrap.symbols={symbols:{}};
assert.equal((await invalid.bootstrap('/symbols')).network,true);
const anonymous=make();delete anonymous.parent.DualWorkspaceBootstrap.session.user.id;
assert.equal((await anonymous.bootstrap('/session')).network,true);
""")

    def test_showable_requires_constructed_workspace_but_does_not_claim_market_ready(self):
        snapshot = next(line.strip() for line in self.adapted.splitlines() if line.strip().startswith('snapshot:()=>'))
        expression = snapshot.removeprefix('snapshot:').removesuffix(',')
        script = r"""
const assert=require('node:assert/strict');
const state={session:null,planReady:false},charts={day:{},m30:{},m3:{}},workspace={hidden:true};
const $=id=>id==='workspace'?workspace:{hidden:false,style:{},value:''};
const document={querySelector:()=>null};let complete=false;
const snapshot=new Function('state','charts','$','marketComplete','document','dualDockWidth','return '+EXPRESSION)(state,charts,$,()=>complete,document,()=>204);
assert.equal(snapshot().showable,false);state.session={user:{id:'owner'}};assert.equal(snapshot().showable,false);
workspace.hidden=false;assert.equal(snapshot().showable,true);assert.equal(snapshot().ready,false);
delete charts.m3;assert.equal(snapshot().showable,false);charts.m3={};
state.planReady=true;assert.equal(snapshot().ready,false);
complete=true;assert.equal(snapshot().ready,true);assert.equal(snapshot().showable,true);
workspace.hidden=true;assert.equal(snapshot().showable,false);
""".replace('EXPRESSION', json.dumps(expression))
        result = subprocess.run(['node', '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
