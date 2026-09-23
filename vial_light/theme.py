"""Tkinter 图形界面：Matrix / Vial 键盘灯光调整。

视觉规范：Windows 11 风格（Mica 浅色）——
浅灰底 + 分层卡片 + 8px 圆角 + 天蓝强调色 + Segoe UI Variable 字体。
"""

import tkinter as tk
from tkinter import colorchooser, font as tkfont, messagebox, ttk

from . import colors as C
from . import effects, kbdef
from .device import VialDevice, VialError, ZONE_LABELS

# ---------------------------------------------------------------------------
# Win11 Mica Light 设计令牌
# 取值参照 Windows 11 官方 Fluent 色板与 WinUI 3 默认主题
# ---------------------------------------------------------------------------
BG = "#f3f3f3"           # Mica 基底（窗口背景）
LAYER = "#fbfbfb"        # Layer：卡片
LAYER_ALT = "#f6f6f6"    # Layer 交替行
CARD = "#ffffff"         # 实心卡片（输入框、列表）
SUBTLE = "#ededed"       # 次级填充（滑块槽、分段未选中）
SUBTLE_HOVER = "#e5e5e5"
STROKE = "#e5e5e5"       # ControlStroke 默认描边
STROKE_STRONG = "#d6d6d6"
CARD_STROKE = "#ebebeb"  # CardStroke

FG = "#1b1b1b"          # TextPrimary
FG_SEC = "#5d5d5d"      # TextSecondary
FG_TER = "#8a8a8a"      # TextTertiary
FG_DIS = "#a6a6a6"      # TextDisabled

ACCENT = "#0067c0"       # AccentDefault
ACCENT_HOVER = "#1975c5"
ACCENT_PRESS = "#005ba1"
ACCENT_LIGHT = "#e8f2fb"
ACCENT_FG = "#ffffff"

OK = "#0f7b0f"
OK_BG = "#dff6dd"
WARN = "#9d5d00"
WARN_BG = "#fff4ce"
ERR = "#c42b1c"
ERR_BG = "#fde7e9"

#: 4px 栅格（Win11 用 4 的倍数）
GAP = 4
PAD = 8
PAD_L = 16

UI_FONT = "Segoe UI Variable Text"
UI_FONT_FALLBACK = "Segoe UI"
MONO_FONT = "Cascadia Mono"
MONO_FALLBACK = "Consolas"

RADIUS = 8       # 卡片圆角
RADIUS_CTL = 4   # 控件圆角（Win11 按钮/输入框）


def _pick_font(root, *candidates):
    """挑第一个系统里真实可用的字体。

    注意：Tk 的 ``font.families()`` 有时不含某些真实存在的字体
    （例如 Windows 11 的 "Segoe UI Variable Text"），所以还要用
    ``Font().actual("family")`` 回读验证 —— Tk 找不到字体时会静默
    回退到默认字体，靠 actual() 才能识别出来。
    """
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()

    for name in candidates:
        if name in available:
            return name

    # 备选路径：实际创建一次，看 Tk 是否接受
    for name in candidates:
        try:
            f = tkfont.Font(root=root, family=name, size=10)
            actual = f.actual("family")
            if actual and actual.lower() == name.lower():
                f.delete_font() if hasattr(f, "delete_font") else None
                return name
        except Exception:
            continue

    return candidates[-1]


def _hex_to_rgb(hx):
    hx = hx.lstrip("#")
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


def _rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (
        max(0, min(255, int(r))),
        max(0, min(255, int(g))),
        max(0, min(255, int(b))),
    )


def mix(a, b, t):
    """在 a、b 之间按 t 插值（t=0 取 a）。"""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return _rgb_to_hex(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


def shade(hx, deg=0.06):
    """压暗一点，用于 hover。"""
    return mix(hx, "#000000", deg)


# ---------------------------------------------------------------------------
# 自绘基础构件
# ---------------------------------------------------------------------------
def rounded_points(x1, y1, x2, y2, r):
    """圆角矩形的平滑多边形顶点（Tk 用 Catmull-Rom 近似）。"""
    r = max(min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0), 0)
    if r <= 0.6:
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def round_rect(cv, x1, y1, x2, y2, r, fill, outline=None, width=1, tags=None):
    """在草图上画一个圆角矩形（Tkinter 没有原生支持，用平滑多边形近似）。"""
    pts = rounded_points(x1, y1, x2, y2, r)
    kw = {"fill": fill, "smooth": True, "splinesteps": 12}
    if outline:
        kw["outline"] = outline
        kw["width"] = width
    else:
        kw["outline"] = ""
    if tags:
        kw["tags"] = tags
    return cv.create_polygon(pts, **kw)


