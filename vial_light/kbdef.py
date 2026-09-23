"""键盘定义（内嵌 vial.json）解析与绘制辅助。

vial.json 的 ``layouts.keymap`` 用 KLE 格式描述物理配列：
每个键要么是 ``"行,列"`` 字符串，要么是带 ``x`` / ``y`` / ``w`` / ``h`` /
``r`` / ``rx`` / ``ry`` 的字典。

KLE 的坐标规则（**必须完整实现，否则 Alice 式配列一定画歪**）：

* 每个顶层数组是新的一「行」，行首 ``x`` 重置为 0，行尾 ``y += 1``。
* ``x`` 只影响其后第一个键。
* ``rx`` / ``ry`` 是旋转基准点；声明后 ``x`` 重置到 ``rx``、``y`` 重置到
  ``ry``。``r`` 是对该基准点的顺时针旋转角度（度）。
* ``r`` / ``rx`` / ``ry`` 持续生效到被新声明覆盖 —— 旋转的是「区块」。
"""

import math


class Key(object):
    __slots__ = ("x", "y", "w", "h", "row", "col", "options",
                 "r", "rx", "ry")

    def __init__(self, x, y, w, h, row, col, options=None,
                 r=0.0, rx=0.0, ry=0.0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.row = row
        self.col = col
        self.options = options
        self.r = r
        self.rx = rx
        self.ry = ry

    @property
    def label(self):
        if self.row is None:
            return ""
        return "%d,%d" % (self.row, self.col)

    @property
    def rotated(self):
        return bool(self.r)

    def corners(self):
        """返回旋转后的四个角，供精确计算外接框 / 绘制多边形。"""
        pts = [(self.x, self.y), (self.x + self.w, self.y),
               (self.x + self.w, self.y + self.h), (self.x, self.y + self.h)]
        if not self.r:
            return pts
        a = math.radians(self.r)
        ca, sa = math.cos(a), math.sin(a)
        out = []
        for px, py in pts:
            dx, dy = px - self.rx, py - self.ry
            out.append((self.rx + dx * ca - dy * sa,
                        self.ry + dx * sa + dy * ca))
        return out

    def __repr__(self):
        return "<Key (%g,%g) %gx%g matrix=%s r=%g>" % (
            self.x, self.y, self.w, self.h, self.label, self.r)


class KeyboardLayout(object):
    def __init__(self, keys, matrix=None, name=None, mapped=None):
        self.keys = keys
        self.matrix = matrix or {}
        self.name = name
        if mapped is None:
            mapped = set((k.row, k.col) for k in keys if k.row is not None)
        self.mapped = mapped
        # 外接框必须按**旋转后的角点**算，否则带 r 的键会被低估尺寸
        pts = []
        for k in keys:
            pts.extend(k.corners())
        if not pts:
            pts = [(0.0, 0.0), (1.0, 1.0)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.min_x, self.max_x = min(xs), max(xs)
        self.min_y, self.max_y = min(ys), max(ys)

    @property
    def width_units(self):
        return self.max_x - self.min_x

    @property
    def height_units(self):
        return self.max_y - self.min_y

    def __len__(self):
        return len(self.keys)


def _parse_matrix_position(text):
    """``"0,3"`` 或 ``"0,10\\n\\n\\n3,0"`` -> (row, col) 用第一个位置。"""
    if not isinstance(text, str):
        return None, None, None
    parts = [p for p in text.split("\n") if p.strip()]
    if not parts:
        return None, None, None
    primary = parts[0].strip()
    variants = [p.strip() for p in parts[1:]]
    try:
        row_s, col_s = primary.split(",")
        return int(row_s), int(col_s), variants
    except ValueError:
        return None, None, None


def count_layout_options(definition):
    """``layouts.labels`` 里定义的选项数（每项可选方案个数）。"""
    labels = ((definition or {}).get("layouts") or {}).get("labels") or []
    out = []
    for item in labels:
        out.append(len(item) if isinstance(item, list) else 2)
    return out


def parse_layout(definition, options=None):
    """把键盘定义中的 ``layouts.keymap`` 解析成 :class:`KeyboardLayout`。

    完整实现 KLE 规范，这是把 Alice 式分离人体工学配列画对的前提：

    * **每个顶层数组是新的一「行」** —— 行首 ``x`` 重置为 0，行尾 ``y += 1``。
      （旧实现漏了这条：``x`` 在行之间会累积，把键盘拉成 30U 长条。）
    * ``x`` 只影响**其后第一个键**；``rx`` 把 ``x`` 重置到旋转基准点。
    * ``ry`` 把 ``y`` 重置到旋转基准点（旧实现把 ``ry`` 当普通累加，导致
      旋转区块一路往下掉）。
    * ``r`` / ``rx`` / ``ry`` 一旦声明，对**后续所有键**持续生效，
      直到被新的声明覆盖 —— 所以旋转的绝对是「区块」而不是单个键。
    * ``labels`` 里的 ``\\n\\n\\n`` 变体是**布局选项**（Step Caps / 空格宽度 /
      Split Backspace 等）。默认只渲染每个键的**主位置**，选项变体不参与
      几何 —— 旧实现把变体也当独立键画了出来，于是同一格出现两个方块。

    ``options`` 目前接受 ``None``（用默认组合）。键的取色/标签仍以主位置为准。
    """
    layouts = (definition or {}).get("layouts") or {}
    rows = layouts.get("keymap") or []
    keys = []
    y = 0.0
    r = 0.0
    rx = 0.0
    ry = 0.0
    for row in rows:
        x = 0.0                    # KLE：每个顶层数组行首重置 x
        w = 1.0
        h = 1.0
        for item in row:
            if isinstance(item, dict):
                # 顺序很重要：先 rx（重置基准）再 x（相对偏移）
                if "rx" in item:
                    rx = float(item["rx"])
                    x = rx
                if "x" in item:
                    x += float(item["x"])
                if "y" in item:
                    y += float(item["y"])
                if "w" in item:
                    w = float(item["w"])
                if "h" in item:
                    h = float(item["h"])
                if "r" in item:
                    r = float(item["r"])
                if "ry" in item:
                    ry = float(item["ry"])
                    y = ry
                continue
            row_i, col_i, variants = _parse_matrix_position(item)
            if row_i is not None:
                keys.append(Key(x, y, w, h, row_i, col_i, variants,
                                r=r, rx=rx, ry=ry))
            x += w
        y += 1.0
    return KeyboardLayout(keys, (definition or {}).get("matrix"),
                          (definition or {}).get("name"))


#: 常用 QMK 键码名（够标签显示用）
_HID_NAMES = {
    0x00: "--",
    0x01: "▽",
    0x28: "Enter",
    0x29: "Esc",
    0x2A: "Bksp",
    0x2B: "Tab",
    0x2C: "Space",
    0x2D: "-",
    0x2E: "=",
    0x2F: "[",
    0x30: "]",
    0x31: "\\",
    0x32: "#",
    0x33: ";",
    0x34: "'",
    0x35: "`",
    0x36: ",",
    0x37: ".",
    0x38: "/",
    0x39: "Caps",
    0x46: "PrtSc",
    0x47: "ScrLk",
    0x48: "Pause",
    0x49: "Ins",
    0x4A: "Home",
    0x4B: "PgUp",
    0x4C: "Del",
    0x4D: "End",
    0x4E: "PgDn",
    0x4F: "→",
    0x50: "←",
    0x51: "↓",
    0x52: "↑",
    0x53: "Num",
    0x54: "/",
    0x55: "*",
    0x56: "-",
    0x57: "+",
    0x58: "Enter",
    0xE0: "LCtl",
    0xE1: "LSft",
    0xE2: "LAlt",
    0xE3: "LGui",
    0xE4: "RCtl",
    0xE5: "RSft",
    0xE6: "RAlt",
    0xE7: "RGui",
}
for _i in range(26):
    _HID_NAMES[0x04 + _i] = chr(ord("A") + _i)
for _i in range(9):
    _HID_NAMES[0x1E + _i] = str(_i + 1)
_HID_NAMES[0x27] = "0"
for _i in range(12):
    _HID_NAMES[0x3A + _i] = "F%d" % (_i + 1)


def keycode_name(keycode):
    """QMK 16 位键码 -> 简短可读文本。"""
    if keycode is None:
        return ""
    code = keycode & 0xFFFF
    if code == 0x0000:
        return ""
    if code == 0x0001:
        return "▽"
    if code in _HID_NAMES:
        return _HID_NAMES[code]
    high = (code >> 8) & 0xFF
    low = code & 0xFF
    if high == 0x52:  # MO(x) / TG(x) / TO(x) ... 0x52xx 为层操作
        base = {0x00: "MO", 0x10: "TO", 0x20: "TG", 0x30: "TT", 0x40: "OSL"}.get(low & 0xF0, "LY")
        return "%s%d" % (base, low & 0x0F)
    if high == 0x5C:  # 自定义键码
        return "CUS%d" % low
    if 0x04 <= code <= 0xE7:
        return _HID_NAMES.get(code, "0x%02X" % code)
    if high == 0x00:
        return "0x%04X" % code
    return "0x%04X" % code


def collect_matrix_cells(definition):
    """从定义里收集所有被映射到的 ``(行, 列)``。"""
    used = set()
    rows = ((definition or {}).get("layouts") or {}).get("keymap") or []
    for row in rows:
        for item in row:
            if not isinstance(item, str):
                continue
            for part in item.split("\n"):
                part = part.strip()
                if not part or "," not in part:
                    continue
                try:
                    r, c = part.split(",")
                    used.add((int(r), int(c)))
                except ValueError:
                    continue
    return used


def matrix_grid_layout(definition):
    """按键盘声明的矩阵尺寸生成规整网格视图。

    Matrix 系键盘（如 Faukwaa）的 ``layouts.keymap`` 是机器生成的**拼贴**结构
    —— 每个数组是一个独立区块，会出现 ``x=-5``、``y=-2.75`` 这类越界偏移，
    并不是标准的 KLE 逐行结构。按 KLE 规则累加会把它拉成一条竖直长条，
    画出来和实物不符。所以默认用这个矩阵网格视图：它不依赖定义的坐标写法，
    对任何键盘都成立，而且直接标出 ``行,列``，做灯光排查时反而更好用。
    """
    matrix = (definition or {}).get("matrix") or {}
    rows = int(matrix.get("rows") or 0)
    cols = int(matrix.get("cols") or 0)
    used = collect_matrix_cells(definition)
    if rows <= 0 or cols <= 0:
        if not used:
            return KeyboardLayout([], matrix, (definition or {}).get("name"), set())
        rows = max(r for r, _c in used) + 1
        cols = max(c for _r, c in used) + 1
    keys = [Key(float(c), float(r), 1.0, 1.0, r, c, None) for r in range(rows) for c in range(cols)]
    return KeyboardLayout(keys, matrix, (definition or {}).get("name"), used)


def layout_looks_montage(layout):
    """粗略判断物理配列解析结果是否可信。

    正常客制化键盘约 15–18U 宽、5–7U 高（含 Alice 分离配列）。
    若外接框显著超出这个范围，说明解析仍然没对齐，此时应引导用户
    改用矩阵网格视图，而不是把歪掉的图画出去。
    """
    if layout is None or not layout.keys:
        return False
    return layout.height_units > 9.0 or layout.width_units > 26.0
