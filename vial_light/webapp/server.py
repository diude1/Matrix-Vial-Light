"""Flask 应用：REST 命令 + SSE 事件流。

为什么是 Flask + SSE 而不是别的：

* 本机 Python 是 3.6，FastAPI / uvicorn 需要 3.7+，用不了；
* ``flask_socketio`` / ``gevent`` / ``eventlet`` / ``websockets`` 一个都没装，
  而 Flask 已经装好了 —— 所以走 ``text/event-stream``，**零新增依赖**。

命令走普通 REST（一次请求一个动作，结果立刻返回），只有「日志 / 状态 / 忙闲」
三种持续性的东西走 SSE。这样既不用 WebSocket 库，也不用前端做打包。

约定：所有接口统一返回 ``{"ok": true, "data": ...}`` 或
``{"ok": false, "error": "..."}``，前端只需要看 ``ok``。
"""

import functools
import json
import os
import queue
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from .session import DeviceSession, SessionClosed

#: 静态资源目录（前端就放在这里，免构建）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

#: SSE 心跳间隔（秒）—— 太长会被浏览器/代理当死连接掐掉
SSE_KEEPALIVE = 15.0


# ---------------------------------------------------------------------------
# 统一响应
# ---------------------------------------------------------------------------
def ok(data=None):
    return jsonify({"ok": True, "data": data})


def fail(message, status=400):
    return jsonify({"ok": False, "error": str(message)}), status


