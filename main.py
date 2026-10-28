#!/usr/bin/env python
"""vial-matrix-light —— Matrix / Vial 键盘灯光调整（本地 Web 界面）。

用法::

    python main.py                # 起本地服务并自动打开浏览器
    python main.py --port 9000    # 指定端口（默认从 8765 起找空闲端口）
    python main.py --no-browser   # 只起服务，不打开浏览器
    python main.py --selftest     # 只做自检（建应用 + 校验静态资源），立即退出

界面跑在浏览器里，但**完全本地**：服务只监听 127.0.0.1，不改动系统防火墙，
也不会把任何数据发到外面。键盘操作仍然由 :mod:`vial_light.device` 直连 HID 完成。

界面依赖的 Flask 已随程序放在 ``vendor/`` 目录里，**不需要 pip 安装**；
只要这台机器上有 Python 3.6+ 就能直接运行。
"""

import os
import socket
import sys
import traceback
import webbrowser

def _res_dir():
    """**资源**目录：源码运行时是项目根，打包成 exe 后是 PyInstaller 的解压目录。

    onefile 模式下这个目录是临时目录，**只读、退出即删** —— 不能往里写任何东西。
    ``vendor/`` 与 ``vial_light/webapp/static/`` 都在这里，PyInstaller 会原样带过去。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """**可写**目录：源码运行时是项目根，打包后是 exe 所在目录（日志要留得住）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


sys.path.insert(0, _res_dir())

#: 随程序打包的第三方依赖（Flask 及其纯 Python 依赖）。
#: 放在仓库根，且插到 ``sys.path`` 最前面 —— 这样即使系统里装过别的版本，
#: 也一定用这份自带的；机器上完全没装 Flask 也能直接跑。
_VENDOR_DIR = os.path.join(_res_dir(), "vendor")
if os.path.isdir(_VENDOR_DIR):
    sys.path.insert(0, _VENDOR_DIR)

DEFAULT_PORT = 8765
#: 端口被占用时往后顺延探测的个数
PORT_TRIES = 20

#: 自检要求必须存在的静态资源（少一个前端就是白屏）。
#: 前端是 Vite 构建产物，文件名带内容哈希，所以这里只固定 ``index.html``，
#: ``assets/`` 下的产物按实际存在情况枚举。
_REQUIRED_ASSETS = ["index.html"]


def _collect_assets(base):
    """返回 ``_REQUIRED_ASSETS`` + ``assets/`` 目录里的全部产物（相对路径）。"""
    out = list(_REQUIRED_ASSETS)
    assets = os.path.join(base, "assets")
    if os.path.isdir(assets):
        for name in sorted(os.listdir(assets)):
            path = os.path.join(assets, name)
            if os.path.isfile(path):
                out.append(os.path.join("assets", name))
    return out


def _force_utf8_output():
    """被重定向到文件/管道时，强制 UTF-8，避免中文日志乱码。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if not stream.isatty():
                stream.flush()
                buffer = getattr(stream, "buffer", None)
                if buffer is not None:
                    import io

                    setattr(sys, name, io.TextIOWrapper(buffer, encoding="utf-8"))
        except Exception:
            pass


def _report_fatal(exc):
    """把致命错误同时写到 stderr 和 run.log，保证双击场景也看得见。"""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        sys.stderr.write(text)
    except Exception:
        pass
    log_path = os.path.join(_data_dir(), "run.log")
    try:
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write("\n===== FATAL ERROR =====\n")
            fp.write(text)
    except Exception:
        pass
    return text


def _port_free(port):
    """精确判断 ``127.0.0.1:port`` 能不能独占绑定。

    这里**不能**设 ``SO_REUSEADDR``：Windows 对它的语义和 Linux 不同，
    它允许同一个端口被重复绑定（不检查冲突），设了之后 bind 永远成功，
    顺延探测就彻底失效了，服务会静默撞到已被占用的端口上 —— 表现就是
    「控制台说 8766，浏览器却一直转圈」。

    Windows 上还额外打开 ``SO_EXCLUSIVEADDRUSE``：它能连「别的进程绑在
    ``0.0.0.0``」这种隐性占用一起挡住，比裸 bind 更严格。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _bind_server(app, start=DEFAULT_PORT):
    """真正把服务绑到端口上，返回 ``(server, port)``；全被占则抛最后一个异常。

    与 ``app.run()`` 的区别：**绑定是显式、可失败的**。绑成功之后才去开浏览器，
    所以「浏览器打开的地址」一定有服务在听 —— 旧写法是 ``_free_port()`` 探完就
    close，再由 Flask 重新 bind，中间既有竞态又无法在失败时收回浏览器。
    """
    from werkzeug.serving import make_server

    last = None
    for port in range(start, start + PORT_TRIES):
        if not _port_free(port):
            last = OSError("端口 %d 已被占用" % port)
            continue
        try:
            return make_server("127.0.0.1", port, app, threaded=True), port
        except OSError as exc:                     # 探测和 bind 之间被抢走
            last = exc
            continue
    raise last or OSError("没有可用端口")


