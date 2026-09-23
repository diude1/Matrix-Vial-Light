"""Vial 设备与三种照明后端的协议实现。

协议来源（均已按真机核对）：

* VIA raw HID：命令 ``0x01`` 取协议版本，``0x02`` 取键盘值。
* Vial：``0xFE`` 前缀。``0xFE 0x00`` 返回 ``<I`` 协议版本 + 8 字节 UID + 标志字节
  （标志为 1 表示固件编译了 VialRGB）。
* VialRGB：``id_lighting_get_value(0x08)`` / ``id_lighting_set_value(0x07)`` 承载
  ``0x40``-``0x44`` 子命令，逐键直接控制用 ``0x42`` FASTSET，每包最多 9 颗 LED。
* AMK（Matrix 官方固件）：分两层。

  第一层用标准 ``0x07/0x08``，value_id 来自 QMK 官方 ``via_lighting_value``
  （``0x80`` 亮度 / ``0x81`` 灯效 / ``0x82`` 速度 / ``0x83`` 色相+饱和度），
  只控制**轴灯**。

  第二层是 ``0xFD`` 前缀的**扩展协议**（同为 32 字节报文），把灯光分成四个
  互相独立的类型 —— ``MATRIX``(轴灯) / ``STRIP``(配件灯) / ``INDICATOR``
  (指示灯) / ``GRID``(点阵屏)，各有独立的灯效与参数。配件灯就是从这里控的。
  命令表见 ``AMK_GET_RGB_STRIP_*`` 等常量。
"""

import json
import lzma
import struct
import time

from . import effects
from .transport import enumerate_devices, open_device

MSG_LEN = 32
VIAL_SERIAL_MAGIC = "vial:f64c2b3c"

CMD_GET_PROTOCOL_VERSION = 0x01
CMD_GET_KEYBOARD_VALUE = 0x02
CMD_DYNAMIC_KEYMAP_GET_KEYCODE = 0x04
CMD_LIGHTING_SET_VALUE = 0x07
CMD_LIGHTING_GET_VALUE = 0x08
CMD_LIGHTING_SAVE = 0x09

VIAL_GET_KEYBOARD_ID = 0x00
VIAL_GET_SIZE = 0x01
VIAL_GET_DEF = 0x02

VIALRGB_GET_INFO = 0x40
VIALRGB_GET_MODE = 0x41
VIALRGB_GET_SUPPORTED = 0x42
VIALRGB_SET_MODE = 0x41
VIALRGB_GET_NUMBER_LEDS = 0x43
VIALRGB_GET_LED_INFO = 0x44
VIALRGB_DIRECT_FASTSET = 0x42

# ---------------------------------------------------------------------------
# AMK 灯光字段（QMK via_lighting_value 标准编号，非厂商私有）
#
#   quantum/via.h:
#     id_qmk_rgblight_brightness   = 0x80
#     id_qmk_rgblight_effect       = 0x81
#     id_qmk_rgblight_effect_speed = 0x82
#     id_qmk_rgblight_color        = 0x83
#
# 这组字段只控制 **轴灯（RGB Matrix）** 这一路。配件灯走的是下面的
# 0xFD 扩展协议，字段编号与灯效表和这里完全不同。
# ---------------------------------------------------------------------------
AMK_VAL = 0x80
AMK_EFFECT = 0x81
AMK_SPEED = 0x82
AMK_COLOR = 0x83
AMK_PROBE_IDS = (AMK_VAL, AMK_EFFECT, AMK_SPEED, AMK_COLOR)
AMK_EFFECT_MAX = 45
AMK_SPEED_MAX = 3

# ---------------------------------------------------------------------------
# AMK 扩展协议（Matrix 官方，0xFD 前缀，同为 32 字节报文）
#
# 来源：Matrix 官方 Vial Web 的 amk/protocol.py（已实测逐条核对）。
# 它把灯光分成四个**互相独立**的类型，各有自己的灯效与参数：
#
#     RGB_TYPE_MATRIX    = 0   轴灯
#     RGB_TYPE_STRIP     = 1   配件灯（灯条）
#     RGB_TYPE_INDICATOR = 2   指示灯（CapsLock 等）
#     RGB_TYPE_GRID      = 3   点阵屏
#
# 这就是「配件灯跟着轴灯一起亮，但有独立灯效」的实现方式 —— 两者在
# 固件里是两套独立的状态，只是一起被打到同一个 LED 数组上。
# ---------------------------------------------------------------------------
AMK_PREFIX = 0xFD
AMK_OK = 0xAA

AMK_GET_VERSION = 0
AMK_GET_RGB_STRIP_COUNT = 27
AMK_GET_RGB_STRIP_INFO = 28
AMK_GET_RGB_STRIP_LED = 29
AMK_SET_RGB_STRIP_LED = 30
AMK_GET_RGB_STRIP_MODE = 31
AMK_SET_RGB_STRIP_MODE = 32
AMK_GET_RGB_INDICATOR_LED = 33
AMK_SET_RGB_INDICATOR_LED = 34
AMK_GET_RGB_MATRIX_INFO = 43
AMK_GET_RGB_MATRIX_ROW_INFO = 44
AMK_GET_RGB_MATRIX_MODE = 45
AMK_SET_RGB_MATRIX_MODE = 46
AMK_GET_RGB_MATRIX_LED = 47
AMK_SET_RGB_MATRIX_LED = 48
AMK_GET_RGB_DATA = 60
AMK_GET_RGB_PARAM = 61
AMK_SET_RGB_PARAM = 62
AMK_GET_RGB_GRID_COUNT = 65
AMK_GET_RGB_GRID_INFO = 66
AMK_GET_RGB_GRID_MODE = 67
AMK_SET_RGB_GRID_MODE = 68

#: 灯光类型
RGB_TYPE_MATRIX = 0
RGB_TYPE_STRIP = 1
RGB_TYPE_INDICATOR = 2
RGB_TYPE_GRID = 3

#: 灯条 / 点阵的可调参数
RGB_PARAM_COLOR = 0
RGB_PARAM_HSV = 1
RGB_PARAM_SPEED = 2
RGB_PARAM_SYNC = 3
RGB_PARAM_BRIGHT = 4
RGB_PARAM_USE_CUSTOM_COLOR = 5

#: 指示灯编号
RGB_LED_NUM_LOCK = 0
RGB_LED_CAPS_LOCK = 1
RGB_LED_SCROLL_LOCK = 2
RGB_LED_COMPOSE = 3
RGB_LED_KANA = 4

INDICATOR_LABELS = {
    "num_lock": RGB_LED_NUM_LOCK,
    "caps_lock": RGB_LED_CAPS_LOCK,
    "scroll_lock": RGB_LED_SCROLL_LOCK,
    "compose": RGB_LED_COMPOSE,
    "kana": RGB_LED_KANA,
}

# VIA 标准照明 value id（协议 <= 10）
VIA_BACKLIGHT_BRIGHTNESS = 0x01
VIA_BACKLIGHT_EFFECT = 0x02
VIA_BRIGHTNESS = 0x09
VIA_EFFECT = 0x0A
VIA_EFFECT_SPEED = 0x0B
VIA_COLOR = 0x0C

FASTSET_PER_PACKET = 9


class VialError(Exception):
    pass


class Led(object):
    """VialRGB 的一颗灯。

    ``index`` 是**列表位置**（0 起连续）。AMK 后端没有 VialRGB 的 LED 表，
    造的 :meth:`VialDevice.amk_leds` 会把固件的真实全局灯号放在
    ``global_index`` 里；VialRGB 后端两者相同。
    """

    __slots__ = ("index", "x", "y", "flags", "row", "col", "keycode",
                 "h", "s", "v", "global_index")

    def __init__(self, index, x, y, flags, row, col, keycode=None):
        self.index = index
        self.x = x
        self.y = y
        self.flags = flags
        self.row = row
        self.col = col
        self.keycode = keycode
        self.h = 0
        self.s = 0
        self.v = 0
        self.global_index = index

    @property
    def is_matrix(self):
        """是键位灯（占据矩阵某个 row/col）。"""
        return self.row is not None and self.col is not None

    @property
    def zone(self):
        """所属分区：``"key"`` = 轴灯（键位灯），``"acc"`` = 配件灯（灯条/底灯/氛围灯）。"""
        return "key" if self.is_matrix else "acc"

    def __repr__(self):
        return "<Led %d xy=(%d,%d) matrix=(%s,%s)>" % (self.index, self.x, self.y, self.row, self.col)


#: 分区元信息：key = 轴灯，acc = 配件灯
ZONES = ("key", "acc")

ZONE_LABELS = {
    "key": "轴灯",
    "acc": "配件灯",
}


