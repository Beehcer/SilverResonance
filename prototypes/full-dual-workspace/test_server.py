"""Offline full-API checks; each test writes only its own new prototype folder."""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("full_preview_server_test", DIRECTORY / "server.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class FullPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="test-data-", dir=DIRECTORY)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        with patch.object(preview.simulation.symbols.user_settings, "load_settings",
                          side_effect=AssertionError("account settings must not be read")):
            self.app = preview.create_application(self.directory)
        self.app.testing = True
        self.client = self.app.test_client()
        response = self.client.get("/api/workspace/session")
        self.assertEqual(response.status_code, 200)
        self.session = response.json
        self.headers = {"X-Workspace-CSRF": self.session["csrf_token"], "X-Workspace-Candle-Protocol": "2"}
        self.symbol = next(item["symbol"] for item in preview.catalogue() if item["symbol"].startswith("CZCE.MA"))

    def test_store_and_cookie_are_isolated_and_forbidden_files_unreachable(self):
        import workspace_api as original_api
        self.assertNotIn("app", sys.modules)
        self.assertEqual(original_api.COOKIE_NAME, "workspace_session")
        self.assertEqual(preview.workspace_api.COOKIE_NAME, "workspace_full_preview_session")
        self.assertEqual(self.app.extensions["preview_store"].path, self.directory / "workspace.json")
        with self.assertRaises(ValueError):
            preview.create_application(preview.PROJECT / "state")
        fresh = self.app.test_client()
        fresh.set_cookie("workspace_session", "formal-workspace-cookie", path="/api/workspace")
        response = fresh.get("/api/workspace/session")
        self.assertIn("workspace_full_preview_session=", response.headers["Set-Cookie"])
        self.assertNotIn("workspace_session=", response.headers["Set-Cookie"])
        self.assertEqual(fresh.get_cookie("workspace_session", path="/api/workspace").value, "formal-workspace-cookie")
        for route in ("/state/workspace.json", "/data/workspace.json", "/preview/server.py",
                      "/preview/../server.py", "/preview/%2e%2e/user_config.json", "/api/config", "/monitor"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual(self.client.put("/api/workspace/risk", json={"risk": {}}).status_code, 403)

    def test_original_template_keeps_tools_cases_and_injects_embedding_in_order(self):
        response = self.client.get("/pane?pane=pane-a&symbol=" + self.symbol)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for identity in ("cases-button", "members-button", "board-select", "quick-plan-panel",
                         "object-context-menu", "objects-details", "profile-form"):
            self.assertIn('id="' + identity + '"', html)
        self.assertIn('/preview/embed.css', html)
        self.assertIn('/preview/workspace-chart.js', html)
        self.assertLess(html.index('/preview/workspace-chart.js'), html.index('/preview/embed.js'))
        self.assertLess(html.index('/preview/embed.js'), html.index('/preview/workspace-runtime.js'))
        self.assertNotRegex(html, r'/static/workspace\.js(?:\?|["\'])')
        self.assertNotRegex(html, r'/static/workspace-chart\.js(?:\?|["\'])')
        self.assertIn('id="monitor-link"', html)
        self.assertRegex(html, r'<a\s+hidden\s+id="monitor-link"')
        for url in re.findall(r'(?:src|href)="(/static/[^"?]+)', html):
            with self.subTest(asset=url):
                with self.client.get(url) as asset:
                    self.assertEqual(asset.status_code, 200)
        with self.client.get('/preview/model.js') as asset:
            self.assertEqual(asset.status_code, 200)
        self.assertEqual(response.headers['X-Preview-Mode'], 'isolated-mock')

    def test_shell_and_checked_runtime_serve_without_exposing_adapter_source(self):
        with self.client.get('/') as response:
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
        for url in re.findall(r'(?:src|href)="(/preview/[^"?]+)', html):
            with self.client.get(url) as asset:
                self.assertEqual(asset.status_code, 200, url)
        response = self.client.get('/preview/workspace-runtime.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.FullWorkspace?.register', response.get_data(as_text=True))
        self.assertNotIn("const PREFIX='resonance-workspace-v1:'", response.get_data(as_text=True))
        chart = self.client.get('/preview/workspace-chart.js')
        self.assertEqual(chart.status_code, 200)
        adapted_chart = chart.get_data(as_text=True)
        self.assertNotEqual(adapted_chart, (preview.PROJECT / 'static' / 'workspace-chart.js').read_text(encoding='utf-8'))
        syntax = subprocess.run(['node', '--check', '-'], input=adapted_chart,
                                text=True, encoding='utf-8', capture_output=True, timeout=15)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        with patch.object(preview, 'load_module', side_effect=ValueError('anchor changed')):
            self.assertEqual(self.client.get('/preview/workspace-runtime.js').status_code, 503)
            self.assertEqual(self.client.get('/preview/workspace-chart.js').status_code, 503)
        self.assertEqual(self.client.get('/preview/runtime_adapter.py').status_code, 404)

    def test_market_has_completed_bar_metadata_without_mutating_previous_generator(self):
        now = datetime(2026, 11, 13, 10, 23, tzinfo=preview.simulation.BEIJING)
        for prefix in ('CZCE.MA', 'GFEX.lc', 'SHFE.au'):
            item = next(item for item in preview.catalogue() if item['symbol'].startswith(prefix))
            market = preview.market_provider(item['symbol'], now=now)
            self.assertEqual(market['source'], 'mock')
            self.assertFalse(market['fresh'])
            self.assertFalse(market['incremental'])
            self.assertEqual(market['as_of'], now.isoformat())
            for period, bars in market['periods'].items():
                closed, valid = preview.workspace_rules._bars(bars, now)
                self.assertTrue(valid)
                self.assertTrue(closed)
                self.assertEqual(bars[-1]['close'], market['price'])
                self.assertEqual(market['period_counts'][period], len(bars))
            original = preview.simulation.mock_market(item, now)
            self.assertNotIn('closed', original['periods']['m3'][0])
        response = self.client.get('/api/workspace/market?symbol=' + self.symbol, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['market']['symbol'], self.symbol)
        self.assertEqual(self.client.get('/api/workspace/market?symbol=UNKNOWN').status_code, 400)

    def test_actual_http_json_is_accepted_by_the_real_frontend_market_cache(self):
        base = '/api/workspace/market?symbol=' + self.symbol
        full = self.client.get(base, headers=self.headers).json['market']
        refreshed = self.client.get(base + '&history=' + full['history_token'], headers=self.headers).json['market']
        partial = self.client.get(base + '&preview=240', headers=self.headers).json['market']
        # This executes the production cache validator, not a Python copy of its
        # assumptions; missing tokens or mismatched counts must fail this test.
        script = r"""
const assert=require('node:assert/strict');
const Cache=require('./static/workspace-market-cache.js');
const packets=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const store=new Cache.Store();
for(const market of packets){
  const result=store.accept(market.symbol,market,store.begin(market.symbol));
  assert.equal(result.accepted,true,JSON.stringify({reason:result.reason,partial:market.partial}));
  assert.equal(result.ready,true);
  assert.equal(store.history(market.symbol),market.history_token);
  assert.equal(store.ready(market.symbol),true);
}
process.stdout.write('accepted '+packets.length+' real service packets');
"""
        result = subprocess.run(['node', '-e', script], input=json.dumps([full, refreshed, partial, full]),
                                text=True, encoding='utf-8', capture_output=True,
                                cwd=preview.PROJECT, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('accepted 4', result.stdout)
        now = datetime(2026, 11, 13, 10, 23, tzinfo=preview.simulation.BEIJING)
        first = preview.market_provider(self.symbol, now=now)
        unchanged = preview.market_provider(self.symbol, now=now + timedelta(seconds=1))
        advanced = preview.market_provider(self.symbol, now=now + timedelta(minutes=3))
        self.assertEqual(first['history_token'], unchanged['history_token'])
        self.assertNotEqual(first['history_token'], advanced['history_token'])

    def test_full_plan_history_review_risk_and_comments_persist_only_in_preview(self):
        body = {'base_revision': 0, 'plan': {'title': '样板计划', 'direction': 'long',
                'mode': 'continuation', 'enabled': False, 'objects': [],
                'planned_entry_price': 2600, 'stop_price': 2580}}
        url = '/api/workspace/plans/' + self.symbol
        saved = self.client.put(url, json=body, headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json['plan']['revision'], 1)
        risk = {'lots': 1, 'fee_per_lot': 2, 'slippage_ticks': 1, 'budget': 1000}
        self.assertEqual(self.client.put('/api/workspace/risk', json={'risk': risk}, headers=self.headers).status_code, 200)
        patched = self.client.post(url + '/patch', headers=self.headers,
                                   json={'base_revision': 1, 'changes': {'notes': '完整功能保留'}})
        self.assertEqual(patched.status_code, 200, patched.json)
        commented = self.client.post('/api/workspace/comments?symbol=' + self.symbol,
                                     headers=self.headers, json={'text': '本地伙伴评论'})
        self.assertEqual(commented.status_code, 200, commented.json)
        history = self.client.get('/api/workspace/history?symbol=' + self.symbol).json['history']
        self.assertEqual(len(history), 3)
        review = self.client.get('/api/workspace/review?symbol=' + self.symbol).json
        self.assertEqual(review['evaluation']['source'], 'mock')
        self.assertNotEqual(review['evaluation']['status'], 'ready')
        self.assertEqual(review['plan_revision'], 3)
        created = self.client.post('/api/workspace/boards?symbol=' + self.symbol,
                                  json={'title': '另一个计划'}, headers=self.headers)
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(len(self.client.get('/api/workspace/boards?symbol=' + self.symbol).json['boards']), 2)
        disk = json.loads((self.directory / 'workspace.json').read_text(encoding='utf-8'))
        self.assertEqual(len(disk['plans']), 2)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'workspace.json', 'workspace.json.lock'})

    def test_two_local_browsers_share_plans_presence_and_events_with_private_risk(self):
        second = self.app.test_client()
        session = second.get('/api/workspace/session').json
        headers = {'X-Workspace-CSRF': session['csrf_token'], 'X-Workspace-Candle-Protocol': '2'}
        response = second.post('/api/workspace/profile', json={'name': '样板伙伴'}, headers=headers)
        self.assertEqual(response.status_code, 200)
        second.post('/api/workspace/presence', headers=headers, json={'symbol': self.symbol, 'period': 'm30'})
        presence = self.client.get('/api/workspace/presence?symbol=' + self.symbol).json['presence']
        self.assertTrue(any(item['name'] == '样板伙伴' for item in presence))
        self.assertEqual(second.get('/api/workspace/risk').json['risk'], {})
        response = self.client.get('/api/workspace/events?candle_protocol=2&symbol=' + self.symbol, buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/event-stream')
        iterator = iter(response.response)
        self.assertIn(b'retry:', next(iterator))
        self.assertIn(b'event: workspace', next(iterator))
        response.close()


if __name__ == '__main__':
    unittest.main()