class ScrollArea(tk.Frame):
    """可竖向滚动的容器（用于内容比窗口高的页面）。

    结构::

        ScrollArea (Frame, bg=BG)
          ├─ Canvas            ← 视口，负责 yview
          │    └─ inner (Frame) ← 真正放内容的容器
          └─ 细滚动条（自绘 Canvas，按需显示）

    **为什么要自己写**：Tkinter 没有可滚动 Frame。常见做法是
    ``Canvas + create_window(inner)``，但有两个必踩的坑：

    1. 只设了 ``scrollregion`` 还不够 —— 内容 Frame 的**请求高度变化时**
       必须重新计算 scrollregion，否则滚到底部仍有内容看不见。
       这里在 ``inner`` 上绑 ``<Configure>`` 统一处理。
    2. 滚动条只在真正溢出时才该出现。宽度变化会影响换行 → 又改变高度，
       所以要防抖（``after_idle``），否则会出现「显示滚动条 → 变窄 →
       内容变高 → 更该显示」的抖动循环。

    **鼠标滚轮**：Windows 上 ``<MouseWheel>`` 只发给有焦点的控件，而
    画布里的子控件（按钮、输入框）会把事件吃掉。所以这里用
    ``bind_all`` 在**指针进入本区域时**接管滚轮，离开时解绑 ——
    这是让「在任意子控件上滚」都能生效的唯一可靠办法。
    """

    def __init__(self, parent, bg=BG, pad=0, step=40):
        tk.Frame.__init__(self, parent, bg=bg)
        self._bg = bg
        self._step = step
        self._wheel_bound = False

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        #: 内容容器 —— 调用方往这里 pack
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")

        # 细滚动条（自绘，和主题一致；ttk.Scrollbar 太老气）
        self._bar = tk.Canvas(self, width=8, bg=bg, highlightthickness=0, bd=0)
        self._bar_thumb = None
        self._bar.bind("<Button-1>", self._on_bar_click)
        self._bar.bind("<B1-Motion>", self._on_bar_drag)

        self.inner.bind("<Configure>", self._on_inner_config)
        self.canvas.bind("<Configure>", self._on_canvas_config)

        # 指针进出时才接管滚轮，避免抢别的区域的滚动
        self.bind("<Enter>", self._grab_wheel)
        self.bind("<Leave>", self._release_wheel)

        self._visible = False

    # ---- 几何 ---------------------------------------------------------
    def _on_inner_config(self, _event=None):
        self._sync()

    def _on_canvas_config(self, event=None):
        # 视口宽度变化时让内容跟着变宽（否则内容不会自动撑满）
        if event is not None:
            self.canvas.itemconfigure(self._win, width=event.width)
        self._sync()

    def _sync(self):
        """重算 scrollregion 并按需显示/隐藏滚动条。"""
        req_h = self.inner.winfo_reqheight()
        view_h = self.canvas.winfo_height()
        need = req_h > view_h + 1

        self.canvas.configure(scrollregion=(0, 0, 0, max(req_h, view_h)))

        if need != self._visible:
            self._visible = need
            if need:
                self._bar.pack(side="right", fill="y")
            else:
                self._bar.pack_forget()
        if need:
            self._draw_bar()
        # 内容变矮后如果当前视口越界了，拉回来
        if not need:
            self.canvas.yview_moveto(0)

    def _draw_bar(self):
        self._bar.delete("all")
        h = max(self._bar.winfo_height(), 1)
        req_h = max(self.inner.winfo_reqheight(), 1)
        view_h = max(self.canvas.winfo_height(), 1)
        frac = min(view_h / float(req_h), 1.0)
        thumb = max(int(h * frac), 24)
        first, last = self.canvas.yview()
        top = int(first * h)
        top = max(0, min(top, h - thumb))
        self._bar_thumb = (top, thumb)
        round_rect(self._bar, 2, top + 1, 6, top + thumb - 1, 2,
                   STROKE_STRONG if self._bar_hover() else "#cfcfcf", None)

    def _bar_hover(self):
        return False

    # ---- 滚动条拖动 ---------------------------------------------------
    def _on_bar_click(self, event):
        h = max(self._bar.winfo_height(), 1)
        if self._bar_thumb and self._bar_thumb[0] <= event.y <= (
                self._bar_thumb[0] + self._bar_thumb[1]):
            return  # 按在滑块上，交给 drag
        self.canvas.yview_moveto(max(0.0, min(event.y / float(h), 1.0)))
        self._draw_bar()

    def _on_bar_drag(self, event):
        h = max(self._bar.winfo_height(), 1)
        self.canvas.yview_moveto(max(0.0, min(event.y / float(h), 1.0)))
        self._draw_bar()

    # ---- 滚轮 ---------------------------------------------------------
    def _grab_wheel(self, _event=None):
        if self._wheel_bound:
            return
        self._wheel_bound = True
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)
        self._draw_bar()

    def _release_wheel(self, _event=None):
        # 指针离开可能只是进了子控件，用 after_idle 复核一次真实位置
        self.after_idle(self._maybe_release)

    def _maybe_release(self):
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
        except Exception:
            w = None
        if w is not None and self._is_inside(w):
            return
        self._unbind_wheel()

    def _is_inside(self, widget):
        w = widget
        while w is not None:
            if w is self:
                return True
            try:
                w = w.master
            except Exception:
                return False
        return False

    def _unbind_wheel(self):
        if not self._wheel_bound:
            return
        self._wheel_bound = False
        try:
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
        except Exception:
            pass

    def _on_wheel(self, event):
        if not self._visible:
            return
        if getattr(event, "num", None) == 4:
            delta = 1
        elif getattr(event, "num", None) == 5:
            delta = -1
        else:
            d = getattr(event, "delta", 0)
            delta = 1 if d > 0 else (-1 if d < 0 else 0)
        if not delta:
            return
        self.canvas.yview_scroll(-delta * 3, "units")
        self._draw_bar()

    # ---- 对外 ---------------------------------------------------------
    def scroll_to_top(self):
        self.canvas.yview_moveto(0)
        self._draw_bar()

    def reset_scroll_region(self):
        """内容整体替换后调用（例如切页签时重新量一次）。"""
        self.update_idletasks()
        self._sync()


