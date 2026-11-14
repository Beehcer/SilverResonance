"""Production page wiring, isolation and generated asset regression checks."""
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import Flask

import workspace_web


ROOT = Path(__file__).parent


class DualWorkspaceWebTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
        app.add_url_rule("/", view_func=workspace_web.render_workspace_page)
        app.add_url_rule("/workspace", endpoint="workspace", view_func=workspace_web.render_workspace_page)
        workspace_web.register_workspace_assets(app)
        self.client = app.test_client()

    def test_desktop_uses_shell_and_mobile_keeps_original_readonly_page(self):
        for route in ("/", "/workspace?symbol=CZCE.MA701&board=existing"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"workspace-shell-runtime.js", response.data)
            self.assertNotIn(b"/preview/", response.data)
        for route, agent in (("/workspace?view=single", "Desktop"), ("/workspace", "Mozilla iPhone Mobile")):
            response = self.client.get(route, headers={"User-Agent": agent})
            self.assertIn(b"mobile-readonly-label", response.data)
            self.assertIn(b'workspace.js', response.data)
            self.assertNotIn(b"workspace-dual-embed.js", response.data)

    def test_panes_use_existing_tools_with_production_adapters(self):
        for pane in ("pane-a", "pane-b"):
            response = self.client.get("/workspace?pane=" + pane)
            self.assertEqual(response.status_code, 200)
            for asset in (b"workspace-dual-runtime.js", b"workspace-dual-chart.js", b"workspace-core.js", b"workspace-dual-embed.css"):
                self.assertIn(asset, response.data)
            self.assertIn(b'id="plan-form"', response.data)
            self.assertNotIn(b'/static/workspace.js?', response.data)
            self.assertNotIn(b'/static/workspace-chart.js?', response.data)
        self.assertEqual(self.client.get("/workspace?pane=arbitrary").status_code, 400)

    def test_generated_assets_do_not_use_preview_storage_or_mock_sources(self):
        for asset in ("runtime", "chart"):
            response = self.client.get("/static/workspace-dual-" + asset + ".js")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/javascript")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
            for forbidden in (b"resonance-full-dual-preview-v1", b"migration:empty-custom-groups", b"/preview/", b"isolated-mock"):
                self.assertNotIn(forbidden, response.data)
        response = self.client.get("/static/workspace-dual-runtime.js")
        self.assertIn(b"resonance-workspace-v1:", response.data)

    def test_critical_bundles_preserve_dependencies_and_cases_are_lazy(self):
        from html.parser import HTMLParser

        class ActiveScripts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_template = 0
                self.urls = []

            def handle_starttag(self, tag, attrs):
                if tag == "template":
                    self.in_template += 1
                if tag == "script" and not self.in_template:
                    self.urls.extend(value for key, value in attrs if key == "src")

            def handle_endtag(self, tag):
                if tag == "template":
                    self.in_template -= 1

        for route, count in (("/", 1), ("/workspace?pane=pane-a", 3)):
            parser = ActiveScripts()
            parser.feed(self.client.get(route).text)
            self.assertEqual(len(parser.urls), count)
            self.assertFalse(any("workspace-cases" in url for url in parser.urls))
        shell = self.client.get("/static/workspace-shell-runtime.js").data
        for module in (b"WorkspaceHistoryCache", b"WorkspaceMarketCache", b"WorkspaceMarketHub"):
            self.assertIn(module, shell)
        core = self.client.get("/static/workspace-core.js").data
        for module in (b"WorkspaceHistoryCache", b"WorkspaceCases", b"cases-resources"):
            self.assertIn(module, core)

    def test_pages_load_versioned_oss_assets_without_redirecting_api_or_iframe(self):
        from html.parser import HTMLParser
        class Assets(HTMLParser):
            urls = None
            def handle_starttag(self, tag, attrs):
                if tag in ('script', 'link'):
                    self.urls.extend(v for k,v in attrs if k in ('src','href'))
        for route in ('/', '/workspace?pane=pane-a', '/workspace?view=single'):
            response = self.client.get(route)
            parser = Assets(); parser.urls=[]; parser.feed(response.text)
            self.assertTrue(parser.urls)
            for url in parser.urls:
                self.assertRegex(url, r'^https://silver-resonance\.oss-cn-hangzhou\.aliyuncs\.com/workspace/[a-f0-9]{24}/')

    def test_oss_manifest_matches_current_public_payloads(self):
        import hashlib, json
        from scripts.publish_workspace_assets import payloads
        manifest=json.loads((ROOT/'static/workspace-assets.json').read_text())
        files=payloads()
        self.assertEqual(set(manifest['sha256']),set(files))
        for name,raw in files.items():
            self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['sha256'][name])

    def test_incompatible_source_fails_explicitly(self):
        with patch.object(workspace_web, "adapt_workspace_js", side_effect=ValueError("bad anchor")):
            self.assertEqual(self.client.get("/static/workspace-dual-runtime.js").status_code, 503)


if __name__ == "__main__":
    unittest.main()
