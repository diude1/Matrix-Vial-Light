"""灯效 / 灯光模式名称表。

**三套互不相同的编号体系**，混用会让灯跑出完全不同的效果：

``AMK 轴灯``
    Matrix 固件的轴灯，走 ``0x81`` 字段（QMK 官方 ``id_qmk_rgblight_effect``）。
    编号 1-45，一一对应。实测 Faukwaa：写 0 会被固件改成 1，也就是说
    **轴灯无法通过这个字段关闭**。

``AMK 配件灯（灯条）``
    走 ``0xFD`` 扩展协议的 ``SET_RGB_STRIP_MODE``，编号 0-8。
    见 :data:`STRIP_EFFECTS`。

``VialRGB``
    由 VialRGB 协议固定，编号永不改变（见 vial-qmk ``vialrgb_effects.inc``），
    因此这里的名称是**权威**的。

旧版本曾把「AMK 轴灯」按 VialRGB 的编号取名字，那是错的：VialRGB 的
``Breathing`` 是 6，AMK 轴灯的呼吸在别的编号上，而配件灯的编号又是第三套。
"""

#: AMK 配件灯（灯条）灯效。
#:
#: 来源：Matrix 官方 Vial Web 的 ``amk/rgb.py`` 常量 + **真机实测**。
#:
#: 真机实测（Faukwaa，2026-09）：
#:
#: * ``SET_RGB_STRIP_MODE`` 对**任意**值都回 ``0xAA``，但读回会被
#:   **对 10 取模** —— 写 10 读回 0、写 11 读回 1、写 255 读回 5。
#:   所以这把键盘的灯效编号是 **0-9，共 10 档**，不是官方源码里的 0-8。
#: * 具体的「第 N 档是什么效果」由固件决定，官方源码里的名字表来自键盘
#:   定义里的 ``amkFeature``（本机定义没有这一段），所以**顺序不保证一致**。
#:   下面的中文名沿用官方顺序，仅作参考；实际以肉眼观察为准。
#:
#: 官方 ``Wipe`` 与 ``Scan`` 共用编号 7 —— 那是官方定义的重复，不是笔误。
STRIP_EFFECTS = [
    (0, "Custom", "自定义（逐灯上色）"),
    (1, "Gradient", "渐变"),
    (2, "Static", "静态"),
    (3, "Blink", "闪烁"),
    (4, "Rainbow", "彩虹"),
    (5, "Random", "随机"),
    (6, "Breath", "呼吸"),
    (7, "Wipe / Scan", "擦除 / 扫描"),
    (8, "Circle", "环绕"),
    (9, "Effect 9", "灯效 9（固件专有）"),
]

#: 配件灯灯效编号范围。真机实测是 **0-9 共 10 档**（写超过 9 会被对 10 取模）。
STRIP_EFFECT_MIN = 0
STRIP_EFFECT_MAX = 9

#: **自定义模式**的编号。只有这一档下，逐灯写入的 HSV / on / 速度才会被固件采用。
STRIP_EFFECT_CUSTOM = 0

#: 自定义模式下逐灯可调的速度上限（4 bit 位域）
STRIP_LED_SPEED_MAX = 15


def strip_effect_name(mode_id):
    """配件灯灯效编号 -> (英文名, 中文名)。"""
    for i, en, zh in STRIP_EFFECTS:
        if i == mode_id:
            return en, zh
    return "Effect %d" % mode_id, "灯效 %d" % mode_id


