"""Single-process production workspace. Does not change the desktop launcher.

Prepare SILVER_DATA_DIR/user_config.json and SILVER_RULES_PATH first. --check
validates local prerequisites without importing app or connecting market data.
The only listener is 127.0.0.1; an HTTPS reverse proxy is required
for shared access. Never run this alongside another live feed for the account.
"""

import argparse
import importlib
import importlib.util
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading


LOGGER = logging.getLogger("silver.runtime")
LOOPBACK = {"127.0.0.1", "::1"}


class RuntimeConfigError(Exception):
    """Only fixed, non-secret diagnostics may be exposed by this exception."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="生产工作台：单进程、仅本机监听、真实行情")
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--threads", type=int, default=24, help="HTTP 工作线程，至少 24 个以容纳协作长连接")
    parser.add_argument("--data-dir", help="外部配置及状态目录，也可设置 SILVER_DATA_DIR")
    parser.add_argument("--behind-proxy", action="store_true", help="信任本机反向代理覆盖的 HTTPS 协议头")
    parser.add_argument("--check", action="store_true", help="仅检查文件、规则和依赖，不连接行情")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口须在 1–65535 之间")
    if not 24 <= args.threads <= 128:
        parser.error("线程数须在 24–128 之间")
    return args


def check_runtime(data_dir=None):
    """Validate before importing app, which constructs all persistent stores."""
    if data_dir:
        os.environ["SILVER_DATA_DIR"] = str(Path(data_dir).expanduser().resolve())
    import user_settings
    import workspace_rules

    directory = Path(user_settings.app_dir())
    if not directory.is_dir():
        raise RuntimeConfigError("数据目录不存在，请先创建并授予服务用户读写权限。")
    settings = user_settings.load_settings()
    if not all(isinstance(settings.get(key), str) and settings[key].strip()
               for key in ("tq_user", "tq_password", "symbol")):
        raise RuntimeConfigError("真实行情配置不完整：需在外部 user_config.json 填写行情账号、密码和合约。")
    manifest = workspace_rules.rule_manifest()
    if not manifest.get("available"):
        raise RuntimeConfigError("交易规则文件不可读取，请检查 SILVER_RULES_PATH。")
    if not manifest.get("compatible"):
        raise RuntimeConfigError("交易规则版本尚未与当前程序核对，暂不启动生产服务。")
    try:
        state_dir = directory / "state"
        state_dir.mkdir(exist_ok=True)
        for target in (directory, state_dir):
            with tempfile.TemporaryFile(dir=target) as probe:
                probe.write(b"runtime-check")
                probe.flush()
    except OSError:
        raise RuntimeConfigError("数据目录或 state 子目录不可写，请检查服务用户权限。") from None
    missing = [module for module in ("waitress", "flask", "tqsdk", "pandas", "akshare")
               if importlib.util.find_spec(module) is None]
    if missing:
        raise RuntimeConfigError("服务器运行依赖不完整，请安装 requirements-server.txt。")
    return directory


class RuntimeLock:
    """One live producer per data directory; OS releases the lock on crashes."""

    def __init__(self, directory):
        self.path = Path(directory) / ".runtime.lock"
        self.file = None

    def __enter__(self):
        try:
            self.file = self.path.open("a+b")
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.file.write(b"0")
                    self.file.flush()
                    self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self.file:
                self.file.close()
                self.file = None
            raise RuntimeConfigError("数据目录已被另一服务占用，或运行锁不可写。请勿重复启动行情进程。") from None
        return self

    def __exit__(self, *_):
        if self.file:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class LocalProxyScheme:
    """Only HTTPS scheme from our loopback proxy; never proxy host or identity."""

    def __init__(self, app):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.app = app
        self.proxied = ProxyFix(app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") in LOOPBACK:
            return self.proxied(environ, start_response)
        return self.app(environ, start_response)


class WorkspaceOnly:
    """Production never exposes desktop setup, account settings or legacy APIs."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        allowed = path in ("/", "/workspace", "/healthz") or path.startswith(("/static/", "/api/workspace/"))
        if not allowed:
            start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", "9")])
            return [b"Not Found"]
        return self.app(environ, start_response)


def feed_alive(runtime):
    thread = getattr(runtime, "_data_thread", None)
    return bool(thread and thread.is_alive())


def register_health(runtime):
    from flask import jsonify, request

    @runtime.app.get("/healthz")
    def production_health():
        if (request.remote_addr not in LOOPBACK
                or request.headers.get("Forwarded") or request.headers.get("X-Forwarded-For")):
            return "Not Found", 404
        alive = feed_alive(runtime)
        response = jsonify(status="ok" if alive else "failed", feed_thread="running" if alive else "stopped")
        response.headers["Cache-Control"] = "no-store"
        return response, 200 if alive else 503


def prepare_application(behind_proxy=False):
    """Call only after check_runtime; importing app creates state-file readers."""
    runtime = importlib.import_module("app")
    runtime.apply_user_settings()
    # Saved desktop preview preferences must never downgrade a production feed.
    runtime.config.MOCK_MODE = False
    runtime._set_state(mock=False)
    runtime.app.config.update(DEBUG=False, TESTING=False, TEMPLATES_AUTO_RELOAD=False, WORKSPACE_PRODUCTION=True)
    register_health(runtime)
    wsgi = runtime.app.wsgi_app
    if behind_proxy:
        wsgi = LocalProxyScheme(wsgi)
    runtime.app.wsgi_app = WorkspaceOnly(wsgi)
    return runtime


def watch_feed(runtime, stop_event, *, interval=5, exit_process=os._exit):
    """Liveness only: closed markets and SDK reconnection do not imply failure."""
    while not stop_event.wait(interval):
        if not feed_alive(runtime):
            LOGGER.error("行情线程已停止，退出服务并交由进程管理器重启。")
            exit_process(1)
            return


def run_server(args):
    directory = check_runtime(args.data_dir)
    if args.check:
        print("检查通过：真实行情配置、交易规则、外部数据目录与服务器依赖可用；未连接行情。")
        return 0
    # Import and bind the HTTP listener before creating a market-data session.
    from waitress import create_server

    with RuntimeLock(directory):
        runtime = prepare_application(args.behind_proxy)
        server = create_server(runtime.app, host="127.0.0.1", port=args.port,
                               threads=args.threads, channel_timeout=120,
                               max_request_body_size=8 * 1024 * 1024,
                               expose_tracebacks=False,
                               clear_untrusted_proxy_headers=not args.behind_proxy)
        stop = threading.Event()
        backups = None
        try:
            from workspace_backup import start_workspace_backup
            backups = start_workspace_backup(runtime._workspace_store.path)
            runtime.start_data_thread()
            watchdog = threading.Thread(target=watch_feed, args=(runtime, stop),
                                        name="market-liveness", daemon=True)
            watchdog.start()
            LOGGER.info("生产工作台已启动：仅本机监听，单进程真实行情，协作长连接已启用。")
            server.run()
        finally:
            stop.set()
            if backups:
                backups.stop()
            server.task_dispatcher.shutdown(timeout=5)
            server.close()
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run_server(parse_args(argv))
    except RuntimeConfigError as error:
        LOGGER.error("%s", error)
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        # A provider exception can contain credentials. Keep startup logs generic.
        LOGGER.error("生产工作台启动失败（%s），请核对配置、依赖、目录权限和端口占用。", type(error).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
