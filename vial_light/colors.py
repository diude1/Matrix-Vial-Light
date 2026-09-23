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
    """(r, g, b) 0-255 -> HSV 0-255。"""
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    v = mx
    if mx == 0:
        return 0, 0, 0
    s = int(delta * 255 / mx)
    if delta == 0:
        return 0, s, v
    if mx == r:
        h = int(43 * ((g - b) / delta) / 6) % 256
    elif mx == g:
        h = int(43 * ((b - r) / delta + 2) / 6) % 256
    else:
        h = int(43 * ((r - g) / delta + 4) / 6) % 256
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
