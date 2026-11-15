"""Cache coverage is observable without causing history loads or private writes."""
from pathlib import Path
import tempfile
import unittest

from flask import Flask

from workspace_api import register_workspace
from workspace_store import WorkspaceStore


class CacheStatusApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = WorkspaceStore(Path(self.directory.name) / "workspace.json")
        self.status = {"total": 20, "ready": 4, "symbols": []}
        self.calls = 0
        self.error = None
        self.app = self.make_app(self.provider)
        self.client = self.app.test_client()

    def provider(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.status

    def make_app(self, provider):
        app = Flask(__name__)
        app.testing = True
        def unexpected(*args):
            self.fail("Coverage reads must not start market/rules/plan work")
        register_workspace(app, self.store, unexpected, unexpected, unexpected,
                           unexpected, cache_status_provider=provider)
        return app

    def test_authentication_precedes_inventory_access(self):
        result = self.client.get("/api/workspace/cache-status")
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.calls, 0)

    def test_inventory_is_read_only_private_and_available_to_mobile(self):
        self.client.get("/api/workspace/session")
        before = self.store.path.read_bytes()
        result = self.client.get("/api/workspace/cache-status", headers={"User-Agent": "iPhone"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json, {"status": "ok", "cache": self.status})
        self.assertEqual(result.headers["Cache-Control"], "no-store, private")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unconfigured_provider_returns_recoverable_error(self):
        client = self.make_app(None).test_client()
        client.get("/api/workspace/session")
        self.assertEqual(client.get("/api/workspace/cache-status").status_code, 503)

    def test_provider_failure_does_not_disclose_error_or_break_session(self):
        session = self.client.get("/api/workspace/session").json
        self.error = RuntimeError("secret-path-and-provider-details")
        result = self.client.get("/api/workspace/cache-status")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("secret-path", result.text)
        self.error = None
        self.assertEqual(self.client.get("/api/workspace/cache-status").status_code, 200)
        self.assertEqual(self.client.get("/api/workspace/session").json["user"]["id"], session["user"]["id"])

    def test_malformed_provider_is_not_reported_as_success(self):
        self.client.get("/api/workspace/session")
        self.status = None
        self.assertEqual(self.client.get("/api/workspace/cache-status").status_code, 503)


if __name__ == "__main__":
    unittest.main()
