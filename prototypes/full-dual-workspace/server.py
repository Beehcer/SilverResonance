"""Complete workspace APIs with an isolated local store and simulated market.

Does not import app.py, load account settings, connect a feed or proxy production.
Only the new prototype data directory is writable through this service.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


DIRECTORY = Path(__file__).resolve().parent
PROJECT = DIRECTORY.parents[1]
PREVIOUS = DIRECTORY.parent / "dual-symbol-workspace"
DEFAULT_PORT = 8865
COOKIE_NAME = "workspace_full_preview_session"
sys.dont_write_bytecode = True
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from flask import Flask, Response, abort, redirect, render_template, send_file  # noqa: E402
import workspace_rules  # noqa: E402
from workspace_store import WorkspaceStore  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A private module copy keeps the original workspace cookie constant untouched
# even when another workspace application's tests run in this Python process.
workspace_api = load_module("full_preview_workspace_api", PROJECT / "workspace_api.py")
workspace_api.COOKIE_NAME = COOKIE_NAME
simulation = load_module("full_preview_simulation", PREVIOUS / "server.py")
PUBLIC_FILES = {
    name: DIRECTORY / name for name in ("embed.css", "embed.js", "shell.css", "shell.js")
}
PUBLIC_FILES["model.js"] = PREVIOUS / "model.js"
RUNTIME_SCRIPT = re.compile(r'<script\s+src="/static/workspace\.js(?:\?[^\"]*)?"\s*></script>')
CHART_SCRIPT = re.compile(r'<script\s+src="/static/workspace-chart\.js(?:\?[^\"]*)?"\s*></script>')
NOTICE = "完整功能本地样板 · 模拟行情 · 计划仅写入样板独立目录，不连接正式工作区。"
MARKET_NOTICE = "简化日盘模拟 · 每5秒检查，3分钟K线推进；收盘后保留最后价。"


def catalogue():
    return simulation.catalogue()


def market_provider(symbol, history_token=None, preview=False, now=None):
    item = next((item for item in catalogue() if item["symbol"] == symbol), None)
    if item is None:
        raise ValueError("未知样板品种")
    now = (now or datetime.now(simulation.BEIJING)).astimezone(simulation.BEIJING)
    market = copy.deepcopy(simulation.mock_market(item, now))
    for period, bars in market["periods"].items():
        for bar in bars:
            start = datetime.fromisoformat(bar["dt"])
            end = (start.replace(hour=15) if period == "day"
                   else start + timedelta(seconds=simulation.PERIODS[period]))
            bar.update(closed=end <= now, end_time=end.isoformat(), closed_at=end.isoformat())
    # Full snapshots are intentional: existing frontend caching already accepts
    # non-incremental replies. Even full responses require a nonempty history
    # identity; generate it from the bars rather than the polling timestamp.
    market.update(as_of=now.isoformat(), quote_at=market["periods"]["m3"][-1]["dt"],
                  connected=True, incremental=False, message=MARKET_NOTICE, setup_required=False)
    for period, details in market["freshness"].items():
        details.update(available=True, checked_recent=True, changed_recent=False,
                       quote_at=market["periods"][period][-1]["dt"])
    if preview:
        market["periods"] = {period: bars[-240:] for period, bars in market["periods"].items()}
        market.update(partial=True, feed_status="preview")
    market["period_counts"] = {period: len(bars) for period, bars in market["periods"].items()}
    fingerprint = json.dumps([symbol, market["periods"]], sort_keys=True, separators=(",", ":"))
    market["history_token"] = "full-preview-v2:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return market


def quotes_provider():
    now = datetime.now(simulation.BEIJING)
    values = []
    for item in catalogue():
        market = simulation.mock_market(item, now)
        values.append({"symbol": item["symbol"], "tick": item["tick"], "price": market["price"],
                       "source": "mock", "connected": True, "at": now.timestamp() * 1000,
                       "bar_time": market["periods"]["m3"][-1]["dt"]})
    return values


def create_application(data_directory=None):
    """Tests may supply their own new directory; the launcher exposes no override."""
    data_directory = (Path(data_directory) if data_directory is not None else DIRECTORY / "data").resolve()
    store_path = (data_directory / "workspace.json").resolve()
    if not data_directory.is_relative_to(DIRECTORY) or not store_path.is_relative_to(DIRECTORY):
        raise ValueError("样板存储必须位于自己的独立目录内")
    store = WorkspaceStore(store_path)
    app = Flask("full_dual_preview", template_folder=str(PROJECT / "templates"),
                static_folder=str(PROJECT / "static"), static_url_path="/static")
    app.config.update(DEBUG=False, TESTING=False, TEMPLATES_AUTO_RELOAD=True,
                      WORKSPACE_PRODUCTION=True, MAX_CONTENT_LENGTH=128 * 1024)
    app.extensions["preview_store"] = store
    workspace_api.register_workspace(
        app, store, market_provider, catalogue, workspace_rules.rule_manifest,
        workspace_rules.evaluate_plan, quotes_provider,
        market_incremental_provider=market_provider,
        market_preview_provider=lambda symbol: market_provider(symbol, preview=True),
    )

    @app.after_request
    def local_preview_headers(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Preview-Mode"] = "isolated-mock"
        return response

    @app.get("/")
    def shell():
        file = DIRECTORY / "shell.html"
        if not file.is_file():
            abort(503, "样板页面尚在准备，请稍后刷新。")
        return send_file(file, mimetype="text/html")

    @app.get("/workspace")
    def workspace_entry():
        return redirect("/")

    @app.get("/pane")
    def pane():
        html = render_template("workspace.html")
        html, chart_count = CHART_SCRIPT.subn('<script src="/preview/workspace-chart.js"></script>', html)
        html, count = RUNTIME_SCRIPT.subn(
            '<script src="/preview/embed.js"></script>'
            '<script src="/preview/workspace-runtime.js"></script>', html)
        if count != 1 or chart_count != 1:
            abort(503, "工作台入口已变化，样板适配需要重新核对。")
        html = html.replace("</head>", '<link rel="stylesheet" href="/preview/embed.css"></head>', 1)
        return Response(html, mimetype="text/html")

    @app.get("/preview/workspace-runtime.js")
    def runtime():
        try:
            adapter = load_module("full_preview_runtime_adapter", DIRECTORY / "runtime_adapter.py")
            source = (PROJECT / "static" / "workspace.js").read_text(encoding="utf-8")
            result = adapter.adapt_workspace_js(source)
        except (OSError, ValueError):
            abort(503, "完整工作台适配尚未就绪，请稍后刷新。")
        return Response(result, mimetype="application/javascript")

    @app.get("/preview/workspace-chart.js")
    def chart_runtime():
        try:
            adapter = load_module("full_preview_runtime_adapter", DIRECTORY / "runtime_adapter.py")
            source = (PROJECT / "static" / "workspace-chart.js").read_text(encoding="utf-8")
            result = adapter.adapt_workspace_chart_js(source)
        except (OSError, ValueError):
            abort(503, "图表适配尚未就绪，请稍后刷新。")
        return Response(result, mimetype="application/javascript")

    @app.get("/preview/<path:name>")
    def preview_asset(name):
        file = PUBLIC_FILES.get(name)
        if file is None or not file.is_file():
            abort(404)
        return send_file(file, mimetype="text/css" if name.endswith(".css") else "application/javascript")

    @app.get("/healthz")
    def health():
        return {"status": "ok", "source": "mock", "isolated": True}

    return app


def main():
    parser = argparse.ArgumentParser(description="完整工作台双品种本地样板（独立数据、模拟行情）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口须在 1–65535 之间")
    app = create_application()
    print(f"完整本地样板 http://127.0.0.1:{args.port}/", flush=True)
    print(NOTICE, flush=True)
    print("Ctrl+C 停止；正式工作台保持原样。", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