class Card(tk.Frame):
    """Win11 卡片：8px 圆角 + CardStroke 描边 + Layer 填充。

    Tkinter 的 Frame 不能画圆角，所以用 Canvas 做背景层，内容用 Frame 叠在上面。

    **尺寸由内容决定**：内容 Frame 用 ``pack`` 参与正常的几何协商，
    卡片自身高度 = 内容高度 + 上下 padding（所以卡片会自己长高）。
    若调用方希望卡片被拉伸填充（例如画布容器），显式传
    ``expand_inside=True`` 改为 ``place`` 布局。

    ⚠️ 注意：这里用 ``self._canvas`` / ``self._pad``，**绝不能**用
    ``self._w`` —— Tkinter 的 ``Misc._w`` 存的是窗口路径名，覆盖它会让
    整个控件失效（``bad window path name "42"``）。
    """

    def __init__(self, parent, radius=RADIUS, fill=LAYER, stroke=CARD_STROKE,
                 padding=PAD_L, expand_inside=False, **kw):
        tk.Frame.__init__(self, parent, bg=BG)
        self._radius = radius
        self._fill = fill
        self._stroke = stroke
        self._pad = padding
        self._expand_inside = expand_inside

        # 背景层：Canvas 铺满整张卡片（place 脱离布局，不会影响卡片尺寸）
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # 内容层：参与 pack 几何协商，决定卡片实际高度
        self.body = tk.Frame(self, bg=fill, **kw)
        if expand_inside:
            self.body.place(x=padding, y=padding, relwidth=0, relheight=0)
        else:
            self.body.pack(fill="both", expand=True,
                           padx=padding, pady=padding)

        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 2 or h <= 2:
            return
        self._canvas.delete("bg")
        round_rect(self._canvas, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                   self._fill, self._stroke, 1, tags="bg")
        self._canvas.tag_lower("bg")
        if self._expand_inside:
            p = self._pad
            self.body.place_configure(x=p, y=p, width=max(w - 2 * p, 1),
                                      height=max(h - 2 * p, 1))

    @property
    def inner(self):
        return self.body


