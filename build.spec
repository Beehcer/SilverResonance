# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：白银三频共振面板 → 单个 exe
打包命令： pyinstaller build.spec  (或用 build.bat 一键)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("templates", "templates"), ("static", "static")]
binaries = []
hiddenimports = []

# tqsdk 依赖大量数据文件与动态导入，用 collect_all 一网打尽
for pkg in ("tqsdk", "pandas", "numpy", "shinny_structlog", "simplejson",
            "sgqlc", "graphql_core", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# akshare（基本面模块）及其动态依赖：缺了会让 fundamentals 线程在 exe 里静默崩溃
# （实测根因 2026-07：exe 内 import akshare 失败 → 三卡全"暂无数据"）
for pkg in ("akshare", "bs4", "soupsieve", "lxml", "html5lib", "openpyxl", "xlrd",
            "jsonpath", "py_mini_racer", "curl_cffi", "requests", "urllib3",
            "charset_normalizer", "idna", "decorator", "tabulate", "tqdm",
            "et_xmlfile", "websocket"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("tqsdk")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide2", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SilverResonance",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # 保留控制台，方便看日志/报错；不想要黑框可改 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
