"""
用户本地配置读写。
- 账号/密码/合约存到 exe（或脚本）同目录的 user_config.json，
  这样打包分发时不把账号打进 exe，每台电脑填自己的。
- 纯技术参数仍在 config.py。
"""

import os
import sys
import json

FIELDS = ("tq_user", "tq_password", "symbol", "mock_mode")


def app_dir() -> str:
    """
    返回可写的程序目录：
    - 设置 SILVER_DATA_DIR 时，配置与运行状态写到该外部目录；
    - PyInstaller 打包(onefile)时 sys.executable 是 exe 路径，取其目录；
    - 源码运行时取本文件所在目录。
    """
    external = os.environ.get("SILVER_DATA_DIR", "").strip()
    if external:
        return os.path.abspath(os.path.expanduser(external))
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    return os.path.join(app_dir(), "user_config.json")


def load_settings() -> dict:
    """读取用户配置；不存在或损坏则返回空字典。"""
    path = config_path()
    if not os.path.exists(path):
        return {}
    try:
        # utf-8-sig 容忍带 BOM 的文件（某些编辑器/PowerShell 会写 BOM）
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return {k: data.get(k) for k in FIELDS if k in data}
    except Exception:  # noqa: BLE001
        return {}


def save_settings(data: dict) -> None:
    """保存用户配置（仅保留已知字段）。"""
    clean = {k: data[k] for k in FIELDS if k in data}
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def is_configured() -> bool:
    """是否已完成首次配置（有账号 + 合约，或显式选了模拟模式）。"""
    s = load_settings()
    if s.get("mock_mode"):
        return True
    return bool(s.get("tq_user") and s.get("tq_password") and s.get("symbol"))
