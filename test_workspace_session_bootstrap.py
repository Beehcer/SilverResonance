"""One session/catalogue read, with old-server and identity-safe fallbacks."""
from pathlib import Path
import tempfile
import unittest

from flask import Flask

import test_workspace_dual_startup as shell_tests
from workspace_api import COOKIE_NAME, register_workspace
from workspace_store import WorkspaceStore


class SessionCatalogueApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = WorkspaceStore(Path(directory.name) / "workspace.json")
        self.app = Flask(__name__)
        self.app.testing = True
        self.catalogue = [{"symbol": "CZCE.TA701", "name": "PTA701", "tick": 2, "multiplier": 5}]
        self.calls = 0
        self.fail_catalogue = False

        def symbols():
            self.calls += 1
            if self.fail_catalogue:
                raise RuntimeError("catalogue unavailable")
            return self.catalogue

        def unexpected(*args):
            self.fail("Session bootstrap must not load market, rules or risk")

        register_workspace(self.app, self.store, unexpected, symbols, unexpected, unexpected)
        self.client = self.app.test_client()

    def test_optional_bootstrap_has_same_cookie_identity_and_catalogue_as_existing_endpoint(self):
        response = self.client.get("/api/workspace/session?bootstrap=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["bootstrap"], {"symbols": self.catalogue})
        self.assertEqual(self.calls, 1)
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", response.headers["Set-Cookie"])
        self.assertIn("Path=/api/workspace", response.headers["Set-Cookie"])
        self.assertEqual(response.headers["Cache-Control"], "no-store, private")
        cookie = self.client.get_cookie(COOKIE_NAME, path="/api/workspace")
        self.assertEqual(self.store.authenticate(cookie.value)["id"], response.json["user"]["id"])
        again = self.client.get("/api/workspace/session?bootstrap=1")
        self.assertEqual(again.json["user"]["id"], response.json["user"]["id"])
        self.assertEqual(again.json["csrf_token"], response.json["csrf_token"])
        self.assertNotIn("Set-Cookie", again.headers)
        self.assertEqual(len(again.json["members"]), 1)
        old = self.client.get("/api/workspace/symbols")
        self.assertEqual(old.json["symbols"], response.json["bootstrap"]["symbols"])
        self.assertNotIn("risk", response.json["bootstrap"])
        self.assertNotIn("plans", response.json["bootstrap"])

    def test_plain_session_unknown_options_and_profile_keep_existing_response(self):
        for suffix in ("", "?bootstrap=0", "?bootstrap=unknown", "?future_option=1"):
            response = self.client.get("/api/workspace/session" + suffix)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("bootstrap", response.json)
        updated = self.client.post("/api/workspace/profile?bootstrap=1", json={"name": "QA"},
                                   headers={"X-Workspace-CSRF": response.json["csrf_token"]})
        self.assertEqual(updated.status_code, 200)
        self.assertNotIn("bootstrap", updated.json)
        self.assertEqual(self.calls, 0)

    def test_origin_check_runs_before_identity_creation_or_catalogue_read(self):
        for headers in ({"Origin": "https://attacker.example"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            response = self.client.get("/api/workspace/session?bootstrap=1", headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("Set-Cookie", response.headers)
        self.assertIsNone(self.client.get_cookie(COOKIE_NAME, path="/api/workspace"))
        self.assertEqual(self.calls, 0)
        response = self.client.get("/api/workspace/session?bootstrap=1", base_url="https://localhost",
                                   headers={"Origin": "https://localhost", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_catalogue_failure_preserves_session_for_endpoint_retry(self):
        self.fail_catalogue = True
        with self.assertLogs(self.app.logger, level="WARNING"):
            response = self.client.get("/api/workspace/session?bootstrap=1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["authenticated"])
        self.assertNotIn("bootstrap", response.json)
        self.assertIn("Set-Cookie", response.headers)
        self.fail_catalogue = False
        retry = self.client.get("/api/workspace/symbols")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json["symbols"], self.catalogue)
        restored = self.client.get("/api/workspace/session")
        self.assertEqual(restored.json["user"]["id"], response.json["user"]["id"])
        self.assertEqual(len(restored.json["members"]), 1)
        self.assertEqual(self.calls, 2)

    def test_bootstrap_does_not_change_mobile_view_permissions(self):
        response = self.client.get("/api/workspace/session?bootstrap=1", headers={"User-Agent": "iPhone"})
        self.assertTrue(response.json["view_only"])
        self.assertTrue(response.json["mobile_device"])
        self.assertEqual(response.json["bootstrap"]["symbols"], self.catalogue)


class SessionCatalogueShellTests(unittest.TestCase):
    run_startup = shell_tests.ShellStartupTests.run_startup

    def test_combined_response_opens_saved_dual_layout_without_second_request(self):
        self.run_startup(r"""
session.bootstrap={symbols:response.symbols,future_option:'ignored'};
saved.set(KEY+'owner',JSON.stringify({panes:[{id:'pane-a',symbol:'MA'},{id:'pane-b',symbol:'SP'}],activeId:'pane-b'}));
await start();assert.deepEqual(calls,['/session?bootstrap=1','persist']);
assert.deepEqual(created.map(p=>p.symbol),['MA','SP']);assert.equal(state.activeId,'pane-b');
assert.equal(window.DualWorkspaceBootstrap.session.user.id,'owner');
assert.deepEqual(window.DualWorkspaceBootstrap.symbols.symbols,response.symbols);
assert.equal(status.hidden,true);
""")

    def test_bootstrap_preserves_deep_link_including_explicit_empty_board(self):
        self.run_startup(r"""
session.bootstrap={symbols:response.symbols};
saved.set(KEY+'owner',JSON.stringify({panes:[{id:'pane-a',symbol:'SP'}],activeId:'pane-a'}));
location.href='https://example.test/workspace?symbol=MA&board=';
await start();assert.deepEqual(calls,['/session?bootstrap=1','persist']);
assert.deepEqual(created,[{id:'pane-a',symbol:'MA',board:''}]);assert.equal(urlChanges.at(-1),'/workspace');
""")

    def test_absent_or_invalid_bootstrap_uses_legacy_catalogue_without_new_session(self):
        for value in ("undefined", "null", "{}", "{symbols:{}}", "{symbols:[null]}",
                      "{symbols:[{}]}", "{symbols:['']}", "{symbols:[{symbol:2}]}"):
            self.run_startup("session.bootstrap=" + value + r""";
await start();assert.deepEqual(calls,['/session?bootstrap=1','/symbols','persist']);
assert.equal(created.length,1);assert.equal(created[0].symbol,'MA');
assert.equal(window.DualWorkspaceBootstrap.session.user.id,'owner');assert.equal(status.hidden,true);
""")

    def test_empty_bootstrap_is_authoritative_and_has_existing_recovery(self):
        self.run_startup(r"""
session.bootstrap={symbols:[]};await start();assert.deepEqual(calls,['/session?bootstrap=1']);
assert.equal(created.length,0);assert.equal(status.hidden,false);
assert.match(startupNodes['startup-label'].textContent,/暂无可用品种/);
assert.equal(startupNodes['startup-actions'].hidden,false);
""")


if __name__ == "__main__":
    unittest.main()