def strip_mode_editable(mode_id):
    """这个灯效档位下，逐灯的颜色 / 亮度 / 速度是否说了算？

    真机实测结论：**只有 ``Custom``（0）档**会采用 ``SET_RGB_STRIP_LED``
    写进去的逐灯 HSV / on / 速度；其余档位由固件按自己的算法渲染，
    逐灯数据只是被忽略。

    更关键的是：本机固件**没有实现灯条级参数通道**
    （``SET_RGB_PARAM(62)`` 对任何 param 都回 ``0x55``，
    ``GET_RGB_PARAM(61)`` / ``GET_RGB_DATA(60)`` 同样不可用），
    所以非 Custom 档**没有办法**再用软件改它的颜色/亮度 ——
    官方 Web 配置里那条 ``apply_rgb_param(RGB_TYPE_STRIP, RGB_PARAM_COLOR)``
    路径在这把键盘上根本走不通。
    """
    return mode_id == STRIP_EFFECT_CUSTOM


def vialrgb_name(mode_id):
    for i, en, zh in VIALRGB_EFFECTS:
        if i == mode_id:
            return en, zh
    return "Effect %d" % mode_id, "灯效 %d" % mode_id


#: AMK 轴灯编号范围（实测 Faukwaa：1-45 一一对应，46+ 夹取到 45，0 被改回 1）
AMK_MODE_MIN = 1
AMK_MODE_MAX = 45
#: 别名，语义上更直观（界面里用它标范围）
AMK_EFFECT_MAX = AMK_MODE_MAX
AMK_SPEED_MAX = 3
#: VialRGB 模式范围
VIALRGB_MODE_MAX = 44


def strip_effect_ids():
    return [i for i, _en, _zh in STRIP_EFFECTS]


def strip_effect_label(mode_id):
    en, zh = strip_effect_name(mode_id)
    return "%d  %s" % (mode_id, zh)


#: VialRGB 官方灯效编号 (id, 英文名, 中文名)
VIALRGB_EFFECTS = [
    (0, "Off", "关闭"),
    (1, "Direct", "逐键直接控制"),
    (2, "Solid Color", "纯色常亮"),
    (3, "Alphas Mods", "字母 + 修饰键区分色"),
    (4, "Gradient Up/Down", "上下渐变"),
    (5, "Gradient Left/Right", "左右渐变"),
    (6, "Breathing", "呼吸"),
    (7, "Band Saturation", "色带（饱和度）"),
    (8, "Band Value", "色带（亮度）"),
    (9, "Band Pinwheel Saturation", "风车色带（饱和度）"),
    (10, "Band Pinwheel Value", "风车色带（亮度）"),
    (11, "Band Spiral Saturation", "螺旋色带（饱和度）"),
    (12, "Band Spiral Value", "螺旋色带（亮度）"),
    (13, "Cycle All", "全彩循环"),
    (14, "Cycle Left/Right", "左右循环"),
    (15, "Cycle Up/Down", "上下循环"),
    (16, "Rainbow Moving Chevron", "三角彩虹流动"),
    (17, "Cycle Out/In", "由外向内循环"),
    (18, "Cycle Out/In Dual", "双向由外向内循环"),
    (19, "Cycle Pinwheel", "风车循环"),
    (20, "Cycle Spiral", "螺旋循环"),
    (21, "Dual Beacon", "双信标"),
    (22, "Rainbow Beacon", "彩虹信标"),
    (23, "Rainbow Pinwheels", "彩虹风车"),
    (24, "Raindrops", "雨滴"),
    (25, "Jellybean Raindrops", "糖果雨滴"),
    (26, "Hue Breathing", "色相呼吸"),
    (27, "Hue Pendulum", "色相摆锤"),
    (28, "Hue Wave", "色相波浪"),
    (29, "Typing Heatmap", "打字热力图"),
    (30, "Digital Rain", "数字雨"),
    (31, "Solid Reactive Simple", "单键触发（简单）"),
    (32, "Solid Reactive", "单键触发"),
    (33, "Solid Reactive Wide", "单键触发（宽）"),
    (34, "Solid Reactive Multiwide", "单键触发（多键宽）"),
    (35, "Solid Reactive Cross", "单键触发（十字）"),
    (36, "Solid Reactive Multicross", "单键触发（多十字）"),
    (37, "Solid Reactive Nexus", "单键触发（邻域）"),
    (38, "Solid Reactive Multinexus", "单键触发（多邻域）"),
    (39, "Splash", "涟漪"),
    (40, "Multisplash", "多重涟漪"),
    (41, "Solid Splash", "纯色涟漪"),
    (42, "Solid Multisplash", "纯色多重涟漪"),
    (43, "Pixel Rain", "像素雨"),
    (44, "Pixel Fractal", "像素分形"),
]

