"""Web 前端的设备会话层。

把原来 Tkinter 版本里的 ``_AsyncWorker`` 与散落在 ``App`` 上的一大堆
``self.xxx`` 状态，收敛成一个**单工作线程 + 快照缓存 + 事件总线**的对象。

三条设计约束，都是从原实现里踩出来的：

1. **所有设备 I/O 只走一个工作线程。**
   :meth:`vial_light.device.VialDevice._send` 里虽然有 ``_io_lock``，但它只
   保证**单条报文**原子。像「逐键推 74 颗」这种一次几十条报文的操作，如果
   两个线程同时发起，报文就会互相穿插，固件收到的东西完全错乱。所以这里
   用一条 ``queue.Queue`` 把所有操作串起来，HTTP 请求线程只负责入队 + 等待。
2. **状态以快照为准，``GET /api/state`` 绝不碰设备。**
   界面刷新永远读内存里的快照，想读真机必须显式调 ``/api/light/read``、
   ``/api/leds/refresh`` 之类。这样 UI 不会因为设备卡住而卡住。
3. **改灯走 40ms 节流。**
   对应原 ``queue_state`` + ``after(40, _flush)``：拖动滑块时先把改动记在
   ``_pending`` 里，攒够 40ms 再一次性下发，等价于原界面的「实时下发」开关。

事件总线（``log`` / ``state`` / ``busy``）通过 :meth:`subscribe` 暴露给
SSE 连接；每个订阅者拿到一个独立队列，满了就丢，绝不让慢客户端拖住设备线程。
"""

import atexit
import queue
import threading
import time
import traceback

from .. import effects
from .. import kbdef
from .. import presets as presets_mod
from ..colors import clamp, hsv_to_hex
from ..device import AmkStripLed, VialDevice, ZONE_LABELS, ZONES
from ..transport import backend_description

#: 「实时下发」的攒批间隔（秒），与原 GUI 的 40ms 一致。
LIGHT_FLUSH_DELAY = 0.04
#: 单条命令在 HTTP 线程上的最长等待（秒）。逐键全量推送在慢设备上会很久。
CMD_TIMEOUT = 300.0
#: 日志环形缓冲长度（新开的页面能补上历史记录）。
LOG_LIMIT = 500

#: 分区默认代表色（与原 GUI 一致：轴灯蓝、配件灯青）。
DEFAULT_ZONE_COLORS = {
    "key": (171, 255, 255),
    "acc": (128, 255, 255),
}

BACKEND_SHORT = {
    "amk": "AMK 通道",
    "vialrgb": "VialRGB",
    "via": "VIA 通道",
    None: "无灯光通道",
}


class SessionClosed(Exception):
    """会话已关闭时仍在提交命令。"""