def _probe_instance(port, timeout=0.35):
    """判断 ``port`` 上是不是**已经在跑的本程序**（只认 ``/api/ping`` 自报的标识）。

    别的程序占了 8765 也不会被误认成自己 —— 它得同时是 200、是 JSON、
    且 ``data.app == "vial-matrix-light"``。
    """
    try:
        import json as _json
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port,
                                    timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    return bool(payload.get("ok") and isinstance(data, dict)
                and data.get("app") == "vial-matrix-light")


def _open_browser(url):
    """开浏览器（失败不影响服务本身）。"""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _log(message):
    """同时写到控制台和 exe 旁边的 run.log —— 双击场景只看得到其中一个。"""
    text = message if message.endswith("\n") else message + "\n"
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        path = os.path.join(_data_dir(), "run.log")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(text)
    except Exception:
        pass


def _reset_log():
    """每次启动重写 run.log，避免上一次的记录混进来误导排查。"""
    try:
        with open(os.path.join(_data_dir(), "run.log"), "w",
                  encoding="utf-8") as fp:
            fp.write("")
    except Exception:
        pass


def _hold_console():
    """启动失败时别让控制台窗口一闪而过（只在交互式终端里等回车）。"""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("\n按回车键退出…")
    except Exception:
        pass


def _static_dir():
    return os.path.join(_res_dir(), "vial_light", "webapp", "static")


def run_selftest():
    """建应用、查路由、校验静态资源、各接口返回合法 JSON，然后退出。"""
    report = []
    import flask
    from vial_light.webapp.server import create_app

    # 明确报出 Flask 是从哪儿加载的：vendor 命中说明「不依赖系统环境」成立
    loaded_from = getattr(flask, "__file__", "") or ""
    report.append("Flask %s <- %s"
                  % (flask.__version__,
                     "vendor/（自带）" if _VENDOR_DIR
                     and loaded_from.startswith(_VENDOR_DIR)
                     else loaded_from))

    app = create_app()
    session = app.config["vial_session"]
    report.append("路由数: %d" % len(list(app.url_map.iter_rules())))

    base = _static_dir()
    report.append("静态目录: %s" % base)
    missing = []
    for rel in _collect_assets(base):
        path = os.path.join(base, rel)
        size = os.path.getsize(path) if os.path.exists(path) else -1
        if size < 0:
            missing.append(rel)
        else:
            report.append("  %-26s %6d 字节" % (rel, size))

    client = app.test_client()
    # 首页返回的是 HTML（前端入口），其余接口都必须是约定的 JSON 信封
    home = client.get("/")
    home_ok = home.status_code == 200 and b"<div id=\"root\">" in home.data
    report.append("GET /%-15s -> %s%s"
                  % ("", home.status_code, "" if home_ok else "  (首页不是前端入口)"))
    if not home_ok:
        missing.append("/")

    for path in ("/api/state", "/api/devices", "/api/info", "/api/logs",
                 "/api/layout", "/api/leds", "/api/effects", "/api/strips",
                 "/api/presets", "/api/ping"):
        resp = client.get(path)
        payload = resp.get_json(silent=True)
        good = resp.status_code == 200 and isinstance(payload, dict) \
            and "ok" in payload
        report.append("GET %-16s -> %s%s" % (
            path, resp.status_code, "" if good else "  (响应不是约定的 JSON)"))
        if not good:
            missing.append(path)

    # SSE 至少得能被打开，并且立刻吐出第一帧
    sse = client.get("/api/events", buffered=False)
    report.append("GET /api/events   -> %s  %s"
                  % (sse.status_code, sse.headers.get("Content-Type")))
    if sse.status_code != 200:
        missing.append("/api/events")
    else:
        chunk = next(iter(sse.response), b"")
        sse.close()
        report.append("SSE 首帧: %s" % chunk[:60])

    report.append("方案库: %s（%d 条）"
                  % (session.store().path, len(session.store().presets)))
    session.close()

    print("SELFTEST OK" if not missing else "SELFTEST FAILED")
    for line in report:
        print("  " + line)
    if missing:
        print("  缺失/异常: %s" % ", ".join(missing))
        return 1
    return 0


