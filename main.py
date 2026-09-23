#!/usr/bin/env python
"""vial-matrix-light —— Matrix / Vial 键盘灯光调整（图形界面）。

用法::

    python main.py              # 打开界面
    python main.py --selftest   # 只构建界面并立即退出（自检）
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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


def main():
    _force_utf8_output()
    argv = sys.argv[1:]
    selftest = "--selftest" in argv
    try:
        from vial_light import gui
    except ImportError as exc:
        sys.stderr.write(
            "无法加载图形界面: %s\n"
            "本程序需要带 tkinter 的 Python。\n"
            "如果用的是 Microsoft Store 版 Python，请改装 python.org 版；\n"
            "也可以用命令行模式: python mvl.py --help\n" % exc
        )
        return 2
    except Exception as exc:
        _report_fatal(exc)
        return 3
    try:
        return gui.main(selftest=selftest) or 0
    except Exception as exc:
        _report_fatal(exc)
        if not selftest:
            try:
                from tkinter import messagebox

                messagebox.showerror("vial-matrix-light 启动失败", str(exc))
            except Exception:
                pass
        return 4


if __name__ == "__main__":
    sys.exit(main())
