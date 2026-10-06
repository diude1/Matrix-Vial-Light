"""HSV / RGB 颜色工具。

QMK 与 VialRGB 都用 HSV(h, s, v) 三个 0-255 的字节描述颜色。
"""


def hsv_to_rgb(h, s, v):
    """HSV(0-255) -> (r, g, b) 0-255。"""
    if s == 0:
        return v, v, v
    region = h // 43
    remainder = (h - region * 43) * 6
    p = (v * (255 - s)) >> 8
    q = (v * (255 - ((s * remainder) >> 8))) >> 8
    t = (v * (255 - ((s * (255 - remainder)) >> 8))) >> 8
    if region == 0:
        return v, t, p
    if region == 1:
        return q, v, p
    if region == 2:
        return p, v, t
    if region == 3:
        return p, q, v
    if region == 4:
        return t, p, v
    return v, p, q


def rgb_to_hsv(r, g, b):
    """(r, g, b) 0-255 -> HSV 0-255。

    .. important::
       **hue 的算式里不能出现 ``/6``。** HSV 的六个扇区各占 1/6 色轮，
       而 0-255 档的 256 个色阶已经被压缩进了这六区，所以换算系数是
       ``256/6 = 42.5`` 而不是 ``43/6``。

       早先这里写成 ``int(43 * ((g - b) / delta) / 6)`` —— 相当于把色相
       压到 0-43 就折返，**整个色轮只用了一半**。后果是 RGB→HSV→RGB
       往返严重失真：``(255, 187, 0)`` 会被算成 hue=5，再转回
       ``(255, 30, 0)``（橙色变成红橙色），实测全色域最大误差 255。
       去掉 ``/6`` 并改用 ``round`` 后，最大误差降到 15（hsv_to_rgb
       本身的整数截断所致，属可接受范围），六个主色相也精确落在
       0 / 42 / 85 / 128 / 170 / 214。
    """
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    v = mx
    if mx == 0:
        return 0, 0, 0
    s = int(delta * 255 / mx)
    if delta == 0:
        return 0, s, v
    # 先算出色轮位置 0..6（R 扇区起点、G 扇区起点、B 扇区起点）
    if mx == r:
        sector = (g - b) / delta
    elif mx == g:
        sector = (b - r) / delta + 2
    else:
        sector = (r - g) / delta + 4
    h = int(round(sector * 42.5)) % 256
    if h < 0:
        h += 256
    return h, s, v


def hex_to_rgb(text):
    """``#rrggbb`` 或 ``rrggbb`` -> (r, g, b)。"""
    if not text:
        raise ValueError("空颜色值")
    t = text.strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6:
        raise ValueError("颜色格式应为 #rrggbb")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


def rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (r, g, b)


def hsv_to_hex(h, s, v):
    return rgb_to_hex(*hsv_to_rgb(h, s, v))


def clamp(value, low=0, high=255):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return low
    if value < low:
        return low
    if value > high:
        return high
    return value


#: 界面快捷色板（hue, sat, val）
PALETTE = [
    ("红", 0, 255, 255),
    ("橙", 21, 255, 255),
    ("黄", 43, 255, 255),
    ("绿", 85, 255, 255),
    ("青", 128, 255, 255),
    ("蓝", 171, 255, 255),
    ("紫", 213, 255, 255),
    ("粉", 235, 180, 255),
]