def main():
    _force_utf8_output()
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return run_selftest()

    port = DEFAULT_PORT
    explicit_port = "--port" in argv
    if explicit_port:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            sys.stderr.write("--port 后面要跟一个端口号\n")
            return 2
    no_browser = "--no-browser" in argv

    # 已经有本程序在跑就**直接用它**，不再起第二个 —— 否则会出现
    # 「8765 一个、8766 一个」两个页面，用户不知道该看哪个。
    #
    # 这一步刻意放在写日志之前：命中时只往 run.log 追加一行，
    # 不能把**正在运行的那个实例**的日志清掉。
    if not explicit_port and _probe_instance(port):
        url = "http://127.0.0.1:%d/" % port
        _log("检测到已有实例在运行：%s" % url)
        _log("直接打开它，本次不再启动新服务。这里可以直接关掉这个窗口。")
        if not no_browser:
            _open_browser(url)
        return 0

    _reset_log()
    _log("vial-matrix-light 启动中…（Python %s）"
         % ".".join(str(n) for n in sys.version_info[:3]))
    _log("  程序位置: %s" % _data_dir())
    _log("  资源位置: %s" % _res_dir())

    try:
        from vial_light.webapp.server import create_app
    except ImportError as exc:
        _log(
            "无法加载 Web 界面依赖: %s\n"
            "界面需要的 Flask 本应在 vendor/ 目录随程序提供，这里没找到。\n"
            "请确认 vendor/ 目录完整（flask/ werkzeug/ jinja2/ click/ "
            "itsdangerous/ markupsafe/），\n"
            "或手动安装一份: pip install \"flask>=1.1,<2\"\n"
            "也可以用命令行模式: python mvl.py --help\n" % exc
        )
        _hold_console()
        return 2
    except Exception as exc:
        _report_fatal(exc)
        _hold_console()
        return 3

    try:
        app = create_app()
    except Exception as exc:
        _report_fatal(exc)
        _hold_console()
        return 4

    if _probe_instance(port):
        url = "http://127.0.0.1:%d/" % port
        _log("检测到已有实例在运行：%s" % url)
        if not no_browser:
            _open_browser(url)
        return 0

    # 先把端口握住，再开浏览器 —— 保证浏览器打开的地址一定有服务在听
    try:
        server, port = _bind_server(app, port)
    except OSError as exc:
        _log("启动失败：127.0.0.1:%d-%d 全被占用（%s）"
             % (port, port + PORT_TRIES - 1, exc))
        _log("关掉占用这些端口的程序后重试，或换一个端口："
             "vial-matrix-light.exe --port 9000")
        _hold_console()
        return 6

    url = "http://127.0.0.1:%d/" % port
    _log("vial-matrix-light 已启动：%s" % url)
    if port != DEFAULT_PORT and not explicit_port:
        _log("  （默认端口 %d 被别的程序占用，已顺延到 %d）"
             % (DEFAULT_PORT, port))
    _log("按 Ctrl+C 结束；关掉这个窗口服务就会停。")

    if not no_browser:
        _open_browser(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _report_fatal(exc)
        return 5
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        try:
            app.config["vial_session"].close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as _exc:            # 兜底：双击场景窗口别一闪而过
        _report_fatal(_exc)
        _hold_console()
        sys.exit(9)