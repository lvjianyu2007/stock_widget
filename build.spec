# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：PySide6 + akshare + pytdx。

用法（在项目目录下，用项目自带的虚拟环境）：
    # 绿色版目录（推荐：启动快，config.json 就在 exe 旁边）
    .\\.venv\\Scripts\\pyinstaller.exe build.spec --noconfirm

    # 单文件 exe（便于拷贝给别人，代价是每次启动要多等几秒解包）
    $env:STOCK_WIDGET_ONEFILE=1; .\\.venv\\Scripts\\pyinstaller.exe build.spec --noconfirm

也可以直接双击 build.bat。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ONEFILE = os.environ.get("STOCK_WIDGET_ONEFILE", "") not in ("", "0")
APP_NAME = "StockWidget"

datas, binaries, hiddenimports = [], [], []

# akshare / pytdx / curl_cffi 都有"运行时才 import 的子模块 + 自带数据文件"，
# 必须整体收集，否则打出来的 exe 会在取数时报 ModuleNotFoundError / FileNotFoundError。
# py_mini_racer 尤其关键：akshare 的新浪股票接口（stock_zh_a_sina）顶部就 import 它，
# 缺了它新浪整条线在 exe 里全废（而且只在取数时才报错，很难发现）。
# 注意：它里面是 V8 引擎（mini_racer.dll），个别开了"应用程序控制/Smart App Control"
# 的机器可能拦这个未签名的 exe。真被拦且不想加信任的话，把这行的 py_mini_racer
# 去掉即可，代价是 exe 里只能用东财通道取 A 股行情（新浪那几家会不可用）。
for package in ("akshare", "pytdx", "curl_cffi", "py_mini_racer"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# akshare 部分接口用到的解析库，按需 import，显式带上更保险
hiddenimports += [
    "lxml.etree",
    "bs4",
    "html5lib",
    "jsonpath",
    "openpyxl",
    "xlrd",
    "py_mini_racer",
    "akshare.stock.stock_zh_a_sina",
    "akshare.fund.fund_etf_sina",
]

# curl_cffi 的原生 libcurl 不在包目录里，而是放在 site-packages/curl_cffi.libs，
# 它靠 os.add_dll_directory(包目录/../curl_cffi.libs) 找；打包后必须保持同样的相对位置。
try:
    import curl_cffi

    site_packages = Path(curl_cffi.__file__).resolve().parent.parent
    for dll in (site_packages / "curl_cffi.libs").glob("*.dll"):
        datas.append((str(dll), "curl_cffi.libs"))
except Exception:
    pass

# 用不到的重家伙直接排除，能省下几百 MB
excludes = [
    "tkinter",
    "matplotlib",
    "scipy",
    "IPython",
    "notebook",
    "pytest",
    "PyQt5",
    "PyQt6",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtDesigner",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtNetworkAuth",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtSpatialAudio",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[str(Path(SPECPATH))],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,          # 不要黑窗口
        disable_windowed_traceback=False,   # 真出错时弹窗给出堆栈，方便排查
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # icon="app.ico",       # 有图标文件的话打开这一行
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        # icon="app.ico",
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