def api(fn):
    """把异常收敛成 ``{"ok": false, "error": ...}``，不让页面拿到 500 网页。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return ok(fn(*args, **kwargs))
        except SessionClosed as exc:
            return fail(exc, 503)
        except ValueError as exc:
            return fail(exc, 400)
        except RuntimeError as exc:
            return fail(exc, 409)
        except Exception as exc:                  # 设备层什么都可能抛
            return fail(exc, 500)
    return wrapper


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _pick(data, *names, **kw):
    """从请求体里取第一个存在的键（支持别名）。"""
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return kw.get("default")


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(session=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    session = session or DeviceSession()
    app.config["vial_session"] = session

    def S():
        return app.config["vial_session"]

    # ---------------------------------------------------------------- 页面
    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    # ------------------------------------------------------------ 设备管理
    @app.route("/api/devices")
    @api
    def devices():
        all_raw = request.args.get("all") in ("1", "true", "yes")
        return S().list_devices(all_raw)

    @app.route("/api/connect", methods=["POST"])
    @api
    def connect():
        data = _body()
        index = int(_pick(data, "index", default=0) or 0)
        all_raw = bool(_pick(data, "all", "all_raw", default=False))
        return S().connect(index=index, all_raw=all_raw)

    @app.route("/api/disconnect", methods=["POST"])
    @api
    def disconnect():
        S().disconnect()
        return True

    # ---------------------------------------------------------------- 状态
    @app.route("/api/state")
    @api
    def state():
        return S().state_dict()

    @app.route("/api/logs")
    @api
    def logs():
        return S().logs()

    @app.route("/api/events")
    def events():
        """SSE：``log`` / ``state`` / ``busy`` 三类事件。"""
        session_ = S()
        chan = session_.subscribe()

        def stream():
            try:
                # 一上来先补一份快照，页面刷新后不用再单独拉一次
                yield _sse({"kind": "state", "ts": time.time(),
                            "data": session_.state_dict()})
                while True:
                    try:
                        event = chan.get(timeout=SSE_KEEPALIVE)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(event)
            except GeneratorExit:
                raise
            except Exception:
                pass
            finally:
                session_.unsubscribe(chan)

        return Response(stream(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })

    # ---------------------------------------------------------------- 灯光
    @app.route("/api/light", methods=["POST"])
    @api
    def light():
        data = _body()
        return S().set_light(
            effect=_pick(data, "effect"),
            speed=_pick(data, "speed"),
            hue=_pick(data, "hue"),
            sat=_pick(data, "sat"),
            val=_pick(data, "val"),
            apply_now=bool(_pick(data, "apply", default=False)),
        )

    @app.route("/api/light/read", methods=["POST"])
    @api
    def light_read():
        return S().read_light()

    @app.route("/api/light/apply", methods=["POST"])
    @api
    def light_apply():
        return S().apply_light()

    @app.route("/api/light/save", methods=["POST"])
    @api
    def light_save():
        return S().save_light()

    @app.route("/api/live", methods=["POST"])
    @api
    def live():
        data = _body()
        return S().set_live(bool(_pick(data, "live", default=True)))

    @app.route("/api/effects")
    @api
    def effects_list():
        return S().effects_list()

    # ---------------------------------------------------------------- 分区
    @app.route("/api/zone/color", methods=["POST"])
    @api
    def zone_color():
        data = _body()
        return S().set_zone_color(
            _pick(data, "zone", default="key"),
            hue=_pick(data, "hue"),
            sat=_pick(data, "sat"),
            val=_pick(data, "val"),
            apply_now=bool(_pick(data, "apply", default=True)),
        )

    @app.route("/api/zone/push", methods=["POST"])
    @api
    def zone_push():
        zones = _pick(_body(), "zones", default=["key"])
        if isinstance(zones, str):
            zones = [zones]
        return S().zone_push(zones)

    @app.route("/api/zone/sync", methods=["POST"])
    @api
    def zone_sync():
        return S().zone_sync()

    # ------------------------------------------------------------ 配件灯条
    @app.route("/api/strips")
    @api
    def strips():
        return S().strips_refresh()

    @app.route("/api/strip/mode", methods=["POST"])
    @api
    def strip_mode():
        data = _body()
        return S().strip_set_mode(_pick(data, "index", default=0),
                                  int(_pick(data, "mode", default=0)))

    @app.route("/api/strip/sync", methods=["POST"])
    @api
    def strip_sync():
        return S().strip_sync_all(_pick(_body(), "index", default=0))

    @app.route("/api/strip/led", methods=["POST"])
    @api
    def strip_led():
        data = _body()
        return S().strip_set_led(
            _pick(data, "index", default=0),
            _pick(data, "offset", default=0),
            _pick(data, "hue", default=0),
            _pick(data, "sat", default=255),
            _pick(data, "val", default=255),
        )

    @app.route("/api/strip/color", methods=["POST"])
    @api
    def strip_color():
        data = _body()
        return S().strip_set_color(
            _pick(data, "index", default=0),
            _pick(data, "hue"), _pick(data, "sat"),
            val=_pick(data, "val"), on=_pick(data, "on"))

    @app.route("/api/strip/bright", methods=["POST"])
    @api
    def strip_bright():
        data = _body()
        return S().strip_set_bright(_pick(data, "index", default=0),
                                    _pick(data, "val", default=255))

    @app.route("/api/strip/speed", methods=["POST"])
    @api
    def strip_speed():
        data = _body()
        return S().strip_set_speed(_pick(data, "index", default=0),
                                   _pick(data, "speed", default=0))

    # ------------------------------------------------------------ 配列 / 逐键
    @app.route("/api/layout")
    @api
    def layout():
        return S().get_layout()

    @app.route("/api/leds")
    @api
    def leds():
        return S().get_leds()

    @app.route("/api/leds/refresh", methods=["POST"])
    @api
    def leds_refresh():
        return S().leds_refresh()

    @app.route("/api/perkey/colors", methods=["POST"])
    @api
    def perkey_colors():
        return S().perkey_set_colors(_pick(_body(), "colors", default=[]))

    @app.route("/api/perkey/push", methods=["POST"])
    @api
    def perkey_push():
        return S().perkey_push(_pick(_body(), "changed"))

    @app.route("/api/perkey/fill", methods=["POST"])
    @api
    def perkey_fill():
        data = _body()
        return S().perkey_fill(_pick(data, "hue", default=0),
                               _pick(data, "sat", default=255),
                               _pick(data, "val", default=255))

    @app.route("/api/perkey/read", methods=["POST"])
    @api
    def perkey_read():
        return S().perkey_read()

    @app.route("/api/perkey/custom", methods=["POST"])
    @api
    def perkey_custom():
        return S().perkey_enter_custom()

    # ---------------------------------------------------------------- 其它
    @app.route("/api/raw", methods=["POST"])
    @api
    def raw():
        return S().raw_request(_pick(_body(), "hex", default=""))

    @app.route("/api/info")
    @api
    def info():
        return S().info

    @app.route("/api/info/refresh", methods=["POST"])
    @api
    def info_refresh():
        return S().refresh_info()

    @app.route("/api/snapshot")
    @api
    def snapshot():
        return S().snapshot_lighting()

    # ------------------------------------------------------------ 方案库
    @app.route("/api/presets")
    @api
    def presets_list():
        snap = S().state_dict()
        return snap["presets"]

    @app.route("/api/presets/save", methods=["POST"])
    @api
    def presets_save():
        data = _body()
        return S().preset_add(_pick(data, "name", default=""),
                              overwrite=bool(_pick(data, "overwrite",
                                                   default=False)),
                              unique=not bool(_pick(data, "overwrite",
                                                    default=False)))

    @app.route("/api/presets/apply", methods=["POST"])
    @api
    def presets_apply():
        return S().preset_apply(_pick(_body(), "name", default=""))

    @app.route("/api/presets/delete", methods=["POST"])
    @api
    def presets_delete():
        return S().preset_remove(_pick(_body(), "name", default=""))

    @app.route("/api/presets/rename", methods=["POST"])
    @api
    def presets_rename():
        data = _body()
        return S().preset_rename(_pick(data, "name", default=""),
                                 _pick(data, "new", "new_name", default=""))

    @app.route("/api/presets/duplicate", methods=["POST"])
    @api
    def presets_duplicate():
        return S().preset_duplicate(_pick(_body(), "name", default=""))

    @app.route("/api/presets/move", methods=["POST"])
    @api
    def presets_move():
        data = _body()
        return S().preset_move(_pick(data, "name", default=""),
                               int(_pick(data, "delta", default=-1)))

    # ------------------------------------------------------------ 兜底
    @app.errorhandler(404)
    def not_found(_exc):
        return fail("没有这个接口", 404)

    @app.errorhandler(405)
    def not_allowed(_exc):
        return fail("请求方法不对", 405)

    return app


def _sse(event):
    payload = json.dumps(event, ensure_ascii=False)
    return "data: %s\n\n" % payload