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
import threading
import traceback
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: 随程序打包的第三方依赖（Flask 及其纯 Python 依赖）。
#: 放在仓库根，且插到 ``sys.path`` 最前面 —— 这样即使系统里装过别的版本，
#: 也一定用这份自带的；机器上完全没装 Flask 也能直接跑。
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
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
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.log")
    try:
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write("\n===== FATAL ERROR =====\n")
            fp.write(text)
    except Exception:
        pass
    return text


def _free_port(start=DEFAULT_PORT):
    """从 ``start`` 起找一个能绑上的端口（只探本机回环）。

    这里**不能**设 ``SO_REUSEADDR``：Windows 对它的语义和 Linux 不同，
    它允许同一个端口被重复绑定（不检查冲突），设了之后 bind 永远成功，
    顺延探测就彻底失效了，服务会静默撞到已被占用的端口上。
    """
    for port in range(start, start + PORT_TRIES):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    return start


def _static_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "vial_light", "webapp", "static")


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
                 "/api/presets"):
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
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            sys.stderr.write("--port 后面要跟一个端口号\n")
            return 2
    no_browser = "--no-browser" in argv

    try:
        from vial_light.webapp.server import create_app
    except ImportError as exc:
        sys.stderr.write(
            "无法加载 Web 界面依赖: %s\n"
            "界面需要的 Flask 本应在 vendor/ 目录随程序提供，这里没找到。\n"
            "请确认 vendor/ 目录完整（flask/ werkzeug/ jinja2/ click/ "
            "itsdangerous/ markupsafe/），\n"
            "或手动安装一份: pip install \"flask>=1.1,<2\"\n"
            "也可以用命令行模式: python mvl.py --help\n" % exc
        )
        return 2
    except Exception as exc:
        _report_fatal(exc)
        return 3

    try:
        app = create_app()
    except Exception as exc:
        _report_fatal(exc)
        return 4

    port = _free_port(port)
    url = "http://127.0.0.1:%d/" % port
    print("vial-matrix-light 已启动：%s" % url)
    print("按 Ctrl+C 结束。")
    sys.stdout.flush()

    if not no_browser:
        def _open():
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Timer(0.6, _open).start()

    try:
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False,
                debug=False)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _report_fatal(exc)
        return 5
    finally:
        try:
            app.config["vial_session"].close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())