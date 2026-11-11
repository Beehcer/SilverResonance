"""Launch a durable workspace; preview data never writes the host's trading ledger."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="多周期计划工作台")
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--mock", action="store_true", help="明确使用模拟行情，存入独立工作区文件")
    parser.add_argument("--share", action="store_true", help="允许同一网络的伙伴访问")
    parser.add_argument("--state", type=Path, help="指定独立的工作区存储文件")
    args = parser.parse_args()
    import app
    app.apply_user_settings()
    if args.mock:
        app.config.MOCK_MODE = True
        app._set_state(mock=True)
        app._dump_signals_state = lambda: None
        app.fundamentals.start = lambda: None
    if args.state or args.mock:
        path = args.state or Path(__file__).parent / "state" / "workspace-preview.json"
        # Keep the instance referenced by the already registered routes.
        app._workspace_store.__init__(path)
    app.app.config.update(TEMPLATES_AUTO_RELOAD=True)
    app.start_data_thread()
    host = "0.0.0.0" if args.share else "127.0.0.1"
    print(f"工作台 http://127.0.0.1:{args.port}/workspace · {'模拟' if app.config.MOCK_MODE else '配置行情'}", flush=True)
    app.app.run(host=host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