class FlatButton(tk.Canvas):
    """Win11 按钮：4px 圆角、扁平、hover 提亮。

    variant: "accent" 实心天蓝 / "standard" 浅灰描边 / "subtle" 无描边
    """

    def __init__(self, parent, text, command=None, variant="standard",
                 width=None, height=32, bg=LAYER, font=None, padx=16):
        self._font = font or (UI_FONT, 10)
        self._text = text
        self._command = command
        self._variant = variant
        self._bg = bg
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._draw_items = []
        self._pending = None

        # 先量文字宽度
        try:
            tw = tkfont.Font(root=parent, font=self._font).measure(text)
        except Exception:
            tw = len(text) * 8

        w = width or (tw + padx * 2)
        tk.Canvas.__init__(self, parent, width=w, height=height, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        # 注意：绝不能叫 self._w / self._h —— Tkinter 的 Misc 用 _w 存窗口
        # 路径名，覆盖它会让这个控件彻底失效（bad window path name）。
        self._bw = w
        self._bh = height

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.render()

    def _clear(self):
        try:
            for item in self._draw_items:
                self.delete(item)
        except tk.TclError:
            pass
        self._draw_items = []

    def render(self):
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except Exception:
                pass
            self._pending = None
        self._clear()
        fill, fg, stroke = self._colors()
        try:
            self._draw_items.append(
                round_rect(self, 0.5, 0.5, self._bw - 0.5, self._bh - 0.5,
                           RADIUS_CTL, fill, stroke, 1))
            self._draw_items.append(
                self.create_text(self._bw / 2, self._bh / 2, text=self._text,
                                 fill=fg, font=self._font))
        except tk.TclError:
            self._draw_items = []

    def configure_text(self, text):
        self._text = text
        self.render()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self.render()

    def _colors(self):
        if not self._enabled:
            return (SUBTLE, FG_DIS, None)
        if self._variant == "accent":
            if self._pressed:
                return (ACCENT_PRESS, ACCENT_FG, None)
            if self._hover:
                return (ACCENT_HOVER, ACCENT_FG, None)
            return (ACCENT, ACCENT_FG, None)
        if self._variant == "subtle":
            if self._pressed:
                return (mix(SUBTLE, "#000000", 0.08), FG, None)
            if self._hover:
                return (SUBTLE_HOVER, FG, None)
            return (self._bg, FG, None)
        # standard
        fill = STROKE if not self._hover else STROKE_STRONG
        if self._pressed:
            fill = mix(STROKE, "#000000", 0.05)
        return (fill, FG, STROKE_STRONG)

    def _on_enter(self, _e=None):
        self._hover = True
        self.render()

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self.render()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self.render()

    def _on_release(self, _e=None):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self.render()
        if was and self._command is not None:
            self._command()


class Segmented(tk.Frame):
    """Win11 分段控件：浅灰底槽 + 选中项白底高亮。

    options: [(value, label), ...]
    """

    def __init__(self, parent, options, value=None, command=None,
                 bg=LAYER, height=32):
        tk.Frame.__init__(self, parent, bg=bg)
        # 不要叫 _options —— Tkinter 的 Misc 有个同名方法
        self._opts = list(options)
        self._value = value if value is not None else self._opts[0][0]
        self._command = command
        self._bg = bg
        self._height = height
        self._items = {}
        self._enabled = True

        self._slot = tk.Canvas(self, height=height, bg=bg, highlightthickness=0,
                               bd=0)
        self._slot.pack()
        self._hover = None
        self.render()

    def _measure(self):
        try:
            f = tkfont.Font(root=self, font=(UI_FONT, 10))
        except Exception:
            f = None
        widths = []
        for _v, label in self._opts:
            widths.append((f.measure(label) if f else len(label) * 8) + 26)
        return widths

    def render(self):
        if not getattr(self, "_slot", None):
            return
        try:
            self._slot.delete("all")
        except tk.TclError:
            return
        widths = self._measure()
        total = sum(widths) + 8
        self._slot.configure(width=total)
        h = self._height
        pad = 4
        off = not self._enabled
        round_rect(self._slot, 0.5, 0.5, total - 0.5, h - 0.5, RADIUS_CTL + 1,
                   SUBTLE if not off else LAYER_ALT, STROKE, 1)
        x = pad
        self._items = {}
        try:
            f = tkfont.Font(root=self, font=(UI_FONT, 10))
        except Exception:
            f = None
        for (val, label), w in zip(self._opts, widths):
            selected = (val == self._value)
            hovered = (val == self._hover) and not off
            if selected:
                if off:
                    # 禁用时选中项退成「灰底灰字」，一眼看出点不动
                    fg = FG_DIS
                    round_rect(self._slot, x + 0.5, 3.5, x + w - 0.5, h - 3.5,
                               RADIUS_CTL, SUBTLE, STROKE, 1)
                else:
                    fill = CARD
                    fg = FG
                    round_rect(self._slot, x + 0.5, 3.5, x + w - 0.5, h - 3.5,
                               RADIUS_CTL, fill, STROKE_STRONG, 1)
            elif hovered:
                fill = mix(SUBTLE, "#000000", 0.04)
                fg = FG
                round_rect(self._slot, x + 0.5, 3.5, x + w - 0.5, h - 3.5,
                           RADIUS_CTL, fill, "", 1)
            else:
                fg = FG_DIS if off else FG_SEC
            self._slot.create_text(x + w / 2, h / 2, text=label, fill=fg,
                                   font=(UI_FONT, 10))
            self._items[val] = (x, w)
            x += w + 0

        self._slot.configure(cursor="arrow" if off else "hand2")
        self._slot.bind("<Motion>", self._on_motion)
        self._slot.bind("<Leave>", lambda e: self._set_hover(None))
        self._slot.bind("<Button-1>", self._on_click)

    def set_enabled(self, on):
        """禁用时置灰并吞掉点击（避免看起来能点、点了没反应）。"""
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if not on:
            self._hover = None
        self.render()

    def _hit(self, x):
        for val, (sx, w) in self._items.items():
            if sx <= x <= sx + w:
                return val
        return None

    def _set_hover(self, val):
        if val != self._hover:
            self._hover = val
            self.render()

    def _on_motion(self, event):
        if not self._enabled:
            return
        self._set_hover(self._hit(event.x))

    def _on_click(self, event):
        if not self._enabled:
            return
        val = self._hit(event.x)
        if val is None or val == self._value:
            return
        self._value = val
        self.render()
        if self._command is not None:
            self._command(val)

    def get(self):
        return self._value

    def set(self, val):
        self._value = val
        self.render()

    def set_options(self, options, value=None):
        """运行时换一组选项（分段控件在连接设备后才知道有几条灯条）。

        ``options`` 为空时会退化成一项占位，避免 ``_opts[0]`` 崩掉。
        """
        self._opts = list(options) or [("", "—")]
        if value is None:
            value = self._opts[0][0]
        self._value = value
        self._hover = None
        self.render()


class Check(tk.Frame):
    """Win11 复选框：圆角方块 + 对勾，选中实心天蓝。"""

    SIZE = 18

    def __init__(self, parent, text, variable, command=None, bg=LAYER,
                 fg=FG, fg_active=FG):
        tk.Frame.__init__(self, parent, bg=bg, cursor="hand2")
        self.var = variable
        self._command = command
        self._bg = bg
        self._fg = fg
        self._fg_active = fg_active
        self._hover = False
        self._pressed = False

        self.box = tk.Canvas(self, width=self.SIZE, height=self.SIZE, bg=bg,
                             highlightthickness=0, bd=0, cursor="hand2")
        self.box.pack(side="left")
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=(UI_FONT, 10),
                              cursor="hand2")
        self.label.pack(side="left", padx=(8, 0))

        for w in (self, self.box, self.label):
            w.bind("<Button-1>", self._on_press)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

        self.var.trace_add("write", lambda *a: self.render())
        self.render()

    def _enter(self, _e=None):
        self._hover = True
        self.render()

    def _leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self.render()

    def _on_press(self, _e=None):
        self._pressed = True
        self.render()

    def _on_release(self, _e=None):
        was = self._pressed
        self._pressed = False
        self.render()
        if was:
            self.var.set(0 if self.var.get() else 1)
            if self._command is not None:
                self._command()

    def render(self):
        s = self.SIZE
        self.box.delete("all")
        on = bool(self.var.get())
        if on:
            fill = ACCENT_PRESS if self._pressed else (
                ACCENT_HOVER if self._hover else ACCENT)
            round_rect(self.box, 0.5, 0.5, s - 0.5, s - 0.5, 4, fill, None, 1)
            self.box.create_line(4.2, s * 0.52, s * 0.42, s * 0.74,
                                 fill="#ffffff", width=2, capstyle="round")
            self.box.create_line(s * 0.42, s * 0.74, s - 3.8, s * 0.27,
                                 fill="#ffffff", width=2, capstyle="round")
            self.label.configure(fg=self._fg_active)
        else:
            edge = FG_TER if self._hover else FG_SEC
            fill = "#fdfdfd" if not self._hover else "#f7f7f7"
            round_rect(self.box, 0.5, 0.5, s - 0.5, s - 0.5, 4, fill, edge, 1.4)
            self.label.configure(fg=self._fg)