class AmkStripLed(object):
    """一颗配件灯 / 点阵灯。

    ``param`` 是一个打包的位域，不是「亮度」：

        bit 0     on        是否点亮
        bit 1     dynamic   动态效果
        bit 2     blink     闪烁
        bit 3     breath    呼吸
        bit 4-7   speed     速度 0-15

    单个灯条也可以整体切到 ``mode`` 灯效（见 :data:`effects.STRIP_EFFECTS`），
    此时 ``on/dynamic/blink/breath`` 这些逐灯开关只在 ``mode == Custom``
    时才是用户可调的。
    """

    __slots__ = ("index", "hue", "sat", "val", "param")

    def __init__(self, index, hue, sat, val, param):
        self.index = index
        self.hue = hue & 0xFF
        self.sat = sat & 0xFF
        self.val = val & 0xFF
        self.param = param & 0xFF

    # --- 位域读写 ---------------------------------------------------
    @property
    def on(self):
        return self.param & 0x01

    @property
    def dynamic(self):
        return (self.param >> 1) & 0x01

    @property
    def blink(self):
        return (self.param >> 2) & 0x01

    @property
    def breath(self):
        return (self.param >> 3) & 0x01

    @property
    def speed(self):
        """逐灯速度 0-15。"""
        return (self.param >> 4) & 0x0F

    def with_flags(self, on=None, dynamic=None, blink=None, breath=None, speed=None):
        """返回一个改了若干位域的新实例（不改自身）。"""
        p = self.param
        if on is not None:
            p = (p | 0x01) if on else (p & ~0x01)
        if dynamic is not None:
            p = (p | 0x02) if dynamic else (p & ~0x02)
        if blink is not None:
            p = (p | 0x04) if blink else (p & ~0x04)
        if breath is not None:
            p = (p | 0x08) if breath else (p & ~0x08)
        if speed is not None:
            p = (p & 0x0F) | ((int(speed) & 0x0F) << 4)
        return AmkStripLed(self.index, self.hue, self.sat, self.val, p)

    def with_hsv(self, hue=None, sat=None, val=None):
        return AmkStripLed(
            self.index,
            self.hue if hue is None else hue,
            self.sat if sat is None else sat,
            self.val if val is None else val,
            self.param,
        )

    def __repr__(self):
        return "<AmkStripLed %d hsv=(%d,%d,%d) %s>" % (
            self.index, self.hue, self.sat, self.val, self.flag_text())

    def flag_text(self):
        bits = []
        bits.append("on" if self.on else "off")
        if self.dynamic:
            bits.append("动态")
        if self.blink:
            bits.append("闪烁")
        if self.breath:
            bits.append("呼吸")
        bits.append("速度%d" % self.speed)
        return "/".join(bits)


class AmkStrip(object):
    """一条配件灯（灯条）。

    一把键盘可以有多条，例如 Faukwaa 有 5 条：``start`` 是它在**全局灯
    数组**里的起始灯号（轴灯从 0 开始，配件灯紧接其后），``count`` 是灯数。
    ``mode`` 是这条灯条当前的整体灯效编号。
    """

    __slots__ = ("index", "config", "start", "count", "mode", "custom",
                 "hue", "sat", "val", "speed", "sync", "bright",
                 "use_custom_color", "leds", "name")

    def __init__(self, index, config=0, start=0, count=0, mode=0, custom=0):
        self.index = index
        self.config = config
        self.start = start
        self.count = count
        self.mode = mode
        self.custom = custom
        self.hue = 0
        self.sat = 0
        self.val = 0
        self.speed = 0
        self.sync = 0xFF
        self.bright = 255
        self.use_custom_color = False
        self.leds = {}
        self.name = "灯条 #%d" % (index + 1)

    @property
    def indices(self):
        return list(range(self.start, self.start + self.count))

    def __repr__(self):
        return "<AmkStrip %d start=%d count=%d mode=%s>" % (
            self.index, self.start, self.count, self.mode)


def classify_leds(leds):
    """把 LED 列表分成 ``{"key": [...], "acc": [...]}``。"""
    out = {"key": [], "acc": []}
    for led in leds:
        out[led.zone].append(led)
    return out


def zone_indices(leds, zones):
    """返回属于 ``zones`` 这组的 LED 索引列表。顺序与 ``leds`` 一致。"""
    want = set(zones)
    return [led.index for led in leds if led.zone in want]


class LightState(object):
    """当前照明状态。"""

    def __init__(self, backend=None, effect=0, speed=0, hue=0, sat=0, val=0, brightness_max=255):
        self.backend = backend
        self.effect = effect
        self.speed = speed
        self.hue = hue
        self.sat = sat
        self.val = val
        self.brightness_max = brightness_max

    def as_dict(self):
        return dict(
            backend=self.backend,
            effect=self.effect,
            speed=self.speed,
            hue=self.hue,
            sat=self.sat,
            val=self.val,
            brightness_max=self.brightness_max,
        )

    def __repr__(self):
        return "<LightState %s effect=%s speed=%s hsv=(%s,%s,%s)>" % (
            self.backend,
            self.effect,
            self.speed,
            self.hue,
            self.sat,
            self.val,
        )


