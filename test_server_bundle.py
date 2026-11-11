"""Deployment archives must not inherit credentials or personal workspace data."""

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from scripts.build_server_bundle import (
    ASSET_FILES,
    BundleError,
    DEPLOY_FILES,
    MANIFEST_NAME,
    REQUIRED_FILES,
    RUNTIME_PY_FILES,
    _is_link,
    build_bundle,
)


class ServerBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in REQUIRED_FILES:
            self.write(name, "# runtime fixture\n" if name.endswith(".py") else "runtime fixture\n")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_archive_has_only_allowlisted_files_and_verifiable_manifest(self):
        excluded = (
            "test_secret.py", "fetch_klines.py", "new_runtime.py", "personal.py",
            "user_config.json", "state/workspace.json", "state/contracts.json",
            "state/signals_state.json", "state/nested/rules.md", ".git/config",
            ".env", "backtest/old.zip", "logs/runtime.log", "private-rules.md",
            "docs/private.md", "static/private.html", "static/state/notes.js",
            "static/user_config.json", "templates/notes.html", "static/.env",
            "deploy/.env", "deploy/runtime.env", "deploy/user_config.json",
            "deploy/credentials.pem", "deploy/private.md", "requirements-private.txt",
        )
        for name in excluded:
            self.write(name, "PRIVATE_DATA_MUST_STAY_LOCAL")
        result = build_bundle(self.root)
        archive = Path(result["archive"])
        self.assertEqual(archive.parent, self.root / "backtest")
        self.assertEqual(result["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(set(bundle.namelist()), set(REQUIRED_FILES) | {MANIFEST_NAME})
            manifest = json.loads(bundle.read(MANIFEST_NAME))
            self.assertEqual(manifest["files"], result["files"])
            for entry in manifest["files"]:
                content = bundle.read(entry["path"])
                self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
                self.assertEqual(entry["size"], len(content))
                self.assertNotIn(b"PRIVATE_DATA_MUST_STAY_LOCAL", content)
            self.assertEqual([entry["path"] for entry in manifest["files"]], sorted(REQUIRED_FILES))

    def test_missing_runtime_assets_or_deploy_prerequisites_fail_without_overwriting(self):
        output = self.write("backtest/existing.zip", "retain previous valid archive")
        for name in ("serve_workspace.py", "workspace_market.py", ASSET_FILES[0], "static/workspace.js",
                     "requirements-server.txt", "docs/DEPLOYMENT.md", "deploy/runtime.env.example"):
            with self.subTest(missing=name):
                path = self.root / name
                content = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaisesRegex(BundleError, "Missing required runtime file"):
                        build_bundle(self.root, output)
                    self.assertEqual(output.read_text(encoding="utf-8"), "retain previous valid archive")
                    self.assertFalse(list(output.parent.glob(".server-bundle-*")))
                finally:
                    path.write_bytes(content)

    def test_absent_files_do_not_create_partial_archive(self):
        (self.root / RUNTIME_PY_FILES[0]).unlink()
        with self.assertRaises(BundleError):
            build_bundle(self.root)
        self.assertFalse((self.root / "backtest").exists())

    def test_new_local_imports_must_be_reviewed_instead_of_silently_omitted(self):
        self.write("unlisted.py", "# unreviewed\n")
        for statement in ("import unlisted", "from unlisted import value", "importlib.import_module('unlisted')"):
            with self.subTest(statement=statement):
                self.write("app.py", statement)
                with self.assertRaisesRegex(BundleError, "Unlisted local runtime dependency"):
                    build_bundle(self.root)

    def test_local_package_dependencies_are_not_silently_omitted(self):
        self.write("new_package/__init__.py", "# local package\n")
        self.write("app.py", "from new_package import runtime\n")
        with self.assertRaisesRegex(BundleError, "Unlisted local runtime dependency"):
            build_bundle(self.root)

    def test_invalid_python_fails_before_distribution(self):
        self.write("app.py", "def invalid(\n")
        with self.assertRaisesRegex(BundleError, "Cannot parse required Python file"):
            build_bundle(self.root)

    def test_embedded_config_credentials_are_rejected_without_exposing_value(self):
        for declaration in ("TQ_USER = 'private-account'", "TQ_PASSWORD: str = 'private-password'"):
            with self.subTest(field=declaration.split(" ")[0]):
                self.write("config.py", declaration)
                with self.assertRaises(BundleError) as caught:
                    build_bundle(self.root)
                self.assertIn("embedded market credentials", str(caught.exception))
                self.assertNotIn("private-account", str(caught.exception))
                self.assertNotIn("private-password", str(caught.exception))

    def test_template_references_cannot_silently_outgrow_asset_allowlist(self):
        self.write("templates/workspace.html", '<script src="/static/unlisted.js?v=1"></script>')
        self.write("static/unlisted.js", "unreviewed private content")
        with self.assertRaisesRegex(BundleError, "Unlisted template asset"):
            build_bundle(self.root)
        self.write("templates/workspace.html", '<link href="/static/workspace.css?v=1" rel="stylesheet">')
        build_bundle(self.root)

    def test_windows_reparse_points_are_recognized_without_symlink_privileges(self):
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0, st_file_attributes=0x400)):
            self.assertTrue(_is_link(self.root / "junction"))
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0, st_file_attributes=0)):
            self.assertFalse(_is_link(self.root / "regular"))

    def test_link_boundary_checks_run_before_any_target_is_read_or_written(self):
        for blocked in (self.root / "static", self.root / "static/workspace.js", self.root / "backtest"):
            with self.subTest(path=blocked.name):
                def report_link(path):
                    return path == blocked or _is_link(path)
                with patch("scripts.build_server_bundle._is_link", side_effect=report_link):
                    with self.assertRaises(BundleError):
                        build_bundle(self.root)
                self.assertFalse((self.root / "backtest").exists())

    def test_output_cannot_escape_ignored_directory(self):
        for output in (self.root / "release.zip", Path("backtest/../release.zip"),
                       Path("backtest/release.txt"), self.root.parent / "escape.zip"):
            with self.subTest(output=output):
                with self.assertRaises(BundleError):
                    build_bundle(self.root, output)
        result = build_bundle(self.root, Path("backtest/releases/server.zip"))
        self.assertTrue(Path(result["archive"]).is_file())

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlink creation unavailable: {type(error).__name__}")

    def test_required_file_cannot_be_a_symlink_to_private_data(self):
        private = self.write("private.txt", "DO_NOT_PACKAGE")
        target = self.root / "static/workspace.js"
        target.unlink()
        self.symlink(target, private)
        with self.assertRaisesRegex(BundleError, "must not be a link"):
            build_bundle(self.root)

    def test_required_directory_cannot_be_a_symlink(self):
        original = self.root / "static"
        relocated = self.root / "private-static"
        original.rename(relocated)
        self.symlink(original, relocated, directory=True)
        with self.assertRaisesRegex(BundleError, "must not be a link"):
            build_bundle(self.root)

    def test_output_cannot_follow_symlink(self):
        target = self.root / "outside"
        target.mkdir()
        self.symlink(self.root / "backtest", target, directory=True)
        with self.assertRaisesRegex(BundleError, "symlink or junction"):
            build_bundle(self.root)
        self.assertEqual(list(target.iterdir()), [])

    def test_build_is_reproducible_and_never_executes_application(self):
        self.write("app.py", "raise RuntimeError('Do not execute during packaging')\n")
        first = build_bundle(self.root, Path("backtest/first.zip"))
        second = build_bundle(self.root, Path("backtest/second.zip"))
        self.assertEqual(first["sha256"], second["sha256"])

    def test_allowlist_is_explicit_unique_and_contains_external_setup_instructions(self):
        self.assertEqual(len(REQUIRED_FILES), len(set(REQUIRED_FILES)))
        self.assertIn("docs/DEPLOYMENT.md", DEPLOY_FILES)
        self.assertIn("deploy/README.md", DEPLOY_FILES)
        self.assertNotIn("run_workspace.py", RUNTIME_PY_FILES)
        self.assertTrue(all("/" not in name and name.endswith(".py") for name in RUNTIME_PY_FILES))


if __name__ == "__main__":
    unittest.main()