class DeviceSession(object):
    """一个进程内唯一的设备会话。"""

    # ------------------------------------------------------------------
    # 构造 / 线程
    # ------------------------------------------------------------------
    def __init__(self):
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._subs = []
        self._logs = []
        self._closed = False
        self._busy = 0

        # --- 设备 ---
        self._dev = None
        self._devices = []
        self._devices_all_raw = False
        self._device_key = None

        # --- 灯光状态（对应原 self.state）---
        self.light = None
        self._pending = None
        self._flush_job = None
        self.dirty = False
        self.live = True

        # --- 分区 ---
        self.zone_supported = ()
        self.zone_reason = "尚未连接设备"
        self.zone_colors = dict(DEFAULT_ZONE_COLORS)
        self.zone_counts = {"key": 0, "acc": 0}

        # --- 配件灯条 ---
        self.strips = []

        # --- 逐键 ---
        self.leds = []
        self.led_colors = []
        self.led_known = set()
        self._pushed = {}
        self.perkey_custom = False

        # --- 配列 / 信息 ---
        self.layout = None
        self.info = None
        self.definition = None
        self.transport_name = None
        try:
            self.transport_name = backend_description()
        except Exception:
            self.transport_name = None

        # --- 方案库 ---
        self._store = presets_mod.PresetStore()
        self._store_error = self._store.error

        self._thread = threading.Thread(target=self._worker, name="vial-io")
        self._thread.daemon = True
        self._thread.start()

        atexit.register(self.close)

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, box, done = item
            try:
                box["result"] = fn()
            except BaseException as exc:      # 设备层什么都可能抛
                box["error"] = exc
                box["trace"] = traceback.format_exc()
            finally:
                done.set()

    def submit(self, fn, timeout=CMD_TIMEOUT):
        """把 ``fn`` 排到设备线程执行，阻塞等结果。"""
        if self._closed:
            raise SessionClosed("会话已关闭")
        box = {}
        done = threading.Event()
        self._busy_enter()
        try:
            self._queue.put((fn, box, done))
            if not done.wait(timeout):
                raise RuntimeError("设备命令超时（%.0f 秒）" % timeout)
        finally:
            self._busy_exit()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._cancel_flush()
        dev = self._dev
        self._dev = None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
        self._queue.put(None)

    # ------------------------------------------------------------------
    # 事件总线
    # ------------------------------------------------------------------
    def subscribe(self):
        q = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _emit(self, kind, payload):
        event = {"kind": kind, "ts": time.time(), "data": payload}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def log(self, text, level="info"):
        """写一条日志（同时进环形缓冲 + 推给所有 SSE 连接）。"""
        entry = {"ts": time.time(), "level": level, "text": text}
        with self._lock:
            self._logs.append(entry)
            if len(self._logs) > LOG_LIMIT:
                del self._logs[:len(self._logs) - LOG_LIMIT]
        self._emit("log", entry)

    def logs(self):
        with self._lock:
            return list(self._logs)

    def _busy_enter(self):
        with self._lock:
            self._busy += 1
            value = self._busy
        self._emit("busy", {"count": value})

    def _busy_exit(self):
        with self._lock:
            if self._busy > 0:
                self._busy -= 1
            value = self._busy
        self._emit("busy", {"count": value})

    def _emit_state(self):
        self._emit("state", self.state_dict())

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def state_dict(self):
        """当前完整状态（纯 JSON 友好的结构）。

        **只读内存，不碰设备。**
        """
        with self._lock:
            dev = self._dev
            connected = dev is not None
            backend = dev.lighting_backend if dev is not None else None
            return {
                "connected": connected,
                "busy": self._busy,
                "transport": self.transport_name,
                "backend": backend,
                "backend_short": BACKEND_SHORT.get(backend, backend),
                "backend_label": effects.backend_label(backend),
                "device": self.info,
                "device_key": self._device_key,
                "live": self.live,
                "dirty": self.dirty,
                "light": self._light_dict(),
                "light_range": self._light_range(),
                "zone": {
                    "supported": list(self.zone_supported),
                    "capable": len(self.zone_supported) >= 2 and connected,
                    "reason": self.zone_reason,
                    "colors": {z: list(self.zone_colors[z]) for z in ZONES},
                    "counts": dict(self.zone_counts),
                    "hint": self._zone_hint(),
                },
                "strips": [dict(s) for s in self.strips],
                "perkey": self._perkey_dict(),
                "layout": self.layout,
                "presets": {
                    "path": self._store.path if self._store else None,
                    "error": self._store_error,
                    "names": self._store.names() if self._store else [],
                    "items": self._preset_items(),
                },
            }

    def _light_dict(self):
        if self.light is None:
            return None
        out = self.light.as_dict()
        out["hex"] = hsv_to_hex(self.light.hue, self.light.sat, self.light.val)
        out["label"] = self.light_label(self.light.effect)
        if self._pending:
            out["pending"] = dict(self._pending)
        return out

    def _light_range(self):
        dev = self._dev
        if dev is None:
            return {"effect_ids": [], "speed": [0, 255]}
        try:
            lo, hi = dev.speed_range()
        except Exception:
            lo, hi = 0, 255
        return {"effect_ids": self._effect_ids(), "speed": [lo, hi]}

    def light_label(self, mode_id):
        dev = self._dev
        if dev is None or mode_id is None:
            return ""
        try:
            return dev.effect_label(mode_id)
        except Exception:
            return ""

    def _perkey_dict(self):
        dev = self._dev
        if dev is None:
            return {"supported": False, "custom": False, "leds": [],
                    "colors": [], "known": [], "count": 0}
        leds = []
        for led in self.leds:
            leds.append({
                "i": led.index,
                "g": led.global_index,
                "x": led.x,
                "y": led.y,
                "row": led.row,
                "col": led.col,
                "zone": led.zone,
                "writable": self._led_writable(led),
            })
        return {
            "supported": bool(dev.perkey_supported()) or dev.lighting_backend == "vialrgb",
            "custom": self.perkey_custom,
            "leds": leds,
            "colors": [list(c) for c in self.led_colors],
            "known": sorted(self.led_known),
            "count": len(self.leds),
            "backend": dev.lighting_backend,
        }

    def _led_writable(self, led):
        """这一颗灯能不能被逐键写入。

        AMK 后端的配件灯只能显示（固件的矩阵通道不驱动灯条），
        VialRGB 后端所有灯都能写。
        """
        dev = self._dev
        if dev is None:
            return False
        if dev.lighting_backend == "amk":
            return bool(led.is_matrix)
        return True

    def _zone_hint(self):
        dev = self._dev
        if dev is not None and dev.lighting_backend == "amk":
            return ("灯光页负责轴灯的全局灯效 / 速度；这里的轴灯卡片改的是同一组"
                    "轴灯的颜色 / 亮度 / 灯效。配件灯按灯条单独编辑，"
                    "「全部灯条同步」会把当前灯条的设置套到所有灯条。"
                    "只有 Custom 模式支持自定义颜色、亮度和速度。")
        return ("轴灯卡片写入轴灯那一组 LED；配件灯按灯条单独编辑，"
                "「全部灯条同步」会把当前灯条的设置套到所有灯条。"
                "VialRGB 分区写入会切换全局灯效为 Direct。")

    # ------------------------------------------------------------------
    # 设备枚举 / 连接
    # ------------------------------------------------------------------
    def _device_dict(self, info):
        return {
            "key": info.key,
            "name": info.display_name,
            "manufacturer": info.manufacturer,
            "product": info.product,
            "vendor_id": info.vendor_id,
            "product_id": info.product_id,
            "serial": info.serial,
            "usage_page": info.usage_page,
            "usage": info.usage,
        }

    def list_devices(self, all_raw=False):
        """枚举 HID 接口。``all_raw=True`` 时不过滤 Vial 魔术串。"""

        def job():
            if all_raw:
                found = VialDevice.discover_all_raw()
            else:
                found = VialDevice.discover()
                if not found:
                    found = VialDevice.discover_all_raw()
            self._devices = found
            self._devices_all_raw = all_raw
            return [self._device_dict(d) for d in found]

        result = self.submit(job)
        self._emit_state()
        return result

    def connect(self, index=0, all_raw=False):
        """连接第 ``index`` 个设备；``index < 0`` 或设备不存在时连第一个。"""

        def job():
            self._teardown()
            # 重新枚举一次，避免用的是过期的列表
            if all_raw:
                found = VialDevice.discover_all_raw()
            else:
                found = VialDevice.discover()
                if not found:
                    found = VialDevice.discover_all_raw()
            self._devices = found
            self._devices_all_raw = all_raw
            if not found:
                raise RuntimeError("没有找到 Vial / VIA raw HID 设备")
            info = found[index] if 0 <= index < len(found) else found[0]

            dev = VialDevice(info)
            dev.set_io_profile(fast=True)
            dev.open()
            self._dev = dev
            try:
                dev.read_versions()
                dev.detect_lighting()
                self.definition = dev.read_definition()
            except Exception:
                # 定义读坏了不该让连接失败（describe() 也是这个态度）
                self.definition = None

            self._device_key = {
                "vendor_id": info.vendor_id,
                "product_id": info.product_id,
                "keyboard_uid": (None if dev.keyboard_uid is None
                                 else "0x%016X" % dev.keyboard_uid),
                "backend": dev.lighting_backend,
            }
            self.info = self._describe()
            self.log("已连接 %s" % info.display_name, "ok")
            self.log("启用后端：%s" % effects.backend_label(dev.lighting_backend), "ok")
            for name, note in dev.methods_tried:
                self.log("探测 %-9s %s" % (name, note), "info")

            # 逐键 LED + 配列 + 分区
            self._load_leds()
            self._load_layout()
            self._pull_light(quiet=True)
            self._refresh_zone()
            self._read_strips()
            self._sync_zone_from_device(quiet=True)
            return self._device_dict(info)

        result = self.submit(job)
        self._emit_state()
        return result

    def disconnect(self, quiet=False):
        def job():
            self._teardown()
            if not quiet:
                self.log("已断开设备连接", "info")
            return True

        self.submit(job)
        self._emit_state()

    def _teardown(self):
        """释放设备与全部缓存（必须在工作线程里调）。"""
        self._cancel_flush()
        dev = self._dev
        self._dev = None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass
        self.light = None
        self._pending = None
        self.dirty = False
        self.info = None
        self.definition = None
        self._device_key = None
        self.zone_supported = ()
        self.zone_reason = "尚未连接设备"
        self.zone_counts = {"key": 0, "acc": 0}
        self.strips = []
        self.leds = []
        self.led_colors = []
        self.led_known = set()
        self._pushed = {}
        self.perkey_custom = False
        self.layout = None

    def _describe(self):
        dev = self._dev
        if dev is None:
            return None
        try:
            return dev.describe()
        except Exception as exc:
            self.log("读取设备信息失败: %s" % exc, "err")
            return None

    def refresh_info(self):
        self.info = self.submit(self._describe)
        self._emit_state()
        return self.info

    # ------------------------------------------------------------------
    # 灯光
    # ------------------------------------------------------------------
    def _require_dev(self):
        dev = self._dev
        if dev is None:
            raise RuntimeError("尚未连接设备")
        return dev

    def _effect_ids(self):
        dev = self._dev
        if dev is None:
            return []
        try:
            return list(dev.effect_ids())
        except Exception:
            return []

    def effects_list(self):
        ids = self._effect_ids()
        dev = self._dev
        out = []
        for eid in ids:
            out.append({
                "id": eid,
                "label": "%d  %s" % (eid, dev.effect_label(eid) if dev else ""),
                "desc": dev.effect_label(eid) if dev else "",
            })
        return out

    def _pull_light(self, quiet=False):
        dev = self._dev
        if dev is None:
            return None
        try:
            self.light = dev.read_lighting()
        except Exception as exc:
            if not quiet:
                self.log("读取灯光状态失败: %s" % exc, "err")
            return self.light
        return self.light

    def read_light(self):
        """从键盘读回当前灯光状态（显式操作，会碰设备）。"""

        def job():
            self._require_dev()
            self._pull_light()
            self._cancel_flush()
            self._pending = None
            self._set_dirty(False)
            self.log("已从键盘读取灯光状态", "ok")
            return self._light_dict()

        result = self.submit(job)
        self._emit_state()
        return result

    def save_light(self):
        def job():
            dev = self._require_dev()
            if self.dirty and self._pending:
                self._apply_pending()
            ok, resp = dev.save()
            if ok:
                self.log("已请求固件保存当前灯光配置", "ok")
            else:
                self.log("固件未处理保存命令（响应 %s）。AMK 固件通常是即改即存，"
                         "可忽略。" % str(resp)[:2], "warn")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    def set_light(self, effect=None, speed=None, hue=None, sat=None, val=None,
                  apply_now=False):
        """改灯光参数。默认走 40ms 节流；``apply_now=True`` 立刻下发。"""
        with self._lock:
            if self.light is None:
                raise RuntimeError("尚未连接设备")
            changed = {}
            if effect is not None and int(effect) != self.light.effect:
                self.light.effect = int(effect)
                changed["effect"] = int(effect)
            if speed is not None and int(speed) != self.light.speed:
                self.light.speed = clamp(speed, 0, 255)
                changed["speed"] = self.light.speed
            if hue is not None and int(hue) != self.light.hue:
                self.light.hue = clamp(hue)
                changed["hue"] = self.light.hue
            if sat is not None and int(sat) != self.light.sat:
                self.light.sat = clamp(sat)
                changed["sat"] = self.light.sat
            if val is not None and int(val) != self.light.val:
                self.light.val = clamp(val)
                changed["val"] = self.light.val
            if changed:
                if self._pending is None:
                    self._pending = {}
                self._pending.update(changed)
                self._set_dirty(True)

        if changed:
            if apply_now:
                self.apply_light()
            else:
                self._schedule_flush()
        self._emit_state()
        return self._light_dict()

    def apply_light(self):
        """把待下发的改动立刻写到设备。"""
        self._cancel_flush()
        result = self.submit(self._apply_pending)
        self._emit_state()
        return result

    def _apply_pending(self):
        """（工作线程内）把 ``_pending`` 一次性下发。"""
        changes = self._pending
        self._pending = None
        dev = self._dev
        if dev is None or not changes:
            self._set_dirty(False)
            return False
        dev.set_all(**changes)
        self._set_dirty(False)
        return True

    def _set_dirty(self, value):
        with self._lock:
            self.dirty = bool(value)
            if not value:
                self._pending = None

    def set_live(self, live):
        """「实时下发」开关。"""
        with self._lock:
            self.live = bool(live)
        if self.live and self._pending:
            self._schedule_flush()
        self._emit_state()
        return self.live

    def _schedule_flush(self):
        with self._lock:
            if not self.live or self._closed or self._flush_job is not None:
                return
            timer = threading.Timer(LIGHT_FLUSH_DELAY, self._flush_timer)
            timer.daemon = True
            self._flush_job = timer
        timer.start()

    def _cancel_flush(self):
        with self._lock:
            job = self._flush_job
            self._flush_job = None
        if job is not None:
            try:
                job.cancel()
            except Exception:
                pass

    def _flush_timer(self):
        with self._lock:
            self._flush_job = None
        if self._closed or self._dev is None or not self._pending:
            return
        try:
            self.submit(self._apply_pending)
            self._emit_state()
        except Exception as exc:
            self.log("实时下发失败: %s" % exc, "err")

    # ------------------------------------------------------------------
    # 分区
    # ------------------------------------------------------------------
    def _refresh_zone(self):
        dev = self._dev
        if dev is None:
            self.zone_supported = ()
            self.zone_reason = "尚未连接设备"
            self.zone_counts = {"key": 0, "acc": 0}
            return
        try:
            zones, reason = dev.zone_support()
        except Exception as exc:
            zones, reason = (), "分区能力检测失败: %s" % exc
        counts = {"key": 0, "acc": 0}
        if zones:
            try:
                counts = dev.zone_counts()
            except Exception:
                counts = {"key": 0, "acc": 0}
        self.zone_supported = tuple(zones)
        self.zone_reason = reason
        self.zone_counts = counts

    def set_zone_color(self, zone, hue=None, sat=None, val=None, apply_now=True):
        """改一侧的代表色。改完默认立刻推送（与原 GUI 行为一致）。"""
        if zone not in ZONES:
            raise ValueError("分区只能是 key / acc")
        blocked = self._zone_blocked()
        if blocked:
            raise RuntimeError(blocked)
        h, s, v = self.zone_colors[zone]
        if hue is not None:
            h = clamp(hue)
        if sat is not None:
            s = clamp(sat)
        if val is not None:
            v = clamp(val)
        self.zone_colors[zone] = (h, s, v)
        if apply_now and self._zone_active(zone):
            self.zone_push([zone])
        else:
            self._emit_state()
        return list(self.zone_colors[zone])

    def _zone_blocked(self):
        if self._dev is None:
            return "未连接设备"
        if len(self.zone_supported) < 2:
            return "此固件不支持分区控制：%s" % (self.zone_reason or "无独立寻址接口")
        return None

    def _zone_active(self, zone):
        """这一侧现在能不能推送。

        原「作用范围」开关已移除 —— 它只决定卡片能不能点，不改变任何下发行为，
        所以现在设备支持哪一侧就哪一侧可用。
        """
        if self._dev is None:
            return False
        return zone in self.zone_supported

    def zone_push(self, zones):
        """推送分区颜色。``zones`` 是要推送的分区列表（``key`` / ``acc``）。"""
        blocked = self._zone_blocked()
        if blocked:
            raise RuntimeError(blocked)
        targets = [z for z in (zones or []) if z in ZONES]
        if not targets:
            raise ValueError("要推送的分区为空（只能是 key / acc）")

        result = self.submit(lambda: self._zone_push_locked(targets))
        self._emit_state()
        return result

    def _zone_push_locked(self, targets):
        """（**只能在工作线程内调用**）真正的分区推送。

        ``zone_push()`` 只是把它排进设备队列。已经在工作线程上的调用方
        （``preset_apply()`` 的 ``job()``）必须**直接调这个方法** ——
        否则会再次 ``submit()`` 到同一个队列，而唯一能消费队列的就是它自己，
        于是死锁到 ``CMD_TIMEOUT`` 才抛超时。实测复现过：
        ``设备命令超时（300 秒）``。
        """
        dev = self._require_dev()
        if dev.lighting_backend == "amk":
            return self._push_zone_amk(targets)

        if not self.leds:
            raise RuntimeError("没有逐键 LED 数据，无法分区推送")
        if self.light is None:
            self.light = dev.read_lighting()
        if self.light.effect != effects.VIALRGB_DIRECT:
            dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
            self.light.effect = effects.VIALRGB_DIRECT
        if not self.led_colors or len(self.led_colors) != len(self.leds):
            self.led_colors = [(0, 0, 0)] * len(self.leds)
        for led in self.leds:
            if led.zone in targets:
                self.led_colors[led.index] = tuple(self.zone_colors[led.zone])
        total = dev.push_zoned(self.led_colors, targets)
        self.led_known.update(
            led.global_index for led in self.leds if led.zone in targets)
        names = "、".join(ZONE_LABELS.get(z, z) for z in targets)
        self.log("已按分区推送 %s（%d 颗 LED）" % (names, total), "ok")
        return total

    def _push_zone_amk(self, targets):
        """（工作线程内）AMK 后端的分区推送。

        轴灯（``0x80``–``0x83``）与配件灯（``0xFD`` STRIP 命令族）是两套
        独立状态，所以「仅轴灯」不会动到配件灯 —— 这正是原厂行为。
        """
        dev = self._dev
        if self.light is None:
            self.light = dev.read_lighting()
        written = 0
        if "key" in targets:
            h, s, v = self.zone_colors["key"]
            dev.set_color(h, s, v)
            self.light.hue, self.light.sat, self.light.val = h, s, v
            written += dev.num_key_leds()
        if "acc" in targets:
            h, s, v = self.zone_colors["acc"]
            for strip in dev.strips():
                was_custom = strip.mode == effects.STRIP_EFFECT_CUSTOM
                if dev.set_strip_color(strip, h, s, v, on=v > 0, force=True):
                    written += strip.count
                    if not was_custom:
                        self.log("批量改色：灯条 %d 已从原灯效切到「自定义」"
                                 % (strip.index + 1), "warn")
            self._read_strips()
        names = "、".join(ZONE_LABELS.get(z, z) for z in targets)
        self.log("已按分区推送 %s（%d 颗灯）" % (names, written), "ok")
        return written

    def zone_sync(self):
        """从设备实际可回读的通道同步两侧代表色。"""
        blocked = self._zone_blocked()
        if blocked:
            raise RuntimeError(blocked)

        def job():
            dev = self._require_dev()
            if dev.lighting_backend == "amk":
                self._pull_light(quiet=True)
                if self.light is not None:
                    self.zone_colors["key"] = (
                        self.light.hue, self.light.sat, self.light.val)
                tally = {}
                for strip in dev.strips():
                    try:
                        led = dev.read_strip_led(strip.start)
                    except Exception:
                        led = None
                    if led is not None:
                        colour = (led.hue, led.sat, led.val)
                        tally[colour] = tally.get(colour, 0) + 1
                if tally:
                    colour = max(tally.items(), key=lambda item: item[1])[0]
                    self.zone_colors["acc"] = colour
                self._read_strips()
                self.log("已从 AMK 设备状态同步轴灯和配件灯代表色", "ok")
                return True

            if dev.lighting_backend == "vialrgb":
                self._pull_light(quiet=True)
                self.log("VialRGB 协议不能回读逐灯颜色，未修改分区代表色", "warn")
                return True

            self.log("当前灯光后端没有可回读的分区颜色通道", "warn")
            return False

        result = self.submit(job)
        self._emit_state()
        return result

    def _sync_zone_from_device(self, quiet=False):
        """连接后自动回填分区代表色（工作线程内调用）。"""
        dev = self._dev
        if dev is None or len(self.zone_supported) < 2:
            return
        try:
            if dev.lighting_backend == "amk" and self.light is not None:
                self.zone_colors["key"] = (
                    self.light.hue, self.light.sat, self.light.val)
            if dev.lighting_backend == "vialrgb" and self.light is not None:
                self.zone_colors["key"] = (
                    self.light.hue, self.light.sat, self.light.val)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 配件灯条
    # ------------------------------------------------------------------
    def _read_strips(self):
        """（工作线程内）读回灯条结构 + 逐灯聚合状态。

        逐灯全读（每条 4-6 颗）是为了界面能给出「几颗亮着、是不是同色」——
        灯条级参数通道在本机固件上是坏的（``SET_RGB_PARAM`` 回 0x55），
        逐灯读是**唯一**能问到真实颜色的办法。
        """
        dev = self._dev
        if dev is None or dev.lighting_backend != "amk":
            self.strips = []
            return []
        out = []
        for s in dev.strips():
            leds = []
            for i in range(s.count):
                try:
                    led = dev.read_strip_led(s.start + i)
                except Exception:
                    led = None
                if led is not None:
                    leds.append((i, led))
            first = leds[0][1] if leds else None
            # 把读回来的代表色 / 速度回填到 AmkStrip 上。
            #
            # ``device.read_strips()`` 只填 start / count / config / mode，
            # ``hue/sat/val/speed`` 一直是构造函数里的 0。而
            # ``set_strip_color()`` 在改亮度时会拿 ``s.hue, s.sat`` 当基准，
            # ``strip_sync_all()`` 也靠这几个字段 —— 不回填的话「调亮度」会把
            # 颜色一起冲成 (0,0,0)（实测：青色 #00fffc 直接变灰 #808080）。
            if first is not None:
                s.hue, s.sat, s.val = first.hue, first.sat, first.val
                s.speed = first.speed
            colors = set((l.hue, l.sat, l.val) for _, l in leds)
            out.append({
                "index": s.index,
                "start": s.start,
                "count": s.count,
                "mode": s.mode,
                "mode_label": effects.strip_effect_label(s.mode),
                "mode_name": effects.strip_effect_name(s.mode)[1],
                "editable": effects.strip_mode_editable(s.mode),
                "hue": first.hue if first else 0,
                "sat": first.sat if first else 0,
                "val": first.val if first else 0,
                "speed": first.speed if first else 0,
                "on": bool(first.on) if first else False,
                "on_count": sum(1 for _, l in leds if l.on),
                "read_count": len(leds),
                "uniform": len(colors) <= 1,
                "hex": (hsv_to_hex(first.hue, first.sat, first.val)
                        if first else "#000000"),
                # 逐灯明细：界面按 offset 画成一颗颗灯，点一颗只改那一颗
                "leds": [{
                    "offset": i,
                    "hue": l.hue,
                    "sat": l.sat,
                    "val": l.val,
                    "on": bool(l.on),
                    "speed": l.speed,
                } for i, l in leds],
            })
        self.strips = out
        return out

    def strips_refresh(self):
        result = self.submit(self._read_strips)
        self._emit_state()
        return result

    def _require_strip(self, index):
        dev = self._require_dev()
        strips = dev.strips()
        if not strips:
            raise RuntimeError("这把键盘没有读到配件灯条")
        if index is None or not (0 <= index < len(strips)):
            index = 0
        return dev, strips[index]

    def strip_set_mode(self, index, mode):
        def job():
            dev, s = self._require_strip(index)
            ok = dev.set_strip_mode(s, mode)
            self._read_strips()
            if ok:
                self.log("灯条 %d 灯效已切到「%s」（%d）"
                         % (s.index + 1, effects.strip_effect_name(int(mode) % 10)[1],
                            int(mode) % 10), "ok")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    def strip_sync_all(self, index):
        """把**当前灯条**的设置套用到全部灯条（灯效档 + 颜色 / 亮度 / 速度）。

        取代原「配件灯（批量）」卡片 —— 批量上色现在走这里，而不是靠一个
        单独的 acc 代表色。非 Custom 档由固件自己渲染，此时只同步灯效档。
        """
        def job():
            dev, src = self._require_strip(index)
            strips = dev.strips()
            if not strips:
                raise RuntimeError("这把键盘没有读到配件灯条")
            custom = src.mode == effects.STRIP_EFFECT_CUSTOM
            for s in strips:
                dev.set_strip_mode(s, src.mode)
                if custom:
                    dev.set_strip_color(s, src.hue, src.sat, src.val,
                                        on=src.val > 0)
                    dev.set_strip_led_speed(s, src.speed)
            self._read_strips()
            if custom:
                self.log("已把灯条 %d 同步到全部 %d 条：灯效档 %d、%s、亮度 %d、速度 %d"
                         % (src.index + 1, len(strips), src.mode,
                            hsv_to_hex(src.hue, src.sat, src.val),
                            src.val, src.speed), "ok")
            else:
                self.log("已把灯条 %d 的灯效档「%s」（%d）同步到全部 %d 条灯条"
                         % (src.index + 1, effects.strip_effect_name(src.mode)[1],
                            src.mode, len(strips)), "ok")
            return len(strips)

        result = self.submit(job)
        self._emit_state()
        return result

    def strip_set_led(self, index, offset, hue, sat, val):
        """只给当前灯条的**某一颗**灯上色，同条灯条的其它灯不动。"""
        def job():
            dev, s = self._require_strip(index)
            # 别复用 ``offset`` 这个名字：它是外层参数，在嵌套函数里赋值会让它
            # 变成 job() 的局部变量，下一行的读取就会抛 UnboundLocalError。
            pos = int(offset)
            if not (0 <= pos < s.count):
                raise ValueError("第 %d 颗超出灯条 %d 的范围（共 %d 颗）"
                                 % (pos + 1, s.index + 1, s.count))
            h, sa, v = clamp(hue), clamp(sat), clamp(val)
            was_custom = s.mode == effects.STRIP_EFFECT_CUSTOM
            if not was_custom:
                # 固件只在 Custom 档采用逐灯颜色，不切档这一颗不会亮
                dev.set_strip_mode(s, effects.STRIP_EFFECT_CUSTOM)
            led = dev.read_strip_led(s.start + pos)
            if led is None:
                led = AmkStripLed(s.start + pos, h, sa, v, 0)
            led = led.with_hsv(h, sa, v).with_flags(on=1 if v > 0 else 0)
            ok = dev.set_strip_led(led)
            self._read_strips()
            if ok:
                if not was_custom:
                    self.log("灯条 %d 已从原灯效切到「自定义」档以显示自选颜色"
                             % (s.index + 1), "warn")
                self.log("灯条 %d 第 %d 颗已上色 %s"
                         % (s.index + 1, pos + 1, hsv_to_hex(h, sa, v)), "ok")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    def strip_set_color(self, index, hue, sat, val=None, on=None):
        def job():
            dev, s = self._require_strip(index)
            h, sa, v = s.hue, s.sat, s.val
            if hue is not None:
                h = clamp(hue)
            if sat is not None:
                sa = clamp(sat)
            if val is not None:
                v = clamp(val)
            # 别复用 ``on`` 这个名字：它是外层参数，在嵌套函数里赋值会让它变成
            # job() 的局部变量，下一行的读取就会抛 UnboundLocalError。
            turn_on = (v > 0) if on is None else bool(on)
            was_custom = s.mode == effects.STRIP_EFFECT_CUSTOM
            ok = dev.set_strip_color(s, h, sa, v, on=turn_on, force=True)
            self._read_strips()
            if ok:
                if not was_custom:
                    self.log("灯条 %d 已从原灯效切到「自定义」档以显示自选颜色"
                             % (s.index + 1), "warn")
                self.log("灯条 %d 已上色 %s" % (s.index + 1, hsv_to_hex(h, sa, v)), "ok")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    def strip_set_bright(self, index, val):
        def job():
            dev, s = self._require_strip(index)
            h, sa = s.hue, s.sat
            was_custom = s.mode == effects.STRIP_EFFECT_CUSTOM
            ok = dev.set_strip_color(s, h, sa, clamp(val), on=clamp(val) > 0,
                                     force=True)
            self._read_strips()
            if ok and not was_custom:
                self.log("灯条 %d 已切到「自定义」档以应用亮度" % (s.index + 1), "warn")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    def strip_set_speed(self, index, speed):
        def job():
            dev, s = self._require_strip(index)
            ok = dev.set_strip_led_speed(s, speed)
            self._read_strips()
            if ok and not effects.strip_mode_editable(s.mode):
                self.log("灯条 %d 当前不是自定义档，逐灯速度不会被固件采用"
                         % (s.index + 1), "warn")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    # ------------------------------------------------------------------
    # 配列 / 逐键
    # ------------------------------------------------------------------
    def _load_layout(self):
        defn = self.definition
        if not defn:
            self.layout = None
            return None
        try:
            kle = kbdef.parse_layout(defn)
            grid = kbdef.matrix_grid_layout(defn)
        except Exception as exc:
            self.log("解析键盘配列失败: %s" % exc, "err")
            self.layout = None
            return None
        montage = kbdef.layout_looks_montage(kle)
        self.layout = {
            "name": defn.get("name") or "",
            "matrix": defn.get("matrix") or {},
            "kle": self._layout_json(kle),
            "grid": self._layout_json(grid),
            "montage": montage,
            "key_count": len(kle),
            "warn": (
                "旋转（r / rx / ry）已按 KLE 规范还原，但这把键盘的定义把配列"
                "拆成了 %d 个区块，按 KLE 累加会把键盘拉成长条 —— 要准确对照"
                "灯光请看「矩阵网格」。"
                % len((defn.get("layouts") or {}).get("keymap") or [])
                if montage else ""),
        }
        return self.layout

    @staticmethod
    def _layout_json(layout):
        keys = []
        for k in layout.keys:
            keys.append({
                "x": k.x, "y": k.y, "w": k.w, "h": k.h,
                "row": k.row, "col": k.col,
                "r": k.r, "rx": k.rx, "ry": k.ry,
            })
        return {
            "min_x": layout.min_x, "min_y": layout.min_y,
            "max_x": layout.max_x, "max_y": layout.max_y,
            "w": layout.width_units, "h": layout.height_units,
            "keys": keys,
            "mapped": sorted([list(rc) for rc in layout.mapped]),
        }

    def get_layout(self):
        with self._lock:
            return self.layout

    def _load_leds(self):
        """（工作线程内）读逐键 LED 表 + 初始化颜色缓冲。"""
        dev = self._dev
        if dev is None:
            return []
        leds = []
        try:
            if dev.lighting_backend == "amk":
                leds = dev.amk_leds()
            else:
                leds = dev.vialrgb_leds()
        except Exception as exc:
            self.log("读取 LED 表失败: %s" % exc, "err")
            leds = []
        self.leds = leds
        self.led_colors = [(0, 0, 0)] * len(leds)
        self.led_known = set()
        self._pushed = {}
        try:
            self.perkey_custom = bool(dev.lighting_backend == "amk"
                                      and dev.matrix_in_custom())
        except Exception:
            self.perkey_custom = False
        return leds

    def leds_refresh(self):
        def job():
            self._require_dev()
            self._load_leds()
            self.log("已刷新逐键 LED 表（%d 颗）" % len(self.leds), "ok")
            return self._perkey_dict()

        result = self.submit(job)
        self._emit_state()
        return result

    def get_leds(self):
        with self._lock:
            return self._perkey_dict()

    def perkey_set_colors(self, colors):
        """前端直接给一份完整颜色表（``[[h,s,v], ...]``，按显示序号）。"""
        with self._lock:
            if not self.led_colors or len(self.led_colors) != len(self.leds):
                self.led_colors = [(0, 0, 0)] * len(self.leds)
            for item in colors or []:
                idx = int(item.get("i"))
                if 0 <= idx < len(self.led_colors):
                    self.led_colors[idx] = (
                        clamp(item.get("h", 0)), clamp(item.get("s", 0)),
                        clamp(item.get("v", 0)))
        return self._perkey_dict()

    def perkey_push(self, changed=None):
        """把逐键缓冲推到键盘。

        ``changed`` 是一组 ``display index``（前端画笔涂过的那几颗）；
        ``None`` 表示「已知的全都推」。内部还会再和上次推送做一次差量。
        """
        def job():
            dev = self._require_dev()
            if not self.leds:
                raise RuntimeError("还没有 LED 数据，请先刷新")

            if dev.lighting_backend == "amk":
                if not dev.perkey_supported():
                    raise RuntimeError("该固件不支持逐键上色（矩阵通道不可用）")
                allowed = (set(self.led_known) if changed is None
                           else set(int(i) for i in changed))
                if changed is None and not allowed:
                    raise RuntimeError("还没有已知的逐键颜色，请先点按、填充或从键盘读取")
                target = {}
                for led in self.leds:
                    if led.row is None or led.col is None:
                        continue          # 配件灯不走这条路
                    if led.index not in allowed:
                        continue
                    target[led.global_index] = tuple(self.led_colors[led.index])
                diff = {gi: hsv for gi, hsv in target.items()
                        if self._pushed.get(gi) != hsv}
                if not diff:
                    return 0
                ok, switched = dev.perkey_set(diff)
                if switched:
                    self.perkey_custom = True
                self._pushed.update(diff)
                self.led_known.update(diff)
                self.log("逐键推送 %d 颗%s"
                         % (ok, "（已切到自定义档）" if switched else ""), "ok")
                return ok

            if self.light is None:
                self.light = dev.read_lighting()
            if self.light.effect != effects.VIALRGB_DIRECT:
                dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
                self.light.effect = effects.VIALRGB_DIRECT
            if changed is None:
                sent = dev.vialrgb_push(self.led_colors)
                picked = self.leds
            else:
                wanted = set(int(i) for i in changed)
                pairs = [(led.index, tuple(self.led_colors[led.index]))
                         for led in self.leds if led.index in wanted]
                sent = dev.vialrgb_push_pairs(pairs)
                picked = [led for led in self.leds if led.index in wanted]
            self.led_known.update(led.global_index for led in picked)
            self.log("逐键推送 %d 颗灯" % sent, "ok")
            return sent

        result = self.submit(job)
        self._emit_state()
        return result

    def perkey_fill(self, hue, sat, val):
        with self._lock:
            self.led_colors = [(clamp(hue), clamp(sat), clamp(val))
                               for _ in self.leds]

        def job():
            dev = self._require_dev()
            if not self.leds:
                raise RuntimeError("还没有 LED 数据，请先刷新")
            if dev.lighting_backend == "amk":
                ok, switched = dev.perkey_fill(clamp(hue), clamp(sat), clamp(val))
                if switched:
                    self.perkey_custom = True
                self.led_known.update(
                    led.global_index for led in self.leds if led.row is not None)
                self._pushed.update(
                    (led.global_index, (clamp(hue), clamp(sat), clamp(val)))
                    for led in self.leds if led.row is not None)
                self.log("已把 %d 颗轴灯刷成同一颜色%s"
                         % (ok, "（已切到自定义档）" if switched else ""), "ok")
                return ok
            if self.light is None:
                self.light = dev.read_lighting()
            if self.light.effect != effects.VIALRGB_DIRECT:
                dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
                self.light.effect = effects.VIALRGB_DIRECT
            sent = dev.vialrgb_push(self.led_colors)
            self.led_known.update(led.global_index for led in self.leds)
            self.log("已把 %d 颗灯刷成同一颜色" % sent, "ok")
            return sent

        result = self.submit(job)
        self._emit_state()
        return result

    def perkey_read(self):
        def job():
            dev = self._require_dev()
            if not self.leds:
                raise RuntimeError("还没有 LED 数据，请先刷新")
            if dev.lighting_backend == "amk":
                data = dev.perkey_read()
                if not data:
                    raise RuntimeError("固件没有返回逐键颜色")
                rc = dev.matrix_rc_map()
                by_global = {}
                for (r, c), gidx in rc.items():
                    by_global[gidx] = (r, c)
                n = 0
                for led in self.leds:
                    if led.row is None:
                        continue
                    hit = data.get(led.global_index)
                    if hit is None:
                        continue
                    self.led_colors[led.index] = (hit[0], hit[1], hit[2])
                    self.led_known.add(led.global_index)
                    self._pushed[led.global_index] = (hit[0], hit[1], hit[2])
                    n += 1
                if n == 0:
                    self.log("固件返回的逐键颜色全为 0 —— 通常说明当前不在"
                             "「自定义」档，固件不维护逐键颜色表", "warn")
                else:
                    self.log("已从键盘读回 %d 颗逐键颜色" % n, "ok")
                return n

            self.log("VialRGB 协议不能回读逐灯颜色（协议里没有这条命令）", "warn")
            return 0

        result = self.submit(job)
        self._emit_state()
        return result

    def perkey_enter_custom(self):
        def job():
            dev = self._require_dev()
            if dev.lighting_backend != "amk":
                raise RuntimeError("只有 AMK 后端有「逐灯自定义」档")
            ok = dev.enter_matrix_custom()
            self.perkey_custom = bool(ok)
            if ok:
                self.log("已进入「自定义（逐灯）」档", "ok")
            else:
                self.log("固件没有接受切换到自定义档", "warn")
            return ok

        result = self.submit(job)
        self._emit_state()
        return result

    # ------------------------------------------------------------------
    # 原始报文
    # ------------------------------------------------------------------
    def raw_request(self, text):
        payload = _parse_hex(text)

        def job():
            dev = self._require_dev()
            data = dev.raw_request(payload)
            shown = " ".join("%02X" % b for b in bytearray(data))
            self.log("原始报文 %s -> %s"
                     % (" ".join("%02X" % b for b in bytearray(payload)), shown),
                     "info")
            return shown

        return self.submit(job)

    # ------------------------------------------------------------------
    # 方案库
    # ------------------------------------------------------------------
    def store(self):
        with self._lock:
            if self._store is None:
                self._store = presets_mod.PresetStore()
                self._store_error = self._store.error
            return self._store

    def _preset_items(self):
        store = self._store
        if store is None:
            return []
        return [{"name": p.name, "summary": p.summary(),
                 "updated": p.updated, "device": dict(p.device)}
                for p in store.presets]

    def snapshot_lighting(self):
        """当前整套灯光设置（存方案用），对应原 ``snapshot_lighting``。"""
        with self._lock:
            if self.light is None:
                return None
            zone_key = self.zone_colors.get("key", DEFAULT_ZONE_COLORS["key"])
            zone_acc = self.zone_colors.get("acc", DEFAULT_ZONE_COLORS["acc"])
            # 逐灯配色必须一起存：分区页「逐灯上色」画的图案如果只存一个代表色，
            # 「应用」时会用代表色把整条灯条刷成统一色，图案就没了。
            strips = [{"index": s["index"], "mode": s["mode"],
                       "hue": s["hue"], "sat": s["sat"], "val": s["val"],
                       "speed": s.get("speed", 0),
                       "leds": [{"offset": l["offset"], "hue": l["hue"],
                                 "sat": l["sat"], "val": l["val"],
                                 "on": l["on"], "speed": l["speed"]}
                                for l in s.get("leds", [])]}
                      for s in self.strips]
            led_colors = {}
            if self.leds and self.led_colors:
                for led in self.leds:
                    if led.global_index in self.led_known:
                        led_colors[str(led.global_index)] = list(
                            self.led_colors[led.index])
            return {
                "effect": self.light.effect,
                "speed": self.light.speed,
                "hue": self.light.hue,
                "sat": self.light.sat,
                "val": self.light.val,
                "brightness_max": self.light.brightness_max,
                "zone_colors": {"key": list(zone_key), "acc": list(zone_acc)},
                "led_colors": led_colors,
                "strips": strips,
                "backend": self._dev.lighting_backend if self._dev else None,
            }

    def preset_add(self, name, overwrite=False, unique=True):
        store = self.store()
        snap = self.snapshot_lighting()
        if snap is None:
            raise RuntimeError("尚未连接设备，没有可保存的灯光状态")
        ok, result, was_over = store.add(
            name, snap, device=self._device_key, overwrite=overwrite,
            unique=unique)
        if not ok:
            raise RuntimeError(result)
        store.backup()
        self.log("方案「%s」已%s" % (result, "覆盖" if was_over else "保存"), "ok")
        self._emit_state()
        return {"name": result, "overwritten": was_over}

    def preset_remove(self, name):
        store = self.store()
        ok, err = store.remove(name)
        if not ok:
            raise RuntimeError(err)
        self.log("方案「%s」已删除" % name, "ok")
        self._emit_state()
        return True

    def preset_rename(self, old, new):
        store = self.store()
        ok, err, actual = store.rename(old, new)
        if not ok:
            raise RuntimeError(err)
        self.log("方案「%s」已改名为「%s」" % (old, actual), "ok")
        self._emit_state()
        return actual

    def preset_duplicate(self, name):
        store = self.store()
        ok, err, new_name = store.duplicate(name)
        if not ok:
            raise RuntimeError(err)
        self.log("已复制为「%s」" % new_name, "ok")
        self._emit_state()
        return new_name

    def preset_move(self, name, delta):
        store = self.store()
        store.move(name, delta)       # 已在列表顶端/底端时返回 False，属正常
        self._emit_state()
        return True

    def preset_apply(self, name):
        """把一条方案应用到设备（四步，与原 GUI 顺序一致）。"""
        store = self.store()
        preset = store.get(name)
        if preset is None:
            raise RuntimeError("没有找到方案「%s」" % name)
        data = preset.data

        def job():
            dev = self._require_dev()
            applied = []

            # ① 分区代表色（作用范围开关已移除，轴灯那侧固定推送）
            zc = data.get("zone_colors") or {}
            for zone in ZONES:
                colour = zc.get(zone)
                if isinstance(colour, (list, tuple)) and len(colour) >= 3:
                    self.zone_colors[zone] = (clamp(colour[0]), clamp(colour[1]),
                                              clamp(colour[2]))
            if len(self.zone_supported) >= 2:
                try:
                    # 这里已经在工作线程上了，必须直接调 *_locked* 版本；
                    # 调 zone_push() 会再次 submit 到同一个队列 → 死锁。
                    self._zone_push_locked(["key"])
                    applied.append("分区")
                except Exception as exc:
                    self.log("应用方案的「分区」部分失败: %s" % exc, "err")

            # ② 轴灯 effect / speed / hue / sat / val
            if self.light is None:
                self.light = dev.read_lighting()
            changes = {}
            for key in ("effect", "speed", "hue", "sat", "val"):
                if data.get(key) is not None:
                    changes[key] = int(data[key])
            if changes:
                self.light.effect = changes.get("effect", self.light.effect)
                self.light.speed = changes.get("speed", self.light.speed)
                self.light.hue = changes.get("hue", self.light.hue)
                self.light.sat = changes.get("sat", self.light.sat)
                self.light.val = changes.get("val", self.light.val)
                dev.set_all(**changes)
                applied.append("轴灯")

            # ③ 逐键颜色（只写方案里记过的灯号）
            led_colors = data.get("led_colors") or {}
            if led_colors and self.leds:
                want = {}
                for led in self.leds:
                    hit = led_colors.get(str(led.global_index))
                    if not hit:
                        continue
                    hsv = (clamp(hit[0]), clamp(hit[1]), clamp(hit[2]))
                    self.led_colors[led.index] = hsv
                    self.led_known.add(led.global_index)
                    if led.row is not None:
                        want[led.global_index] = hsv
                if want:
                    try:
                        dev.perkey_set(want)
                        applied.append("逐键")
                    except Exception as exc:
                        self.log("应用方案的「逐键」部分失败: %s" % exc, "err")

            # ④ 配件灯逐条 mode / color / speed
            strips = data.get("strips") or []
            if strips and dev.lighting_backend == "amk":
                dev_strips = dev.strips()
                for item in strips:
                    idx = item.get("index")
                    if idx is None or not (0 <= idx < len(dev_strips)):
                        continue
                    s = dev_strips[idx]
                    if item.get("mode") is not None:
                        dev.set_strip_mode(s, item["mode"])
                    leds = item.get("leds") or []
                    if leds:
                        # 逐灯配色：按 offset 一颗颗写回，保住分区页画的图案
                        for one in leds:
                            off = one.get("offset")
                            if off is None or not (0 <= off < s.count):
                                continue
                            h = clamp(one.get("hue"))
                            sa = clamp(one.get("sat"))
                            v = clamp(one.get("val"))
                            led = dev.read_strip_led(s.start + off)
                            if led is None:
                                led = AmkStripLed(s.start + off, h, sa, v, 0)
                            led = led.with_hsv(h, sa, v).with_flags(
                                on=1 if one.get("on", v > 0) else 0,
                                speed=one.get("speed"))
                            dev.set_strip_led(led)
                    else:
                        # 老方案只存了整条的代表色 → 整条刷成统一色
                        if item.get("hue") is not None:
                            dev.set_strip_color(
                                s, clamp(item.get("hue")), clamp(item.get("sat")),
                                clamp(item.get("val")),
                                on=clamp(item.get("val", 0)) > 0, force=True)
                        if item.get("speed") is not None:
                            dev.set_strip_led_speed(s, item["speed"])
                self._read_strips()
                applied.append("配件灯")

            self._pending = None
            self._set_dirty(False)
            if applied:
                self.log("已应用方案「%s」：%s" % (name, "、".join(applied)), "ok")
            else:
                self.log("方案「%s」是空的，没有可应用的字段" % name, "warn")
            return applied

        result = self.submit(job)
        self._emit_state()
        return result


def _parse_hex(text):
    """``"FD 1C 00"`` / ``"fd1c00"`` -> bytes。"""
    if not text:
        raise ValueError("报文不能为空")
    cleaned = []
    for chunk in str(text).replace(",", " ").replace("-", " ").split():
        cleaned.append(chunk)
    joined = "".join(cleaned)
    if len(joined) % 2:
        raise ValueError("十六进制位数必须是偶数")
    try:
        return bytes(bytearray.fromhex(joined))
    except ValueError:
        raise ValueError("报文里含非法十六进制字符")