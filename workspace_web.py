"""Production workspace pages; both panes use the existing live API and session."""

from pathlib import Path
import json
import re
from urllib.parse import urlsplit

from flask import Response, abort, current_app, render_template, request

from workspace_api import MOBILE_USER_AGENT
from workspace_runtime import adapt_workspace_chart_js, adapt_workspace_js


CORE_SCRIPTS = (
    'plan-board-model.js', 'workspace-editing.js', 'workspace-ticket.js',
    'workspace-presence.js', 'workspace-layouts.js', 'workspace-properties.js',
    'workspace-merge.js', 'workspace-layers.js', 'workspace-cases-loader.js',
    'workspace-market-cache.js', 'workspace-market-preview.js',
    'workspace-history-cache.js', 'workspace-symbols.js', 'workspace-shortcuts.js',
)
SHELL_SCRIPTS = ('workspace-dual-model.js', 'workspace-market-cache.js',
                 'workspace-history-cache.js', 'workspace-dual-shell.js')


def compiled_workspace_assets(directory):
    directory = Path(directory)
    def source(name):
        return (directory / name).read_text(encoding='utf-8')
    def combine(names):
        return '\n;\n'.join(source(name) for name in names)
    return {
        'workspace-core.js': combine(CORE_SCRIPTS).encode(),
        'workspace-shell-runtime.js': combine(SHELL_SCRIPTS).encode(),
        'workspace-dual-runtime.js': (source('workspace-dual-embed.js') + '\n;\n' + adapt_workspace_js(source('workspace.js'))).encode(),
        'workspace-dual-chart.js': adapt_workspace_chart_js(source('workspace-chart.js')).encode(),
    }


def render_asset_page(template, **context):
    html = render_template(template, **context)
    manifest = Path(current_app.static_folder) / 'workspace-assets.json'
    if manifest.is_file() and not current_app.config.get('WORKSPACE_LOCAL_ASSETS', False):
        mapping = json.loads(manifest.read_text(encoding='utf-8'))['assets']
        def replace(match):
            original = match.group(2)
            target = mapping.get(urlsplit(original).path)
            if not target:
                return match.group(0)
            parsed = urlsplit(target)
            if parsed.scheme != 'https' or parsed.netloc != 'silver-resonance.oss-cn-hangzhou.aliyuncs.com' or not re.fullmatch(r'/workspace/[a-f0-9]{24}/[a-z0-9-]+\.(js|css)', parsed.path) or parsed.query or parsed.fragment:
                raise ValueError('Invalid workspace asset URL')
            return match.group(1) + target + match.group(3)
        html = re.sub(r'((?:src|href)=")(/static/[^"<>]+)(")', replace, html)
    return html


def render_workspace_page():
    pane = request.args.get("pane")
    if pane is not None:
        if pane not in ("pane-a", "pane-b"):
            abort(400)
        return render_asset_page("workspace.html", dual_pane=True)
    if request.args.get("view") == "single" or MOBILE_USER_AGENT.search(request.headers.get("User-Agent", "")):
        return render_asset_page("workspace.html", dual_pane=False)
    return render_asset_page("workspace-dual.html")


def register_workspace_assets(app):
    def runtime_response(filename):
        try:
            result = compiled_workspace_assets(app.static_folder)[filename]
        except (OSError, ValueError):
            app.logger.exception("Workspace integration is incompatible with this release")
            abort(503, "工作台版本尚未就绪，请稍后刷新。")
        response = Response(result, mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/static/workspace-dual-runtime.js")
    def workspace_dual_runtime():
        return runtime_response("workspace-dual-runtime.js")

    @app.get("/static/workspace-dual-chart.js")
    def workspace_dual_chart():
        return runtime_response("workspace-dual-chart.js")

    @app.get("/static/workspace-core.js")
    def workspace_core():
        return runtime_response("workspace-core.js")

    @app.get("/static/workspace-shell-runtime.js")
    def workspace_shell_runtime():
        return runtime_response("workspace-shell-runtime.js")