#: VialRGB 专用（仅 VialRGB 后端有意义的）模式
VIALRGB_DIRECT = 1
VIALRGB_OFF = 0

#: AMK 轴灯的灯效名。
#:
#: Faukwaa 的轴灯实际跑的是 Matrix 自己移植的 rgb_matrix 效果表，编号 1-45。
#: 固件不提供「把编号翻译成名字」的命令，所以我们**不能保证**这份名字准确。
#: 做法：只给出确凿的部分，其余一律显示「模式 N」，让用户自己用「上一个 /
#: 下一个灯效」逐档试并记下来 —— 比给一个看着像样但错误的名字好。
#:
#: 唯一能确定的是 1 = 静态纯色（原厂默认，且是写 0 时固件自动落到的档位）。
_AMK_AXIS_KNOWN = {
    1: ("Static", "静态纯色"),
}


def qmk_mode_name(mode_id):
    """AMK 轴灯灯效编号 -> (英文名, 中文名)。

    已知的返回确定名字，未知的返回「模式 N」。不要拿 VialRGB 的表来填 ——
    两套编号完全不对应。
    """
    if mode_id in _AMK_AXIS_KNOWN:
        return _AMK_AXIS_KNOWN[mode_id]
    if mode_id == 0:
        return "Off (固件会改回 1)", "关闭（固件会改回 1）"
    return "Mode %d" % mode_id, "模式 %d" % mode_id


def qmk_mode_label(mode_id):
    en, zh = qmk_mode_name(mode_id)
    return "%d  %s" % (mode_id, zh)


def effect_label(backend, mode_id):
    """给下拉框用的一行文字。"""
    if backend == "vialrgb":
        en, zh = vialrgb_name(mode_id)
        return "%d  %s  %s" % (mode_id, zh, en)
    if backend == "strip":
        return strip_effect_label(mode_id)
    en, zh = qmk_mode_name(mode_id)
    return "%d  %s" % (mode_id, zh)


def vialrgb_effect_ids():
    return [i for i, _en, _zh in VIALRGB_EFFECTS]


def amk_mode_ids():
    return list(range(AMK_MODE_MIN, AMK_MODE_MAX + 1))


#: 快捷预设：中文名 -> {后端: 模式号}
#:
#: ``amk`` 是轴灯（0x81），``strip`` 是配件灯（0xFD 扩展）——两者编号不同，
#: 所以必须分开写，不能共用一个数字。
QUICK_PRESETS = [
    ("纯色", {"amk": 1, "strip": 2, "vialrgb": 2}),
    ("呼吸", {"amk": None, "strip": 6, "vialrgb": 6}),
    ("彩虹", {"amk": None, "strip": 4, "vialrgb": 13}),
    ("渐变", {"amk": None, "strip": 1, "vialrgb": 4}),
    ("静态", {"amk": 1, "strip": 2, "vialrgb": 2}),
    ("自定义（逐灯）", {"amk": None, "strip": 0, "vialrgb": 1}),
]


def backend_label(backend):
    return {
        "amk": "AMK 灯光通道（Matrix 官方固件，轴灯 + 配件灯分开控制）",
        "vialrgb": "VialRGB（Vial 官方协议，支持逐键）",
        "via": "VIA 标准照明通道",
        "strip": "AMK 配件灯（灯条）",
        None: "未检测到可用的灯光通道",
    }.get(backend, str(backend))
