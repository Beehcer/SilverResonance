"""Production launch/proxy checks: isolated files and fake feed, never TQ login."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import serve_workspace as runtime
import user_settings
import workspace_rules
from workspace_api import register_workspace
from workspace_store import WorkspaceStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.rules = self.directory / "rules.md"
        self.rules.write_text("# Test rules\n", encoding="utf-8")
        self.settings = {"tq_user": "test-user", "tq_password": "private-test-password",
                         "symbol": "SHFE.ag2702", "mock_mode": True}
        (self.directory / "user_config.json").write_text(json.dumps(self.settings), encoding="utf-8")
        self.environment = patch.dict(os.environ, {"SILVER_DATA_DIR": str(self.directory),
                                                   "SILVER_RULES_PATH": str(self.rules)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.version = patch.object(workspace_rules, "IMPLEMENTED_RULE_VERSION",
                                    hashlib.sha256(self.rules.read_bytes()).hexdigest())
        self.version.start()
        self.addCleanup(self.version.stop)

    def module(self):
        app = Flask(__name__)
        app.add_url_rule("/setup", endpoint="setup", view_func=lambda: "private")
        app.add_url_rule("/workspace", endpoint="workspace", view_func=lambda: "workspace")
        return types.SimpleNamespace(app=app, config=types.SimpleNamespace(MOCK_MODE=True),
                                     apply_user_settings=Mock(), _set_state=Mock(),
                                     start_data_thread=Mock(), _data_thread=Mock(is_alive=lambda: True))

    def test_external_directory_retains_desktop_default(self):
        self.assertEqual(user_settings.app_dir(), str(self.directory))
        self.assertEqual(user_settings.load_settings(), self.settings)
        with patch.dict(os.environ, {"SILVER_DATA_DIR": ""}):
            self.assertEqual(user_settings.app_dir(), str(Path(user_settings.__file__).parent))

    def test_check_has_no_application_import_or_market_connection(self):
        with patch("workspace_backup.start_workspace_backup") as backup_start, \
                patch.object(runtime.importlib.util, "find_spec", return_value=object()), \
                patch.object(runtime.importlib, "import_module") as imported:
            self.assertEqual(runtime.run_server(runtime.parse_args(["--check"])), 0)
            imported.assert_not_called()
            backup_start.assert_not_called()
        self.assertTrue((self.directory / "state").is_dir())
        self.assertEqual(list((self.directory / "state").iterdir()), [])

    def test_missing_credentials_and_rule_mismatch_stop_before_import(self):
        for key in ("tq_user", "tq_password", "symbol"):
            with self.subTest(key=key), patch.object(user_settings, "load_settings", return_value={**self.settings, key: ""}), \
                    patch.object(runtime.importlib, "import_module") as imported:
                with self.assertRaises(runtime.RuntimeConfigError):
                    runtime.check_runtime()
                imported.assert_not_called()
        with patch.object(workspace_rules, "IMPLEMENTED_RULE_VERSION", "different"):
            with self.assertRaisesRegex(runtime.RuntimeConfigError, "规则版本"):
                runtime.check_runtime()
        with patch.object(runtime.importlib.util, "find_spec", return_value=None):
            with self.assertRaisesRegex(runtime.RuntimeConfigError, "运行依赖"):
                runtime.check_runtime()

    def test_data_dir_is_set_before_application_import_and_mock_forced_off(self):
        alternate = self.directory / "external"
        alternate.mkdir()
        (alternate / "user_config.json").write_text(json.dumps(self.settings), encoding="utf-8")
        with patch.object(runtime.importlib.util, "find_spec", return_value=object()):
            self.assertEqual(runtime.check_runtime(str(alternate)), alternate)
        module = self.module()
        def import_app(name):
            self.assertEqual(name, "app")
            self.assertEqual(user_settings.app_dir(), str(alternate))
            return module
        with patch.object(runtime.importlib, "import_module", side_effect=import_app):
            runtime.prepare_application()
        self.assertFalse(module.config.MOCK_MODE)
        module._set_state.assert_called_once_with(mock=False)
        module.start_data_thread.assert_not_called()
        self.assertFalse(module.app.debug)
        self.assertFalse(module.app.config["TEMPLATES_AUTO_RELOAD"])

    def test_only_workspace_routes_and_local_status_are_exposed(self):
        module = self.module()
        with patch.object(runtime.importlib, "import_module", return_value=module):
            runtime.prepare_application()
        client = module.app.test_client()
        self.assertEqual(client.get("/workspace").status_code, 200)
        for path in ("/setup", "/monitor", "/api/config", "/api/trading"):
            self.assertEqual(client.get(path).status_code, 404)
        status = client.get("/healthz", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(status.json, {"status": "ok", "feed_thread": "running"})
        for secret in self.settings.values():
            if isinstance(secret, str):
                self.assertNotIn(secret, status.get_data(as_text=True))
        self.assertEqual(client.get("/healthz", environ_overrides={"REMOTE_ADDR": "203.0.113.8"}).status_code, 404)
        self.assertEqual(client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.8"}).status_code, 404)
        module._data_thread = Mock(is_alive=lambda: False)
        self.assertEqual(client.get("/healthz").status_code, 503)

    def test_singleton_lock_is_released(self):
        with runtime.RuntimeLock(self.directory):
            with self.assertRaises(runtime.RuntimeConfigError):
                with runtime.RuntimeLock(self.directory):
                    self.fail("Second producer acquired runtime lock")
        with runtime.RuntimeLock(self.directory):
            pass

    def test_feed_starts_once_after_listener_and_stops_monitor_on_shutdown(self):
        module = self.module()
        module._workspace_store = types.SimpleNamespace(path=self.directory / "state" / "workspace.json")
        order = []
        module.start_data_thread.side_effect = lambda: order.append("feed")
        server = Mock()
        server.run.side_effect = lambda: order.append("serve")
        def listener(app, **options):
            self.assertEqual(options["host"], "127.0.0.1")
            self.assertEqual(options["threads"], 24)
            self.assertFalse(options["clear_untrusted_proxy_headers"])
            self.assertFalse(options["expose_tracebacks"])
            order.append("bind")
            return server
        waitress = types.SimpleNamespace(create_server=listener)
        backups = Mock()
        def start_backups(source):
            self.assertEqual(source, module._workspace_store.path)
            order.append("backup")
            return backups
        with patch.object(runtime, "check_runtime", return_value=self.directory), \
                patch.object(runtime, "prepare_application", return_value=module), \
                patch("workspace_backup.start_workspace_backup", side_effect=start_backups), \
                patch.dict(sys.modules, {"waitress": waitress}):
            self.assertEqual(runtime.run_server(runtime.parse_args(["--behind-proxy"])), 0)
        self.assertEqual(order, ["bind", "backup", "feed", "serve"])
        module.start_data_thread.assert_called_once()
        backups.stop.assert_called_once()
        server.close.assert_called_once()

    def test_dead_feed_exits_but_idle_alive_feed_does_not(self):
        module = self.module()
        stop = Mock()
        stop.wait.side_effect = [False, True]
        exit_process = Mock()
        runtime.watch_feed(module, stop, exit_process=exit_process)
        exit_process.assert_not_called()
        module._data_thread = Mock(is_alive=lambda: False)
        stop.wait.side_effect = [False]
        runtime.watch_feed(module, stop, exit_process=exit_process)
        exit_process.assert_called_once_with(1)

    def test_startup_exception_does_not_print_secret(self):
        with patch.object(runtime, "run_server", side_effect=ValueError(self.settings["tq_password"])), \
                self.assertLogs("silver.runtime", level="ERROR") as logs:
            self.assertEqual(runtime.main([]), 1)
        self.assertNotIn(self.settings["tq_password"], "\n".join(logs.output))


class RealWaitressProxyTests(unittest.TestCase):
    """Real local HTTP requests exercise Waitress header filtering plus CSRF."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = Flask(__name__)
        store = WorkspaceStore(Path(directory.name) / "workspace.json")
        register_workspace(self.app, store, lambda symbol: {}, lambda: [], lambda: {}, lambda *args: {})
        self.app.add_url_rule("/scheme", view_func=self.scheme)

    @staticmethod
    def scheme():
        from flask import request, jsonify
        return jsonify(scheme=request.scheme, host=request.host, address=request.remote_addr)

    def start(self, behind_proxy):
        from waitress import create_server
        if behind_proxy:
            self.app.wsgi_app = runtime.LocalProxyScheme(self.app.wsgi_app)
        server = create_server(self.app, host="127.0.0.1", port=0, threads=2,
                               clear_untrusted_proxy_headers=not behind_proxy,
                               asyncore_loop_timeout=0.05)
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        def cleanup():
            server.close()
            server.task_dispatcher.shutdown(timeout=1)
            worker.join(timeout=2)
        self.addCleanup(cleanup)
        return int(server.effective_port)

    @staticmethod
    def request(port, path, *, headers=None, method="GET", payload=None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            header_values = {"Host": "resonance.cg-x.com", "Connection": "close", **(headers or {})}
            if payload is not None:
                header_values["Content-Type"] = "application/json"
            client.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=header_values)
            response = client.getresponse()
            content = response.read()
            return response.status, dict(response.getheaders()), json.loads(content) if content else {}
        finally:
            client.close()

    def test_https_session_and_same_origin_mutation_through_waitress(self):
        port = self.start(True)
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.4",
                   "Origin": "https://resonance.cg-x.com", "X-Forwarded-Host": "evil.example"}
        status, response_headers, body = self.request(port, "/api/workspace/session", headers=headers)
        self.assertEqual(status, 200, body)
        self.assertIn("Secure", response_headers["Set-Cookie"])
        mutation = {**headers, "Cookie": response_headers["Set-Cookie"].split(";")[0],
                    "X-Workspace-CSRF": body["csrf_token"]}
        status, _, body = self.request(port, "/api/workspace/profile", method="POST",
                                       headers=mutation, payload={"name": "Proxy test"})
        self.assertEqual(status, 200, body)
        mutation["Origin"] = "https://evil.example"
        self.assertEqual(self.request(port, "/api/workspace/profile", method="POST",
                                      headers=mutation, payload={"name": "Attack"})[0], 403)
        _, _, scheme = self.request(port, "/scheme", headers=headers)
        self.assertEqual(scheme, {"scheme": "https", "host": "resonance.cg-x.com", "address": "127.0.0.1"})

    def test_direct_mode_does_not_trust_forged_proxy_headers(self):
        port = self.start(False)
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"}
        _, _, scheme = self.request(port, "/scheme", headers=headers)
        self.assertEqual(scheme["scheme"], "http")
        self.assertEqual(scheme["host"], "resonance.cg-x.com")
        headers["Origin"] = "https://resonance.cg-x.com"
        self.assertEqual(self.request(port, "/api/workspace/session", headers=headers)[0], 403)

    def test_proxy_middleware_does_not_trust_remote_peer(self):
        self.app.wsgi_app = runtime.LocalProxyScheme(self.app.wsgi_app)
        response = self.app.test_client().get("/scheme", headers={"X-Forwarded-Proto": "https"},
                                              environ_overrides={"REMOTE_ADDR": "203.0.113.4"})
        self.assertEqual(response.json["scheme"], "http")


if __name__ == "__main__":
    unittest.main()
