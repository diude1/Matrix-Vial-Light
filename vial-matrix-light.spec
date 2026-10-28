# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本：把 vial-matrix-light 打成**单文件 exe**。

构建（项目根）：

    build_exe.bat                 :: 推荐，会自动挑 Python 3.6 并校验产物
    :: 或手动：
    set PYTHONPATH=.build\\py36
    python -m PyInstaller --noconfirm --clean vial-matrix-light.spec

产物：``dist/vial-matrix-light.exe`` —— 双击即用，目标机器**不需要装 Python**。

三个必须原样带过去的目录（少一个就白屏 / 起不来）：

* ``vendor/``                 Flask 及其纯 Python 依赖
* ``vial_light/webapp/static/`` 前端构建产物（index.html + assets/）

关于 ``vendor/`` —— 两条路**都走**，互为保险：

1. ``pathex`` 里把 ``vendor`` 排在项目根前面，PyInstaller 静态分析就会从
   **vendor 那份**源码把 flask / werkzeug / jinja2 编进 PYZ。否则它会去捡
   构建机上 ``site-packages`` 里的同名包，打出来的 exe 和源码运行用的就不是
   同一份代码了（哪怕版本号一样）。
2. ``datas`` 把 ``vendor/`` 原样复制进包内，于是 ``main.py`` 那句
   ``sys.path.insert(0, <解压目录>/vendor)`` 依然指向真实存在的目录，
   源码与打包两种形态的资源布局保持一致。

``static/`` 只走 ``datas``：它是浏览器取的文件，必须能被 Flask 的
``STATIC_DIR`` 找到，不能进 PYZ。
"""

import os

# SPECPATH 在不同 PyInstaller 版本里有的指 spec 所在目录、有的指其父目录，
# 直接以「能不能看到 main.py」为准，免得算错一层。
ROOT = os.path.abspath(SPECPATH)
if not os.path.isfile(os.path.join(ROOT, "main.py")):
    ROOT = os.path.dirname(ROOT)

#: 必须随程序提供的纯 Python 包（顺序无关）
VENDOR_PACKAGES = ("flask", "werkzeug", "jinja2", "click",
                   "itsdangerous", "markupsafe")


def _collect(src, dest):
    """递归收集 ``src`` 到包内 ``dest``，跳过 __pycache__ / .pyc。"""
    out = []
    if not os.path.isdir(src):
        return out
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(root, name)
            rel_dir = os.path.relpath(root, src)
            target = dest if rel_dir == "." else os.path.join(dest, rel_dir)
            out.append((full, target.replace("\\", "/")))
    return out


datas = []
datas += _collect(os.path.join(ROOT, "vial_light", "webapp", "static"),
                  "vial_light/webapp/static")
for pkg in VENDOR_PACKAGES:
    datas += _collect(os.path.join(ROOT, "vendor", pkg), "vendor/" + pkg)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    # vendor 排在前面：分析时优先命中自带的那份 Flask，而不是构建机的 site-packages
    pathex=[os.path.join(ROOT, "vendor"), ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # 这些库在开发机上装了（numpy / pandas / Pillow …），本程序一个都用不到，
    # 显式排除，免得 hook 顺手把它们拖进来把 exe 撑到几十 MB。
    excludes=["numpy", "pandas", "PIL", "cv2", "tkinter", "matplotlib",
              "scipy", "openpyxl", "pyexcel", "sqlalchemy", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="vial-matrix-light",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 保留控制台：窗口里要打印「服务已启动 + 地址」，且出错时能看到原因。
    # 关掉它就只能去 run.log 里翻。
    console=True,
)
