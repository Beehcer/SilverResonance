"""Build a reproducible server ZIP from an explicit, reviewable allowlist.

This module never imports the application, reads user configuration, starts a
market feed, or includes local state. Run from any directory:

    python scripts/build_server_bundle.py

The resulting ZIP and its SHA-256/file inventory are written/reported locally.
Server configuration and the private trading-rule document must be provisioned
separately, following docs/DEPLOYMENT.md inside the bundle.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile


# Keep this list explicit. A new root-level Python file is never automatically
# trusted for distribution, even if it has a plausible application filename.
RUNTIME_PY_FILES = (
    "app.py",
    "config.py",
    "double_bottom.py",
    "entry_logic.py",
    "fundamentals.py",
    "market_structure.py",
    "outlook.py",
    "outlook_store.py",
    "plan_board_demo.py",
    "resonance.py",
    "serve_workspace.py",
    "symbols.py",
    "trade_plan.py",
    "trading.py",
    "user_settings.py",
    "workspace_api.py",
    "workspace_market.py",
    "workspace_rules.py",
    "workspace_store.py",
)

ASSET_FILES = (
    "templates/workspace.html",
    "static/plan-board-model.js",
    "static/workspace-cases-data.js",
    "static/workspace-cases.css",
    "static/workspace-cases.js",
    "static/workspace-chart.js",
    "static/workspace-editing.js",
    "static/workspace-layers.js",
    "static/workspace-layouts.js",
    "static/workspace-merge.js",
    "static/workspace-market-cache.js",
    "static/workspace-market-preview.js",
    "static/workspace-symbols.js",
    "static/workspace-shortcuts.js",
    "static/workspace-presence.js",
    "static/workspace-properties.js",
    "static/workspace-ticket.js",
    "static/workspace.css",
    "static/workspace.js",
)

DEPLOY_FILES = (
    ".dockerignore",
    "requirements.txt",
    "requirements-server.txt",
    "deploy/Dockerfile",
    "deploy/compose.yaml",
    "deploy/runtime.env.example",
    "deploy/user_config.example.json",
    "deploy/silver-resonance.service",
    "deploy/nginx-workspace.conf.example",
    "deploy/README.md",
    "docs/DEPLOYMENT.md",
)

REQUIRED_FILES = tuple(sorted(RUNTIME_PY_FILES + ASSET_FILES + DEPLOY_FILES))
MANIFEST_NAME = "bundle-manifest.json"
DEFAULT_ARCHIVE = "silver-resonance-server.zip"


class BundleError(Exception):
    """A packaging prerequisite or boundary was violated."""


def _is_link(path: Path) -> bool:
    """Include Windows junctions/reparse points, not just POSIX symlinks."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_file(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
        raise BundleError(f"Invalid bundle path: {relative}")
    current = root
    for part in parts:
        current = current / part
        try:
            if _is_link(current):
                raise BundleError(f"Required runtime path must not be a link: {relative}")
        except FileNotFoundError:
            raise BundleError(f"Missing required runtime file: {relative}") from None
    if not current.is_file():
        raise BundleError(f"Required runtime path is not a file: {relative}")
    return current


def _check_imports(root: Path, payloads: dict[str, bytes]) -> None:
    """Fail if code adds an unlisted local dependency; never import that code."""
    bundled_modules = {Path(name).stem for name in RUNTIME_PY_FILES}
    for filename in RUNTIME_PY_FILES:
        try:
            tree = ast.parse(payloads[filename].decode("utf-8-sig"), filename=filename)
        except (SyntaxError, UnicodeError):
            raise BundleError(f"Cannot parse required Python file: {filename}") from None
        dependencies = set()
        for node in ast.walk(tree):
            if filename == "config.py" and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id in {"TQ_USER", "TQ_PASSWORD"} for target in targets):
                    if isinstance(node.value, ast.Constant) and node.value.value:
                        raise BundleError("config.py contains embedded market credentials; use external user_config.json.")
            if isinstance(node, ast.Import):
                dependencies.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                dependencies.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and node.args:
                # The server deliberately imports app lazily after preflight.
                func = node.func
                dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                    isinstance(func, ast.Attribute) and func.attr == "import_module"
                )
                if dynamic and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    dependencies.add(node.args[0].value.split(".")[0])
        for dependency in sorted(dependencies - bundled_modules):
            if (root / f"{dependency}.py").exists() or (root / dependency / "__init__.py").exists():
                raise BundleError(
                    f"Unlisted local runtime dependency: {filename} -> {dependency}; "
                    "review and update the allowlist before packaging."
                )


def _check_template_assets(payloads: dict[str, bytes]) -> None:
    class AssetReferences(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key not in {"src", "href"} or not value:
                    continue
                url = urlsplit(value)
                if url.scheme or url.netloc or not url.path.startswith("/static/"):
                    continue
                relative = unquote(url.path).lstrip("/")
                if relative not in payloads:
                    raise BundleError(f"Unlisted template asset: {relative}; review the asset allowlist.")

    parser = AssetReferences()
    for name, content in payloads.items():
        if name.startswith("templates/"):
            parser.feed(content.decode("utf-8-sig"))
    parser.close()


def collect_payloads(root: Path) -> dict[str, bytes]:
    """Read only named application files; do not traverse excluded directories."""
    payloads = {name: _safe_file(root, name).read_bytes() for name in REQUIRED_FILES}
    _check_imports(root, payloads)
    _check_template_assets(payloads)
    return payloads


def _output_path(root: Path, output: Path | None) -> Path:
    path = output if output is not None else root / "backtest" / DEFAULT_ARCHIVE
    if not path.is_absolute():
        path = root / path
    # abspath collapses '..' but does not conceal symlink/junction components.
    path = Path(os.path.abspath(path))
    boundary = root / "backtest"
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        raise BundleError("Output must be inside the project's ignored backtest directory.") from None
    if not relative.parts or path.suffix.lower() != ".zip":
        raise BundleError("Output must be a ZIP file inside backtest.")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            if _is_link(current):
                raise BundleError("Output path must not contain a symlink or junction.")
        except FileNotFoundError:
            continue
    return path


def build_bundle(root: Path, output: Path | None = None) -> dict:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise BundleError("Project root is not a directory.")
    archive = _output_path(root, output)
    # Validate/read the full runtime before creating or replacing any output.
    payloads = collect_payloads(root)
    files = [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(payloads.items())
    ]
    manifest = {"format": 1, "files": files}
    payloads[MANIFEST_NAME] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".server-bundle-", suffix=".tmp", dir=archive.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, content in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, content, compresslevel=9)
        os.replace(temporary, archive)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {
        "archive": str(archive),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "manifest": MANIFEST_NAME,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="ZIP destination inside the project's backtest directory")
    args = parser.parse_args(argv)
    try:
        result = build_bundle(args.root, args.output)
    except (BundleError, OSError) as error:
        print(f"Bundle failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
