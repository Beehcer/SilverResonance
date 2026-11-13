"""Production workspace pages; both panes use the existing live API and session."""

from pathlib import Path

from flask import Response, abort, render_template, request

from workspace_api import MOBILE_USER_AGENT
from workspace_runtime import adapt_workspace_chart_js, adapt_workspace_js


def render_workspace_page():
    pane = request.args.get("pane")
    if pane is not None:
        if pane not in ("pane-a", "pane-b"):
            abort(400)
        return render_template("workspace.html", dual_pane=True)
    if request.args.get("view") == "single" or MOBILE_USER_AGENT.search(request.headers.get("User-Agent", "")):
        return render_template("workspace.html", dual_pane=False)
    return render_template("workspace-dual.html")


def register_workspace_assets(app):
    def runtime_response(filename, adapter):
        try:
            source = (Path(app.static_folder) / filename).read_text(encoding="utf-8")
            result = adapter(source)
        except (OSError, ValueError):
            app.logger.exception("Workspace integration is incompatible with this release")
            abort(503, "工作台版本尚未就绪，请稍后刷新。")
        response = Response(result, mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/static/workspace-dual-runtime.js")
    def workspace_dual_runtime():
        return runtime_response("workspace.js", adapt_workspace_js)

    @app.get("/static/workspace-dual-chart.js")
    def workspace_dual_chart():
        return runtime_response("workspace-chart.js", adapt_workspace_chart_js)