class VialDevice(object):
    """一个 Vial / VIA 键盘的会话。"""

    def __init__(self, info):
        self.info = info
        self._dev = None
        self._definition = None
        self._leds = None
        self._strips = None
        self._amk_leds = None
        self._keycodes = {}

        self.via_protocol = None
        self.vial_protocol = None
        self.keyboard_uid = None
        self.vialrgb_flag = 0
        self.rgb_protocol = None
        self.rgb_max_brightness = 255
        self.supported_effects = []
        self.num_leds = 0
        self.lighting_backend = None
        self.methods_tried = []
        #: AMK 扩展协议是否用 2 字节的灯索引（v2）。默认按 Faukwaa 实机取 False。
        self._amk_protocol_v2 = False
        self.amk_protocol_version = None
        #: ``SET_RGB_PARAM`` 是否可用（真机 Faukwaa 为 False）；None = 还没探过
        self._rgb_param_ok = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @classmethod
    def discover(cls, only_vial=True):
        """找出所有 QMK raw HID 接口；``only_vial`` 时只保留 Vial 固件。"""
        found = []
        for info in enumerate_devices():
            if not info.is_via_raw:
                continue
            if only_vial and (info.serial or "") != VIAL_SERIAL_MAGIC:
                continue
            found.append(info)
        return found

    @classmethod
    def discover_all_raw(cls):
        return [i for i in enumerate_devices() if i.is_via_raw]

    def open(self):
        self._dev = open_device(self.info)
        return self

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        self._leds = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    # 底层收发
    # ------------------------------------------------------------------
    def _send(self, payload, retries=4, timeout_ms=500):
        if self._dev is None:
            raise VialError("设备未打开")
        payload = bytes(payload)
        if len(payload) > MSG_LEN:
            raise VialError("报文不能超过 %d 字节" % MSG_LEN)
        buf = payload + b"\x00" * (MSG_LEN - len(payload))
        last = None
        for attempt in range(retries):
            try:
                n = self._dev.write(b"\x00" + buf)
                if n != MSG_LEN + 1:
                    last = "写入长度异常: %r" % (n,)
                    time.sleep(0.1)
                    continue
                data = self._dev.read(MSG_LEN, timeout_ms=timeout_ms)
                if data:
                    return bytes(data)
                last = "响应为空"
            except OSError as exc:
                last = repr(exc)
            time.sleep(0.1)
        raise VialError("与键盘通信失败: %s" % last)

    # ------------------------------------------------------------------
    # 版本 / 信息
    # ------------------------------------------------------------------
    def read_versions(self):
        data = self._send(b"\x01")
        if len(data) >= 3 and data[0] == CMD_GET_PROTOCOL_VERSION:
            self.via_protocol = (data[1] << 8) | data[2]
        data = self._send(b"\xfe" + bytes(bytearray([VIAL_GET_KEYBOARD_ID])))
        if len(data) >= 13:
            self.vial_protocol, self.keyboard_uid, self.vialrgb_flag = struct.unpack("<IQB", data[0:13])
        return self.via_protocol, self.vial_protocol

    def get_keyboard_value(self, sub_id):
        data = self._send(struct.pack("BB", CMD_GET_KEYBOARD_VALUE, sub_id))
        return bytes(data[2:])

    def uptime_ms(self):
        raw = self.get_keyboard_value(0x01)
        if len(raw) >= 4:
            return struct.unpack("<I", raw[:4])[0]
        return None

    def firmware_version(self):
        raw = self.get_keyboard_value(0x04)
        if len(raw) >= 2:
            return (raw[0] << 8) | raw[1]
        return None

    # ------------------------------------------------------------------
    # 键盘定义（内嵌的 vial.json，XZ 压缩）
    # ------------------------------------------------------------------
    def read_definition(self, force=False):
        if self._definition is not None and not force:
            return self._definition
        size = struct.unpack("<I", self._send(b"\xfe" + bytes(bytearray([VIAL_GET_SIZE])))[:4])[0]
        if size <= 0 or size > 512 * 1024:
            raise VialError("键盘定义的尺寸不合理: %s" % size)
        blob = bytearray()
        pages = (size + MSG_LEN - 1) // MSG_LEN
        for page in range(pages):
            blob += self._send(struct.pack("<BBH", 0xFE, VIAL_GET_DEF, page))
        blob = bytes(blob[:size])
        if blob[:1] == b"\xfd":
            try:
                blob = lzma.decompress(blob)
            except Exception as exc:
                raise VialError("键盘定义解压失败: %s" % exc)
        try:
            self._definition = json.loads(blob.decode("utf-8"))
        except Exception as exc:
            raise VialError("键盘定义不是合法 JSON: %s" % exc)
        return self._definition

    # ------------------------------------------------------------------
    # 照明后端探测
    # ------------------------------------------------------------------
    def detect_lighting(self):
        """探测可用照明后端，返回 ``'vialrgb' | 'amk' | 'via' | None``。"""
        self.methods_tried = []

        if self.vial_protocol is None:
            self.read_versions()

        # 1) VialRGB —— 固件标志位是权威依据
        if self.vialrgb_flag & 1:
            try:
                info = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIALRGB_GET_INFO))
                version = info[2] | (info[3] << 8)
                self.rgb_protocol = version
                self.rgb_max_brightness = info[4] if len(info) > 4 else 255
                self.methods_tried.append(("vialrgb", "OK protocol=%s" % version))
                if version == 1:
                    self.supported_effects = self._vialrgb_supported()
                    self.num_leds = self._vialrgb_led_count()
                    self.lighting_backend = "vialrgb"
                    return "vialrgb"
            except VialError as exc:
                self.methods_tried.append(("vialrgb", "失败: %s" % exc))
        else:
            self.methods_tried.append(("vialrgb", "固件未编译 VIALRGB (标志=%d)" % self.vialrgb_flag))

        # 2) AMK 私有通道
        try:
            probe = {}
            for vid in AMK_PROBE_IDS:
                probe[vid] = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, vid))[2:8]
            if any(any(b != 0 for b in probe[vid]) for vid in AMK_PROBE_IDS):
                self.lighting_backend = "amk"
                self.methods_tried.append(("amk", "OK 探测值=%s" % {hex(k): list(v[:2]) for k, v in probe.items()}))
                # 顺带探一次 0xFD 扩展协议：配件灯 / 点阵屏都在那里
                try:
                    self.amk_protocol_version = self.amk_version()
                    n = self.strip_count()
                    self.methods_tried.append(
                        ("amk-ext", "版本=%s，配件灯条 %d 组"
                         % (self.amk_protocol_version, n)))
                except VialError as exc:
                    self.amk_protocol_version = None
                    self.methods_tried.append(("amk-ext", "不可用: %s" % exc))
                return "amk"
            self.methods_tried.append(("amk", "全部返回 0"))
        except VialError as exc:
            self.methods_tried.append(("amk", "失败: %s" % exc))

        # 3) VIA 标准照明通道
        try:
            effect = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIA_EFFECT))
            if effect[0] == CMD_LIGHTING_GET_VALUE and any(b != 0 for b in effect[2:8]):
                self.lighting_backend = "via"
                self.methods_tried.append(("via", "OK"))
                return "via"
            self.methods_tried.append(("via", "无有效数据"))
        except VialError as exc:
            self.methods_tried.append(("via", "失败: %s" % exc))

        self.lighting_backend = None
        return None

    # ------------------------------------------------------------------
    # AMK 扩展协议（轴灯 / 配件灯 / 点阵屏 的独立控制）
    # ------------------------------------------------------------------
    def amk_request(self, cmd, *payload):
        """发一个 0xFD 扩展命令，返回完整响应。

        响应布局统一是 ``[0]=0xFD [1]=cmd [2]=OK|失败码 [3..]=载荷``。
        """
        msg = struct.pack("BB", AMK_PREFIX, cmd) + bytes(bytearray(payload))
        data = self._send(msg)
        if data[0] != AMK_PREFIX or data[1] != cmd:
            raise VialError("AMK 响应不匹配: %r" % (bytes(data[:4]),))
        return bytes(data)

    @staticmethod
    def amk_ok(data):
        return len(data) > 2 and data[2] == AMK_OK

    def amk_version(self):
        """AMK 扩展协议版本；固件不支持时返回 ``None``。"""
        try:
            data = self.amk_request(AMK_GET_VERSION)
        except VialError:
            return None
        if not self.amk_ok(data):
            return None
        return data[3]

    # --- 配件灯（灯条） -------------------------------------------------
    def strip_count(self):
        data = self.amk_request(AMK_GET_RGB_STRIP_COUNT)
        return data[3] if self.amk_ok(data) else 0

    def read_strips(self):
        """读全部灯条的结构（起始灯号 / 灯数 / 当前灯效）。

        ``GET_RGB_STRIP_INFO(28)`` 的响应布局（真机实测，协议 v1）：

            [0]=0xFD [1]=28 [2]=0xAA [3]=灯条 index
            [4]=1 起的序号（第几条，**不是** config）
            [5]=start（灯条首灯在**全局灯号**里的位置）
            [6]=count（这条几颗灯）

        协议 v2 时 ``start``/``count`` 各占 2 字节小端。

        注意：官方 Web 配置是从键盘定义的 ``amkFeature.rgb_strip.strips``
        里读 start/count 的，并不调这个命令；本机键盘的定义里**没有**
        ``amkFeature`` 这一段，所以必须走这条命令 —— 实测能正确读出
        5 条灯条（start=75/79/83/87/91，count=4/4/4/4/2）。
        """
        count = self.strip_count()
        strips = []
        v2 = self._amk_protocol_v2
        for i in range(count):
            data = self.amk_request(AMK_GET_RGB_STRIP_INFO, i)
            if not self.amk_ok(data):
                continue
            if v2:
                start = data[5] | (data[6] << 8)
                n = data[7] | (data[8] << 8)
            else:
                start = data[5]
                n = data[6]
            strip = AmkStrip(i, config=data[4], start=start, count=n)
            # mode 走独立命令读，GET_STRIP_INFO 不返回它
            m = self.amk_request(AMK_GET_RGB_STRIP_MODE, i)
            if self.amk_ok(m):
                strip.mode = m[4]
            strips.append(strip)
        self._strips = strips
        self._amk_leds = None      # 灯条结构变了，逻辑灯位要重建
        return strips

    def strips(self, force=False):
        if getattr(self, "_strips", None) is None or force:
            self.read_strips()
        return self._strips

    def read_strip_led(self, index):
        """读一颗配件灯，返回 :class:`AmkStripLed`。"""
        v2 = self._amk_protocol_v2
        if v2:
            data = self.amk_request(
                AMK_GET_RGB_STRIP_LED, index & 0xFF, (index >> 8) & 0xFF)
            off = 6
        else:
            data = self.amk_request(AMK_GET_RGB_STRIP_LED, index & 0xFF)
            off = 4
        if not self.amk_ok(data):
            return None
        return AmkStripLed(index, data[off], data[off + 1],
                           data[off + 2], data[off + 3])

    def set_strip_led(self, led):
        """写一颗配件灯。``led`` 是 :class:`AmkStripLed`。"""
        idx = led.index
        body = bytes(bytearray([led.hue, led.sat, led.val, led.param]))
        if self._amk_protocol_v2:
            payload = struct.pack("BBH", AMK_PREFIX, AMK_SET_RGB_STRIP_LED,
                                  idx) + body
        else:
            payload = struct.pack("BBB", AMK_PREFIX, AMK_SET_RGB_STRIP_LED,
                                  idx) + body
        data = self._send(payload)
        return self.amk_ok(data)

    def set_strip_color(self, strip, hue, sat, val, on=True, force=False):
        """把整条灯条刷成同一个颜色（逐灯写）。

        **真机实测的关键约束**：固件只有**逐灯**写色这一条通道
        （``SET_RGB_STRIP_LED(30)``），而且**只有在 ``Custom``（0）档灯效下
        才会采用它**。所以：

        * 想让这个颜色真的显示出来，必须先把灯条切到 ``Custom`` 档 ——
          ``force=True`` 时会自动帮你切。
        * ``val`` 就是亮度（0-255），直接写进每颗灯的 HSV 的 V 分量；
          ``val == 0`` 等价于关灯。这也是本固件上**唯一**能调配件灯亮度的
          办法（灯条级 ``RGB_PARAM_BRIGHT`` 在本机返回 ``0x55``，不可用）。
        """
        s = strip if isinstance(strip, AmkStrip) else self.strips()[strip]
        if force and s.mode != effects.STRIP_EFFECT_CUSTOM:
            self.set_strip_mode(s, effects.STRIP_EFFECT_CUSTOM)
        ok = True
        for i in range(s.count):
            idx = s.start + i
            led = self.read_strip_led(idx)
            if led is None:
                led = AmkStripLed(idx, hue, sat, val, 0)
            led = led.with_hsv(hue, sat, val).with_flags(on=1 if on else 0)
            ok = self.set_strip_led(led) and ok
            s.leds[idx] = led
        s.hue, s.sat, s.val = hue, sat, val
        return ok

    def set_strip_led_speed(self, strip, speed):
        """改整条灯条**逐灯**速度（0-15，4 bit 位域）。

        同样只在 ``Custom`` 档下有意义；非 Custom 档的速度由固件自己控制。
        """
        s = strip if isinstance(strip, AmkStrip) else self.strips()[strip]
        speed = max(0, min(effects.STRIP_LED_SPEED_MAX, int(speed)))
        ok = True
        for i in range(s.count):
            idx = s.start + i
            led = self.read_strip_led(idx)
            if led is None:
                led = AmkStripLed(idx, s.hue, s.sat, s.val, 0)
            led = led.with_flags(speed=speed)
            ok = self.set_strip_led(led) and ok
            s.leds[idx] = led
        s.speed = speed
        return ok

    def set_strip_mode(self, strip, mode):
        """切换一条灯条的整体灯效。

        真机实测：固件对任意值都回 ``0xAA``，但**对 10 取模**
        （写 10 读回 0、写 255 读回 5），所以有效范围是 0-9。
        """
        s = strip if isinstance(strip, AmkStrip) else self.strips()[strip]
        mode = int(mode) % 10
        data = self.amk_request(AMK_SET_RGB_STRIP_MODE, s.index, mode)
        if self.amk_ok(data):
            s.mode = mode
        return self.amk_ok(data)

    def amk_param(self, rgb_type, param, index=0):
        """读一个灯区参数。返回 ``(r, g, b)`` / 整数 / 布尔。"""
        data = self.amk_request(AMK_GET_RGB_PARAM, rgb_type, param, index)
        if not self.amk_ok(data):
            return None
        if param == RGB_PARAM_COLOR:
            return (data[4], data[5], data[6])
        if param == RGB_PARAM_HSV:
            return (data[4], data[5], data[6])
        return data[4]

    def set_amk_param(self, rgb_type, param, values, index=0):
        """写一个灯区参数（灯条级 / 轴灯级）。

        **真机实测结论（Faukwaa，2026-09，逐条扫过 0xFD 命令表）**：

        * ``SET_RGB_PARAM(62)`` 对**任何** ``rgb_type`` / ``param`` 组合都回
          ``0x55``（拒绝），并且把请求原样回显。也就是说本机固件
          **根本没有实现灯区参数通道**。
        * ``GET_RGB_PARAM(61)`` 与 ``GET_RGB_DATA(60)`` 同样返回 ``0x55``。
        * 本机实现了的 0xFD 命令只有：
          ``0``(版本) / ``7-14`` / ``27-34``(STRIP + INDICATOR) /
          ``43-48``(MATRIX)。

        所以官方 Web 配置里那条「非 Custom 模式走
        ``apply_rgb_param(RGB_TYPE_STRIP, RGB_PARAM_COLOR/SPEED/BRIGHT)``」
        的路径在本机**完全走不通**，这是固件差异，不是调用姿势问题。

        返回值恒为 ``False`` 时，调用方应当退回逐灯写
        （见 :meth:`set_strip_color`）。
        """
        if not isinstance(values, (tuple, list)):
            values = (values,)
        payload = [rgb_type, param, index] + [v & 0xFF for v in values]
        data = self.amk_request(AMK_SET_RGB_PARAM, *payload)
        return self.amk_ok(data)

    def rgb_param_supported(self, force=False):
        """本机固件是否支持灯区级参数通道（``SET_RGB_PARAM``）。

        结果缓存；探测一次即可。返回 ``False`` 表示必须走逐灯写。
        """
        if getattr(self, "_rgb_param_ok", None) is not None and not force:
            return self._rgb_param_ok
        try:
            ok = self.set_amk_param(RGB_TYPE_STRIP, RGB_PARAM_COLOR,
                                    (255, 255, 255), 0)
        except VialError:
            ok = False
        self._rgb_param_ok = bool(ok)
        return self._rgb_param_ok

    # --- 指示灯 ---------------------------------------------------------
    def read_indicator(self, which):
        """``which`` 是 ``"caps_lock"`` 之类，返回 :class:`AmkStripLed`。"""
        idx = INDICATOR_LABELS.get(which)
        if idx is None:
            return None
        data = self.amk_request(AMK_GET_RGB_INDICATOR_LED, idx)
        if not self.amk_ok(data):
            return None
        return AmkStripLed(idx, data[4], data[5], data[6], data[7])

    def set_indicator(self, which, led):
        idx = INDICATOR_LABELS.get(which)
        if idx is None:
            return False
        body = bytes(bytearray([led.hue, led.sat, led.val, led.param]))
        payload = struct.pack("BBB", AMK_PREFIX, AMK_SET_RGB_INDICATOR_LED,
                              idx) + body
        return self.amk_ok(self._send(payload))

    def original_colors(self):
        """读出**原始**灯色，作为「恢复出厂」的基线。

        返回 ``{"strips": {strip_index: [(idx, h, s, v, param), ...]},
                 "indicators": {...}}``。读不出来时对应项为 ``None``。
        """
        out = {"strips": {}, "indicators": {}}
        for s in self.strips():
            rows = []
            for i in range(s.count):
                led = self.read_strip_led(s.start + i)
                if led is not None:
                    rows.append((led.index, led.hue, led.sat, led.val,
                                 led.param))
            out["strips"][s.index] = rows
        for which in INDICATOR_LABELS:
            led = self.read_indicator(which)
            out["indicators"][which] = (
                None if led is None
                else (led.index, led.hue, led.sat, led.val, led.param))
        return out

    # ------------------------------------------------------------------
    # 统一读 / 写
    # ------------------------------------------------------------------
    def read_lighting(self):
        backend = self.lighting_backend
        if backend == "amk":
            val = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, AMK_VAL))[2]
            eff = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, AMK_EFFECT))[2]
            spd = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, AMK_SPEED))[2]
            col = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, AMK_COLOR))
            return LightState("amk", eff, spd, col[2], col[3], val, 255)
        if backend == "vialrgb":
            data = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIALRGB_GET_MODE))
            return LightState(
                "vialrgb",
                data[2] | (data[3] << 8),
                data[4],
                data[5],
                data[6],
                data[7],
                self.rgb_max_brightness,
            )
        if backend == "via":
            val = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIA_BRIGHTNESS))[2]
            eff = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIA_EFFECT))[2]
            spd = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIA_EFFECT_SPEED))[2]
            col = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIA_COLOR))
            return LightState("via", eff, spd, col[2], col[3], val, 255)
        raise VialError("没有可用的照明后端")

    def set_brightness(self, val):
        val = max(0, min(255, int(val)))
        if self.lighting_backend == "amk":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_VAL, val))
        elif self.lighting_backend == "vialrgb":
            cur = self.read_lighting()
            self.set_mode(cur.effect, cur.speed, cur.hue, cur.sat, val)
        elif self.lighting_backend == "via":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_BRIGHTNESS, val))
        else:
            raise VialError("没有可用的照明后端")
        return val

    def set_effect(self, eff):
        eff = max(0, int(eff))
        if self.lighting_backend == "amk":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_EFFECT, eff))
        elif self.lighting_backend == "vialrgb":
            cur = self.read_lighting()
            self.set_mode(eff, cur.speed, cur.hue, cur.sat, cur.val)
        elif self.lighting_backend == "via":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_EFFECT, eff))
        else:
            raise VialError("没有可用的照明后端")
        return eff

    def set_speed(self, spd):
        spd = max(0, int(spd))
        if self.lighting_backend == "amk":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_SPEED, spd))
        elif self.lighting_backend == "vialrgb":
            cur = self.read_lighting()
            self.set_mode(cur.effect, spd, cur.hue, cur.sat, cur.val)
        elif self.lighting_backend == "via":
            self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_EFFECT_SPEED, spd))
        else:
            raise VialError("没有可用的照明后端")
        return spd

    def set_color(self, hue, sat, val=None):
        hue = max(0, min(255, int(hue)))
        sat = max(0, min(255, int(sat)))
        if self.lighting_backend == "amk":
            self._send(struct.pack("BBBB", CMD_LIGHTING_SET_VALUE, AMK_COLOR, hue, sat))
            if val is not None:
                self.set_brightness(val)
        elif self.lighting_backend == "vialrgb":
            cur = self.read_lighting()
            self.set_mode(cur.effect, cur.speed, hue, sat, cur.val if val is None else val)
        elif self.lighting_backend == "via":
            self._send(struct.pack("BBBB", CMD_LIGHTING_SET_VALUE, VIA_COLOR, hue, sat))
            if val is not None:
                self.set_brightness(val)
        else:
            raise VialError("没有可用的照明后端")
        return hue, sat

    def set_all(self, effect=None, speed=None, hue=None, sat=None, val=None):
        """一次性写入多个字段（尽可能合并为最少的报文）。"""
        if self.lighting_backend is None:
            raise VialError("没有可用的照明后端")
        cur = self.read_lighting()
        eff = cur.effect if effect is None else int(effect)
        spd = cur.speed if speed is None else int(speed)
        h = cur.hue if hue is None else int(hue)
        s = cur.sat if sat is None else int(sat)
        v = cur.val if val is None else int(val)
        if self.lighting_backend == "amk":
            if effect is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_EFFECT, eff))
            if speed is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_SPEED, spd))
            if hue is not None or sat is not None:
                self._send(struct.pack("BBBB", CMD_LIGHTING_SET_VALUE, AMK_COLOR, h, s))
            if val is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, AMK_VAL, v))
        elif self.lighting_backend == "vialrgb":
            self.set_mode(eff, spd, h, s, v)
        elif self.lighting_backend == "via":
            if effect is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_EFFECT, eff))
            if speed is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_EFFECT_SPEED, spd))
            if hue is not None or sat is not None:
                self._send(struct.pack("BBBB", CMD_LIGHTING_SET_VALUE, VIA_COLOR, h, s))
            if val is not None:
                self._send(struct.pack("BBB", CMD_LIGHTING_SET_VALUE, VIA_BRIGHTNESS, v))
        return LightState(self.lighting_backend, eff, spd, h, s, v, cur.brightness_max)

    def save(self):
        """请求固件把当前照明配置写入持久存储。"""
        data = self._send(struct.pack("BB", CMD_LIGHTING_SAVE, 0x00))
        ok = data[0] == CMD_LIGHTING_SAVE
        return ok, data

    # ------------------------------------------------------------------
    # 轴灯逐灯通道的**真实边界**（2026-09 真机逐颗扫过地址空间后重写）
    # ------------------------------------------------------------------
    #
    # 更正一段早期错误结论。之前以为「固件整组写色漏掉了全局 0 和 74
    # 两颗真实轴灯，把左边几个键（Tab/Caps/Shift/Ctrl）留在旧色上」。
    # 逐颗打断测试（把单颗灯写成纯蓝，其余全红，让用户肉眼指认）证明
    # 这个判断是**错的**：
    #
    #   * **全局 0**：读写都成功（能回读刚写的色），但**物理上没有
    #     任何一颗灯响应** —— 是一颗真实的**幽灵灯**（影子寄存器）。
    #   * **全局 74**：也不是轴灯，而是 **Caps Lock 指示灯**。
    #     键盘定义里写得很清楚：``"indicator": {"caps_lock":
    #     {"index": 74}}``；AMK 矩阵通道读它返回
    #     ``[253,47,170,74, 0,255,255,0x81]``（红、亮）。
    #
    # 而**键位轴灯**（物理上的 74 颗轴灯）根本不在 ``SET_RGB_STRIP_LED``
    # 这条逐灯通道上控制：该通道对键位轴灯只写进影子缓冲，不驱动灯。
    # 轴灯由**板载灯效引擎**驱动（``AMK_EFFECT``，见 ``set_effect``）；
    # 逐灯的矩阵通道 ``GET/SET_RGB_MATRIX_LED(47/48)`` 虽然**存在且可读写**，
    # 但键位轴的 ``h/s/v`` 恒为 0，说明固件并没有为「逐键上色」维护
    # 正经的每键颜色表。
    #
    # 结论：**本机固件无法逐键控制轴灯颜色**。「左边几个键颜色不对」是
    # 固件灯效引擎自己的渲染结果，不是我们写漏了灯 —— 不要再尝试"补灯"。
    def axis_led_range(self):
        """AMK 逐灯通道里**真正驱动轴灯**的全局灯号区间。

        真机实测：这条区间**是空的** —— 逐灯通道不驱动任何键位轴灯。

        保留该方法是为了兼容旧调用方与「幽灵灯」诊断；返回值恒为
        ``(0, -1)``（空区间），让 ``axis_stray_indexes()`` 恒返回 ``[]``。
        """
        return 0, -1

    def ghost_led_indexes(self):
        """在逐灯通道上**可读写但不驱动任何物理灯**的幽灵灯号。

        真机实测（Faukwaa）：全局 ``0`` 写什么都能回读，但板上无灯响应。
        这类索引应当从任何「逐灯上色」的灯表里**排除**，否则会把
        ``num_key_leds() - 1`` 算错一位。
        """
        return [0]

    def indicator_indexes(self):
        """充当键盘指示灯（而非轴灯）的全局灯号。

        键盘定义 ``indicator`` 段给出映射；本机是
        ``{"caps_lock": {"index": 74}}``。这些灯号属于指示灯通道
        （``AMK_SET_RGB_INDICATOR_LED``），**不属于轴灯**。
        """
        try:
            ind = (self.read_definition() or {}).get("indicator") or {}
        except Exception:
            ind = {}
        out = []
        for meta in ind.values():
            if isinstance(meta, dict) and "index" in meta:
                try:
                    out.append(int(meta["index"]))
                except (TypeError, ValueError):
                    pass
        return sorted(set(out))

    def axis_stray_indexes(self):
        """整组写色"漏掉"的轴灯灯号。

        **恒返回 ``[]``** —— 早期以为漏了 ``[0, 74]``，逐颗打断测试证明
        那是误判（0 是幽灵灯、74 是指示灯，都不是轴灯），详见本节注释。
        保留方法是为了让 GUI / CLI 的旧调用点继续正常工作，不再报假警报。
        """
        return []

    def sync_axis_stray_leds(self, hue=None, sat=None, val=None, speed=8):
        """旧接口：补齐"漏掉的轴灯"。现在恒为空操作，返回 ``[]``。

        见 :meth:`axis_stray_indexes` 的说明 —— 本机固件不存在"漏灯"，
        轴灯颜色由板载灯效引擎渲染，无法逐键干预。
        """
        return []

    # --- 轴灯矩阵通道（命令 43-48）-------------------------------------
    def matrix_info(self, force=False):
        """``GET_RGB_MATRIX_INFO(43)`` -> ``{"missing": ?, "count": n}``。

        真机响应 ``[253,43,170, 0, 74, 0...]``：``data[4]=74`` 就是轴灯颗数。

        ``missing``（start 偏移）与 ``count``（灯数）是**设备固有常量**，
        结果缓存到 ``self._matrix_info`` —— 否则逐键上色时每颗灯都会
        多打一次往返（68 颗就是 68 次），非常浪费。
        """
        if not force and getattr(self, "_matrix_info", None) is not None:
            return self._matrix_info
        data = self.amk_request(AMK_GET_RGB_MATRIX_INFO)
        if not self.amk_ok(data):
            return None
        self._matrix_info = {"missing": data[3], "count": data[4]}
        return self._matrix_info

    def matrix_row(self, row):
        """``GET_RGB_MATRIX_ROW_INFO(44, 0, row)`` -> ``{(row,col): led_index}``。

        **官方解码**（`reference/matrix-vialweb/amk/protocol.py:1185`）：

            usb_send(0xFD, 44, 0, i)         # 载荷是 [0, row]
            for j in range(cols):
                data[(i, j)] = data[4 + j]   # 0xFF = 该位无灯

        真机 ``matrix.rows=5 / cols=16``，所以每行读 16 个字节，
        ``0xFF`` 表示这一格没有灯。
        """
        # 行/列数：定义里的 matrix.rows / cols；读不到就按 5 / 16 兜底
        try:
            rows = int(((self.read_definition() or {}).get("matrix") or {})
                       .get("rows") or 5)
        except Exception:
            rows = 5
        try:
            cols = int(((self.read_definition() or {}).get("matrix") or {})
                       .get("cols") or 16)
        except Exception:
            cols = 16

        data = self.amk_request(AMK_GET_RGB_MATRIX_ROW_INFO, 0, row)
        if not self.amk_ok(data):
            return {}
        out = {}
        for c in range(cols):
            if 4 + c >= len(data):
                break
            v = data[4 + c]
            if v != 0xFF:
                out[(row, c)] = v
        return out

    def matrix_map(self, force=False):
        """完整的 ``{led_index: (row, col)}`` 映射（本机 68 颗有键位）。

        **这是「逐键上色」的地基**：固件的 ``SET_RGB_MATRIX_LED(48)`` 吃的
        是**灯号**，而 UI 是按 (row, col) 画格子的，两者必须靠这张表对上。
        结果缓存到 ``self._matrix_map``。
        """
        if not force and getattr(self, "_matrix_map", None) is not None:
            return self._matrix_map
        inv = {}
        try:
            rows = int(((self.read_definition() or {}).get("matrix") or {})
                       .get("rows") or 5)
        except Exception:
            rows = 5
        for r in range(rows):
            for rc, idx in self.matrix_row(r).items():
                inv[idx] = rc
        self._matrix_map = inv
        return inv

    def matrix_led_at(self, row, col):
        """(row, col) -> 灯号；该格无灯时返回 ``None``。

        注意 :meth:`matrix_map` 的方向是 **``{灯号: (row, col)}``**，
        所以这里要扫值匹配，或者走缓存的逆向表。
        """
        return self.matrix_rc_map().get((row, col))

    def matrix_rc_map(self, force=False):
        """``{(row, col): 灯号}`` —— :meth:`matrix_map` 的逆向表（缓存）。"""
        if not force and getattr(self, "_matrix_rc_map", None) is not None:
            return self._matrix_rc_map
        self._matrix_rc_map = {rc: idx for idx, rc in self.matrix_map().items()}
        return self._matrix_rc_map

    def matrix_mode(self):
        """``GET_RGB_MATRIX_MODE(45)`` -> ``{current, custom, total, default}``。

        官方解码（`protocol.py:1177`）：``data[3]=current data[4]=custom
        data[5]=total data[6]=default``。

        **真机实测**：``custom = 45``、``total = 46``、``default = 13``。
        ``custom`` 就是「逐灯自定义」档 —— **只有切到它，逐键写的颜色
        才会真的显示出来**（和配件灯「只有 Custom 档吃逐灯色」是同一个道理）。
        """
        data = self.amk_request(AMK_GET_RGB_MATRIX_MODE)
        if not self.amk_ok(data):
            return None
        return {"current": data[3], "custom": data[4],
                "total": data[5], "default": data[6]}

    def matrix_custom_mode(self):
        """本机「逐灯自定义」档的编号（本机为 45）；读不到返回 ``None``。"""
        m = self.matrix_mode()
        return m.get("custom") if m else None

    def matrix_in_custom(self):
        """当前轴灯是否处于「逐灯自定义」档。"""
        m = self.matrix_mode()
        if not m:
            return False
        return m.get("current") == m.get("custom")

    def set_matrix_mode(self, mode, index=0):
        """``SET_RGB_MATRIX_MODE(46, index, mode)`` —— **双字节载荷**。

        官方写法（`reference/matrix-vialweb/amk/protocol.py:1217`）：

            struct.pack("BBBB", 0xFD, 46, index, mode)

        早期只发了一个字节（`46, mode`），固件把它当 ``index``，
        ``mode`` 默认 0 → 于是永远切不动，被我误判成「本机不响应」。
        **加上 ``index=0`` 之后实测可以正常切换**（写 45 → 回读 current=45）。
        """
        data = self.amk_request(AMK_SET_RGB_MATRIX_MODE,
                                int(index) & 0xFF, int(mode) & 0xFF)
        return self.amk_ok(data)

    def enter_matrix_custom(self):
        """把轴灯切到「逐灯自定义」档，返回是否成功。

        **这是逐键上色的前置条件** —— 非自定义档下固件自己渲染灯效，
        逐键写的 HSV 会被无视（与配件灯完全同样的规律）。
        """
        m = self.matrix_mode()
        if not m:
            return False
        if m.get("current") == m.get("custom"):
            return True
        if not self.set_matrix_mode(m.get("custom")):
            return False
        return self.matrix_in_custom()

    def matrix_default_mode(self):
        """固件默认的矩阵灯效档（本机 = 13）；读不到返回 ``None``。"""
        m = self.matrix_mode()
        return m.get("default") if m else None

    def restore_matrix_default(self):
        """把矩阵灯效模式还原到固件默认档（本机 13），返回是否成功。

        .. important::
           这是**安全收尾**：切到 ``custom`` 档（45）测完逐键上色后，
           一定要还原回默认档，否则键盘会一直停在自定义档 ——
           实机上出现过"停在自定义档后 USB 无法枚举"的情况。
        """
        d = self.matrix_default_mode()
        if d is None:
            return False
        if self.matrix_in_custom() is False:
            m = self.matrix_mode() or {}
            if m.get("current") == d:
                return True
        return self.set_matrix_mode(d)

    def clear_matrix_leds(self, first=0, last=None):
        """把 ``[first, last]`` 范围内的**矩阵轴灯**写成黑色（关灯）。

        ``last`` 默认取 :meth:`matrix_led_count` - 1（本机 73），
        **绝不越过矩阵边界** —— 指示灯与灯条不走矩阵通道，越界写会死机。
        """
        if last is None:
            n_max = self.matrix_led_count()
            last = (n_max - 1) if n_max else 73
        n = 0
        for i in range(int(first), int(last) + 1):
            if self.set_matrix_led(i, 0, 0, 0, 0x00):
                n += 1
            time.sleep(0.004)
        return n

    def read_matrix_led(self, index):
        """``GET_RGB_MATRIX_LED(47,index)`` -> :class:`AmkStripLed`。

        **注意**：非自定义档下，键位轴灯恒返回 ``h=0 s=0 v=0``（固件不维护
        逐键颜色表）；只有切到自定义档并写过之后才有值可读。

        .. note::
           早期以为 ``index == 74`` 是 Caps Lock 指示灯，**逐颗打断法已推翻**：
           74 实际点亮的是**左空格右侧的氛围灯**。指示灯定义请以
           :meth:`indicator_indexes` 为准。
        """
        data = self.amk_request(AMK_GET_RGB_MATRIX_LED, index & 0xFF)
        if not self.amk_ok(data):
            return None
        return AmkStripLed(index, data[5], data[6], data[7], data[8])

    def matrix_led_count(self):
        """矩阵轴灯的**灯号上限**（合法灯号是 ``0 .. count-1``）。本机 = 74。

        来源优先级：键盘定义 ``amk_rgb_matrix.count`` → ``matrix_info()['count']``。
        读不到返回 0（表示"不知道边界"，此时调用方应保守处理）。
        """
        try:
            n = (((self.read_definition() or {}).get("amk_rgb_matrix") or {})
                 .get("count"))
            if n:
                return int(n)
        except Exception:
            pass
        try:
            return int((self.matrix_info() or {}).get("count") or 0)
        except Exception:
            return 0

    def set_matrix_led(self, index, hue, sat, val, param=0x01):
        """``SET_RGB_MATRIX_LED(48,index,h,s,v,param)`` —— **逐键上色**。

        .. important::
           **必须在「逐灯自定义」档下才会显示出来。** 先调
           :meth:`enter_matrix_custom`（或 :meth:`perkey_set`，它会自动切），
           否则写进去的 HSV 会被固件的灯效渲染覆盖，板上毫无反应。

        .. danger::
           **只允许 ``0 <= index < matrix_led_count()``。** 本机矩阵只有
           74 颗灯（0..73），而指示灯/灯条虽然占了更大的灯号（74..92），
           但它们**不走矩阵通道**。往矩阵通道写 ``index >= 74`` 会让固件
           写穿自己的 LED 缓冲区 —— **真机实测两次导致键盘死机**
           （USB 无法枚举、按键无响应）。此方法已内置边界检查，越界直接
           返回 ``False`` 且**不发包**。

        官方解码（`protocol.py:1209`）::

            struct.pack("BBB", 0xFD, 48, start + index) + led.pack()

        其中 ``start`` 取自 ``GET_MATRIX_INFO(43)`` 的 ``data[3]``（本机 0），
        ``led.pack()`` 是 ``h, s, v, param`` 四字节。
        """
        idx = int(index)
        n = self.matrix_led_count()
        if n and not (0 <= idx < n):
            # 越界写 = 固件缓冲区溢出 = 键盘死机。宁可少写一颗也绝不越界。
            return False
        start = (self.matrix_info() or {}).get("missing") or 0
        payload = bytes(bytearray([AMK_PREFIX, AMK_SET_RGB_MATRIX_LED,
                                   (start + idx) & 0xFF,
                                   int(hue) & 0xFF,
                                   int(sat) & 0xFF, int(val) & 0xFF,
                                   int(param) & 0xFF]))
        return self.amk_ok(self._send(payload))

    def perkey_supported(self):
        """本机是否支持逐键轴灯上色（需要 AMK 矩阵通道 + 自定义档）。"""
        if self.lighting_backend != "amk":
            return False
        return self.matrix_custom_mode() is not None

    def perkey_set(self, colors, auto_custom=True):
        """**逐键上色主入口**。

        ``colors`` 是 ``{led_index: (h, s, v)}``，也可以传
        ``{(row, col): (h, s, v)}``（会自动换算成灯号）。

        ``auto_custom=True`` 时先自动切到「逐灯自定义」档 —— 这是必需的，
        非自定义档下固件自己渲染灯效，写进去的颜色会被无视。

        返回 ``(ok_count, switched)``：写成功的颗数、是否发生了切档。
        """
        if not self.perkey_supported():
            return 0, False
        switched = False
        if auto_custom and not self.matrix_in_custom():
            switched = self.enter_matrix_custom()
            if not switched:
                return 0, False

        m = self.matrix_map()
        led_by_rc = {rc: i for i, rc in m.items()}
        ok = 0
        for key, hsv in colors.items():
            idx = key
            if isinstance(key, tuple):
                idx = led_by_rc.get(key)
            if idx is None:
                continue
            h, s, v = hsv
            param = 0x01 if v > 0 else 0x00
            if self.set_matrix_led(idx, h, s, v, param):
                ok += 1
            # 节流：连续高速写会让固件/USB 状态异常（实机踩过）
            time.sleep(0.004)
        return ok, switched

    def perkey_fill(self, hue, sat, val, auto_custom=True):
        """把**所有键位**刷成同一颜色（逐键写）。返回 ``(ok, switched)``。"""
        m = self.matrix_map()
        return self.perkey_set({i: (hue, sat, val) for i in m},
                               auto_custom=auto_custom)

    def perkey_read(self):
        """读回全部键位的当前颜色 -> ``{led_index: (h,s,v,param)}``。

        非自定义档下大概率全是 0（固件不维护），这是正常的。
        """
        out = {}
        for idx in self.matrix_map():
            led = self.read_matrix_led(idx)
            if led is not None:
                out[idx] = (led.hue, led.sat, led.val, led.param)
        return out

    def effect_range(self):
        """(最小值, 最大值) —— 用于界面滑块。"""
        if self.lighting_backend == "vialrgb":
            return 0, effects.VIALRGB_MODE_MAX
        if self.lighting_backend == "amk":
            return effects.AMK_MODE_MIN, AMK_EFFECT_MAX
        return 0, 255

    def speed_range(self):
        if self.lighting_backend == "amk":
            return 0, AMK_SPEED_MAX
        return 0, 255

    # ------------------------------------------------------------------
    # 分区（轴灯 / 配件灯）能力
    # ------------------------------------------------------------------
    def zone_support(self):
        """返回 ``(zones, reason)``。

        ``zones`` 是**可以单独寻址**的分区元组，例如 ``("key", "acc")``；
        空元组表示只能整体控制。``reason`` 是给用户看的说明。

        两条通路：

        * **AMK 扩展协议**：轴灯在 ``0x80``-``0x83``，配件灯在 ``0xFD``
          的 STRIP 命令族，两者是老死不相往来的两套状态 —— 可以真正独立
          控制，甚至各跑各的灯效。
        * **VialRGB**：靠 LED 的 row/col 区分轴灯与配件灯，但只能逐灯推
          颜色，不能给两组分别设灯效。
        """
        if self.lighting_backend == "amk":
            strips = self.strips()
            n = sum(s.count for s in strips)
            if not strips:
                return (), (
                    "这把键盘的固件支持 AMK 灯光通道，但没有读配件灯条"
                    "，无法分区")
            return ("key", "acc"), (
                "轴灯走 0x80-0x83，配件灯 %d 组共 %d 颗走 AMK 扩展协议，"
                "两组可以独立设灯效" % (len(strips), n))

        if self.lighting_backend == "vialrgb":
            leds = self.vialrgb_leds()
            if not leds:
                return (), "没有读到 LED 信息，无法分区"
            groups = classify_leds(leds)
            have = tuple(z for z in ZONES if groups[z])
            if len(have) < 2:
                only = ZONE_LABELS[have[0]] if have else "灯"
                return have, "这把我只读到「%s」一种灯，无法分区联动" % only
            return have, "按 VialRGB 的 row/col 区分：有矩阵坐标的是轴灯，其余是配件灯"

        return (), (
            "%s 只能整块控制所有 LED，固件没有暴露可单独寻址的分区接口"
            % effects.backend_label(self.lighting_backend or "未知"))

    def zone_groups(self):
        """``{"key": [Led...], "acc": [Led...]}``。

        AMK 后端没有逐灯坐标，改用 :meth:`amk_leds` 造的**逻辑灯位**；
        VialRGB 后端用固件真实上报的 LED 表。
        """
        if self.lighting_backend == "amk":
            return classify_leds(self.amk_leds())
        return classify_leds(self.vialrgb_leds())

    def zone_counts(self):
        groups = self.zone_groups()
        return {z: len(groups[z]) for z in ZONES}

    def push_zoned(self, colors, zones):
        """只把 ``zones`` 里那些 LED 的颜色推下去，其余保持原样。

        ``colors`` 是按 LED 索引排列的完整 ``[(h,s,v), ...]``。

        AMK 后端不靠 VialRGB 的 LED 表，而是分两路走：轴灯用标准
        ``0x83`` 整组写色，配件灯用 ``0xFD`` 的逐灯写。所以这里要分支。
        """
        want = set(zones)
        if self.lighting_backend == "amk":
            return self._push_zoned_amk(colors, want)

        leds = self.vialrgb_leds()
        picked = [(led.index, colors[led.index]) for led in leds if led.zone in want]
        return self.vialrgb_push_pairs(picked)

    def _push_zoned_amk(self, colors, want):
        """AMK 分区推送：返回写入的灯颗数。

        ``colors`` 按 :meth:`amk_leds` 的**显示序号**索引，所以配件灯
        那条得先找到它在显示列表里的起始位置；轴灯恒为 0 号（``colors[0]``
        即可，因为轴灯只有整组写色，颜色无逐灯粒度）。

        配件灯这边**必须先把灯条切到 Custom 档**，否则固件会忽略逐灯色
        （非 Custom 档由固件自己渲染，而本机又没有灯区参数通道可改颜色）。
        """
        written = 0
        if "key" in want:
            # 轴灯只有整组写色（0x83），颜色没有逐灯粒度，取 0 号代表色
            hue, sat, val = colors[0]
            self.set_color(hue, sat, val)
            written += self.num_key_leds()
        if "acc" in want:
            # 显示序号 -> 全局灯号的映射，用来把灯条起始灯对到 colors
            disp = {}
            for led in self.amk_leds():
                disp[led.global_index] = led.index
            for s in self.strips():
                i = disp.get(s.start, 0)
                hue, sat, val = colors[i]
                self.set_strip_color(s, hue, sat, val, on=val > 0, force=True)
                written += s.count
        return written

    def num_key_leds(self):
        """轴灯的颗数（来自键盘定义的 ``amk_rgb_matrix``）。"""
        try:
            defn = self.read_definition()
        except VialError:
            return 0
        return int((defn.get("amk_rgb_matrix") or {}).get("count", 0) or 0)

    def amk_leds(self, force=False):
        """把 AMK 的轴灯 + 配件灯拼成一个 :class:`Led` 列表。

        **轴灯位置来自真实矩阵行表**（``GET_RGB_MATRIX_ROW_INFO``）：
        每个键位灯的 ``(row, col)`` 就是它在物理配列里的坐标，所以
        「逐键」页签画出来的键盘图与实际键位一一对应，可以直接点按上色。

        ``Led.index`` 是**显示序号**（0 起连续），因为 GUI 的逐灯缓冲区
        是按列表位置索引的；真实的全局灯号另存在 ``led.global_index``。
        """
        if getattr(self, "_amk_leds", None) is not None and not force:
            return self._amk_leds
        leds = []
        key_n = self.num_key_leds()

        # --- 轴灯：用真实矩阵映射摆位 ---------------------------------
        rcmap = {}
        try:
            rcmap = self.matrix_rc_map()
        except Exception:
            rcmap = {}

        if rcmap:
            # rc -> 全局灯号；按键位排序（行优先），保证图形顺序稳定
            ordered = sorted(rcmap.items(), key=lambda kv: (kv[0][0], kv[0][1]))
            for (r, c), gidx in ordered:
                led = Led(len(leds), c, r, 0, r, c)
                led.global_index = gidx
                leds.append(led)
        else:
            # 兜底：矩阵通道不可用时按固件的线性顺序平铺
            try:
                defn = self.read_definition()
                matrix = defn.get("matrix") or {}
                rows = max(int(matrix.get("rows", 1) or 1), 1)
                cols = max(int(matrix.get("cols", 1) or 1), 1)
            except VialError:
                rows, cols = 1, max(key_n, 1)
            for i in range(key_n):
                led = Led(len(leds), (i // rows) % cols, i % rows, 0,
                          i % rows, (i // rows) % cols)
                led.global_index = i
                leds.append(led)

        # --- 配件灯：排在轴灯右边，每条一列 ---------------------------
        span_cols = max([l.col for l in leds] or [0]) + 1
        x0 = span_cols + 1
        try:
            strip_list = self.strips()
        except Exception:
            strip_list = []
        for s in strip_list:
            first = self.read_strip_led(s.start)
            for i in range(s.count):
                led = Led(len(leds), x0 + s.index, i, 0, None, None)
                led.global_index = s.start + i
                if first is not None:
                    led.h, led.s, led.v = first.hue, first.sat, first.val
                leds.append(led)
        self._amk_leds = leds
        return leds

    def effect_ids(self):
        """可选灯效编号列表。"""
        if self.lighting_backend == "vialrgb":
            if self.supported_effects:
                return sorted(self.supported_effects)
            return effects.vialrgb_effect_ids()
        if self.lighting_backend == "amk":
            return effects.amk_mode_ids()
        return list(range(0, 256))

    def effect_label(self, mode_id):
        """只返回描述部分（不含编号），编号由调用方拼接。"""
        if self.lighting_backend == "vialrgb":
            if self.supported_effects and mode_id not in self.supported_effects:
                suffix = "（固件未启用）"
            else:
                suffix = ""
            en, zh = effects.vialrgb_name(mode_id)
            return "%s  %s%s" % (zh, en, suffix)
        if self.lighting_backend == "amk":
            en, zh = effects.qmk_mode_name(mode_id)
            return zh
        en, zh = effects.qmk_mode_name(mode_id)
        return zh

    # ------------------------------------------------------------------
    # VialRGB 专用：逐键
    # ------------------------------------------------------------------
    def _vialrgb_supported(self):
        supported = set([0])
        max_effect = 0
        guard = 0
        while max_effect < 0xFFFF and guard < 64:
            guard += 1
            data = self._send(struct.pack("<BBH", CMD_LIGHTING_GET_VALUE, VIALRGB_GET_SUPPORTED, max_effect))
            chunk = data[2:]
            for x in range(0, len(chunk) - 1, 2):
                value = int.from_bytes(chunk[x : x + 2], "little")
                if value != 0xFFFF:
                    supported.add(value)
                max_effect = max(max_effect, value)
        return sorted(supported)

    def _vialrgb_led_count(self):
        data = self._send(struct.pack("BB", CMD_LIGHTING_GET_VALUE, VIALRGB_GET_NUMBER_LEDS))
        return struct.unpack("<H", data[2:4])[0]

    def get_keycode(self, layer, row, col):
        key = (layer, row, col)
        if key in self._keycodes:
            return self._keycodes[key]
        data = self._send(struct.pack("BBBB", CMD_DYNAMIC_KEYMAP_GET_KEYCODE, layer, row, col))
        kc = int.from_bytes(data[4:6], "big")
        self._keycodes[key] = kc
        return kc

    def vialrgb_leds(self, force=False):
        if self._leds is not None and not force:
            return self._leds
        count = self._vialrgb_led_count() if self.num_leds <= 0 else self.num_leds
        self.num_leds = count
        leds = []
        for idx in range(count):
            data = self._send(struct.pack("<BBH", CMD_LIGHTING_GET_VALUE, VIALRGB_GET_LED_INFO, idx))
            x, y, flags, row, col = struct.unpack("BBBBB", data[2:7])
            row = None if row == 0xFF else row
            col = None if col == 0xFF else col
            kc = None
            if row is not None and col is not None:
                try:
                    kc = self.get_keycode(0, row, col)
                except VialError:
                    kc = None
            leds.append(Led(idx, x, y, flags, row, col, kc))
        self._leds = leds
        return leds

    def vialrgb_set_mode(self, mode, speed=128, hue=128, sat=128, val=128):
        self._send(
            struct.pack(
                "BBHBBBB",
                CMD_LIGHTING_SET_VALUE,
                VIALRGB_SET_MODE,
                max(0, int(mode)),
                max(0, min(255, int(speed))),
                max(0, min(255, int(hue))),
                max(0, min(255, int(sat))),
                max(0, min(255, int(val))),
            )
        )

    def vialrgb_push(self, colors):
        """``colors`` 是 ``[(h, s, v), ...]``，按 LED 索引排列。"""
        pairs = [(i, c) for i, c in enumerate(colors)]
        return self.vialrgb_push_pairs(pairs)

    def vialrgb_push_pairs(self, pairs):
        """推送任意 LED 子集。

        ``pairs`` 是 ``[(led_index, (h, s, v)), ...]``，必须**按索引升序**。
        固件允许 FASTSET 从任意 LED 开始，所以子集推送只需保证同一包里
        的 LED 连续。这里按「连续性」切包，而不是固定 9 颗一切，
        这样分区控制时每包能尽量装满。
        """
        if not pairs:
            return 0

        # 切成「连续索引段」，每段内再按 9 颗分包
        runs = []
        cur = [pairs[0]]
        for item in pairs[1:]:
            if item[0] == cur[-1][0] + 1 and len(cur) < FASTSET_PER_PACKET:
                cur.append(item)
            else:
                runs.append(cur)
                cur = [item]
        runs.append(cur)

        sent = 0
        for run in runs:
            start = run[0][0]
            body = bytearray()
            for _idx, (h, s, v) in run:
                body += bytes(bytearray([h & 0xFF, s & 0xFF, v & 0xFF]))
            payload = struct.pack(
                "BBHB", CMD_LIGHTING_SET_VALUE, VIALRGB_DIRECT_FASTSET, start, len(run)
            ) + bytes(body)
            self._send(payload)
            sent += len(run)
        return sent

    def raw_request(self, payload, retries=2):
        """协议调试台用：原样发送，返回响应。"""
        return self._send(payload, retries=retries)

    # ------------------------------------------------------------------
    # 描述
    # ------------------------------------------------------------------
    def describe(self):
        """设备画像。**不会因为读键盘定义失败而整体失败。**

        ``read_definition()`` 走 0xFE 分页读取 + XZ 解压，偶发读到坏页是
        可能的（尤其是设备刚被别的程序占用过）。定义只是锦上添花的信息，
        读坏了也应该照样能报告 VID/PID/协议版本/探测过程，所以这里把
        异常收敛成 ``definition_error`` 字段。
        """
        d = {}
        definition_error = None
        if self.vial_protocol:
            try:
                d = self.read_definition()
            except Exception as exc:
                definition_error = str(exc)
        return {
            "name": d.get("name") or (self.info.product or "未知键盘"),
            "manufacturer": self.info.manufacturer,
            "product": self.info.product,
            "vendor_id": self.info.vendor_id,
            "product_id": self.info.product_id,
            "serial": self.info.serial,
            "path": self.info.key,
            "via_protocol": self.via_protocol,
            "vial_protocol": self.vial_protocol,
            "keyboard_uid": None if self.keyboard_uid is None else "0x%016X" % self.keyboard_uid,
            "vialrgb_flag": self.vialrgb_flag,
            "rgb_protocol": self.rgb_protocol,
            "rgb_max_brightness": self.rgb_max_brightness,
            "lighting_backend": self.lighting_backend,
            "definition_lighting": d.get("lighting"),
            "matrix": d.get("matrix"),
            "num_leds": self.num_leds,
            "supported_effects": self.supported_effects,
            "definition_keys": len(((d.get("layouts") or {}).get("keymap")) or []),
            "detect_log": self.methods_tried,
            "zones": self.zone_counts() if self.lighting_backend == "vialrgb" else None,
            "amk_protocol_version": self.amk_protocol_version,
            "amk_rgb_param_supported": (
                None if self.lighting_backend != "amk"
                else self.rgb_param_supported()),
            "amk_strips": [
                {"index": s.index, "start": s.start, "count": s.count,
                 "mode": s.mode, "config": s.config,
                 "mode_editable": effects.strip_mode_editable(s.mode)}
                for s in self.strips()
            ] if self.lighting_backend == "amk" else None,
            "amk_rgb_matrix": d.get("amk_rgb_matrix"),
            "indicator": d.get("indicator"),
            "definition_error": definition_error,
        }


def open_first_vial():
    """便捷函数：打开第一个 Vial 设备并完成探测。"""
    devices = VialDevice.discover()
    if not devices:
        devices = VialDevice.discover_all_raw()
    if not devices:
        raise VialError("没有找到 Vial / VIA raw HID 设备")
    dev = VialDevice(devices[0]).open()
    dev.read_versions()
    dev.detect_lighting()
    return dev