class Entry(tk.Frame):
    """Win11 输入框：4px 圆角 + 底边强调线。"""

    def __init__(self, parent, width=110, text="", bg=LAYER, justify="left",
                 font=None, on_submit=None):
        tk.Frame.__init__(self, parent, bg=bg)
        self._bg = bg
        self._on_submit = on_submit
        h = 32
        self.canvas = tk.Canvas(self, width=width, height=h, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        # 不要用 self._w / self._h（Tkinter 内部占用了 _w 存窗口路径）
        self._ew = width
        self._eh = h
        self._focused = False
        self._hover = False
        self._enabled = True

        self.var = tk.StringVar(value=text)
        self.entry = tk.Entry(self.canvas, textvariable=self.var, bd=0,
                              relief="flat", bg=CARD, fg=FG_TER, font=font or (MONO_FONT, 10),
                              justify=justify, insertbackground=FG,
                              highlightthickness=0)
        self._justify = justify
        self.canvas.bind("<Button-1>", self._on_click)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Enter>", self._enter)
        self.entry.bind("<Leave>", self._leave)
        if on_submit:
            self.entry.bind("<Return>", lambda e: on_submit())
        self.render()

    def _on_click(self, _e=None):
        if self._enabled:
            self.entry.focus_set()

    def set_enabled(self, on):
        """置灰 / 恢复。置灰时不可聚焦、不可编辑、描边变浅。"""
        self._enabled = bool(on)
        try:
            self.entry.configure(state="normal" if on else "disabled")
        except tk.TclError:
            pass
        if not on:
            self._focused = False
            self._hover = False
        self.render()

    def _focus_in(self, _e=None):
        self._focused = True
        self.render()

    def _focus_out(self, _e=None):
        self._focused = False
        self.render()

    def _enter(self, _e=None):
        self._hover = True
        self.render()

    def _leave(self, _e=None):
        self._hover = False
        self.render()

    def render(self):
        cv = self.canvas
        cv.delete("all")
        w, h = self._ew, self._eh
        if not self._enabled:
            edge = STROKE
            fill = SUBTLE
        else:
            edge = ACCENT if self._focused else (FG_TER if self._hover else STROKE_STRONG)
            fill = CARD
        round_rect(cv, 0.5, 0.5, w - 0.5, h - 0.5, RADIUS_CTL, fill, edge,
                   1.6 if self._focused else 1)
        # 底边强调线（Win11 聚焦态）
        if self._focused and self._enabled:
            cv.create_line(4, h - 1.6, w - 4, h - 1.6, fill=ACCENT, width=2.4)
        pad = 10
        self.entry.place(x=pad, y=2, width=max(w - 2 * pad, 10), height=h - 6)
        if not self._enabled:
            fg = FG_DIS
        else:
            fg = FG if self.var.get() else FG_TER
        self.entry.configure(fg=fg, bg=fill, justify=self._justify,
                             disabledforeground=FG_DIS, disabledbackground=fill)

    def get(self):
        return self.var.get()

    def set(self, text):
        self.var.set(text)
        self.render()


class Slider(tk.Frame):
    """Win11 滑块：细轨道 + 圆形滑块，拖到底会变粗。"""

    def __init__(self, parent, frm, to, value, command, bg=LAYER,
                 track_w=300, number_width=52):
        tk.Frame.__init__(self, parent, bg=bg)
        self._frm = frm
        self._to = to
        self._command = command
        self._bg = bg
        self._track_w = track_w
        self._sh = 32
        self._knob_r = 9
        self._dragging = False
        self._hover = False
        self._enabled = True

        self.var = tk.IntVar(value=int(value))

        self.canvas = tk.Canvas(self, width=track_w + 8, height=self._sh, bg=bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(side="left")

        self.box = Entry(self, width=number_width, text=str(int(value)), bg=bg,
                         justify="center", on_submit=self._commit)
        self.box.pack(side="left", padx=(12, 0))
        self.box.entry.configure(fg=FG)

        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Enter>", lambda e: self._set_hover(True))
        self.canvas.bind("<Leave>", lambda e: self._set_hover(False))
        self.render()

    def _set_hover(self, on):
        self._hover = on
        self.render()

    def _ratio(self):
        if self._to == self._frm:
            return 0.0
        r = (self.var.get() - self._frm) / float(self._to - self._frm)
        return max(0.0, min(1.0, r))

    def _x_for(self, ratio):
        return 4 + self._track_w * ratio

    def render(self):
        cv = self.canvas
        cv.delete("all")
        cy = self._sh / 2
        t = self._ratio()
        x = self._x_for(t)

        # 轨道：未填充段浅灰，已填充段天蓝（置灰时整体变浅）
        base_h = 4
        cv.create_line(4, cy, 4 + self._track_w, cy, fill=SUBTLE,
                       width=base_h, capstyle="round")
        if t > 0:
            cv.create_line(4, cy, x, cy,
                           fill=ACCENT if self._enabled else STROKE_STRONG,
                           width=base_h, capstyle="round")

        # 滑块：hover 或拖动时变大（Win11 的 inner-dot 行为）
        r = self._knob_r + (2 if (self._enabled and
                                  (self._dragging or self._hover)) else 0)
        cv.create_oval(x - r, cy - r, x + r, cy + r,
                       fill="#ffffff" if self._enabled else SUBTLE,
                       outline=STROKE_STRONG, width=1)
        ir = max(r - 5, 3)
        cv.create_oval(x - ir, cy - ir, x + ir, cy + ir,
                       fill=ACCENT if self._enabled else FG_DIS, outline="")

        if self.box.var.get() != str(self.var.get()):
            self.box.set(str(self.var.get()))

    def _set_from_x(self, px):
        if not self._enabled:
            return
        t = max(0.0, min(1.0, (px - 4) / float(max(self._track_w, 1))))
        v = int(round(self._frm + t * (self._to - self._frm)))
        if v != self.var.get():
            self.var.set(v)
            self.render()
            if self._command is not None:
                self._command(str(v))

    def _press(self, e):
        if not self._enabled:
            return
        self._dragging = True
        self._set_from_x(e.x)

    def _motion(self, e):
        if self._enabled and self._dragging:
            self._set_from_x(e.x)

    def _release(self, e):
        self._dragging = False
        self.render()

    def _commit(self):
        if not self._enabled:
            self.box.set(str(self.var.get()))
            return
        try:
            v = int(self.box.get())
        except ValueError:
            self.box.set(str(self.var.get()))
            return
        v = max(self._frm, min(self._to, v))
        self.var.set(v)
        self.box.set(str(v))
        self.render()
        if self._command is not None:
            self._command(str(v))

    def set_enabled(self, on):
        """置灰 / 恢复滑块（含数字框）。"""
        self._enabled = bool(on)
        try:
            self.canvas.configure(cursor="hand2" if on else "arrow")
        except tk.TclError:
            pass
        if not on:
            self._hover = False
            self._dragging = False
        self.box.set_enabled(on)
        self.render()

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(int(value))
        self.render()

    def set_range(self, frm, to):
        self._frm, self._to = frm, to
        self.render()

    def refresh(self):
        self.render()


def _chip(parent, text, color, bg=LAYER, font=None):
    """状态标签（圆角 pill）。"""
    f = font or (UI_FONT, 9)
    try:
        w = tkfont.Font(root=parent, font=f).measure(text) + 20
    except Exception:
        w = len(text) * 8 + 20
    cv = tk.Canvas(parent, width=w, height=22, bg=bg, highlightthickness=0, bd=0)
    round_rect(cv, 0.5, 0.5, w - 0.5, 21.5, 11, color, None, 1)
    cv.create_text(w / 2, 11, text=text, fill=FG, font=f)
    return cv
