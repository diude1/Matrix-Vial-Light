"""Tkinter 图形界面：Matrix / Vial 键盘灯光调整。

视觉规范：**Windows 11 风格（Mica 浅色）** —— 浅灰底 + 分层卡片 +
8px 圆角 + 天蓝强调色 + Segoe UI Variable 字体，控件全部落在自绘的
``theme`` 组件上，不用 ttk 的默认外观。

功能上多了「分区」页：把轴灯（键位灯）与配件灯（灯条 / 底灯 / 氛围灯）
拆成两组，可以分开设置 —— 前提是固件暴露了可单独寻址的 LED 接口。
AMK 官方固件通过扩展协议提供轴灯 / 灯条的独立通道，但逐键可编辑范围
仍由具体固件的矩阵通道决定。
"""

import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

import queue
import threading

from . import colors as C
from . import effects, kbdef, presets
from . import theme as T
from .device import VialDevice, VialError, ZONE_LABELS

# ---------------------------------------------------------------------------
# 设计令牌（从 theme 导出，保持既有调用点不用改）
# ---------------------------------------------------------------------------
BG = T.BG
SURFACE = T.LAYER
SURFACE_HI = T.SUBTLE
SURFACE_TOP = T.LAYER
LAYER_ALT = T.LAYER_ALT
CARD = T.CARD
STROKE = T.STROKE
STROKE_STRONG = T.STROKE_STRONG

FG = T.FG
FG_DIM = T.FG_SEC
FG_FAINT = T.FG_TER
FG_DIS = T.FG_DIS

LINE = T.STROKE

ACCENT = T.ACCENT
ACCENT_SOFT = T.ACCENT_HOVER
ACCENT_FG = T.ACCENT_FG

OK = T.OK
WARN = T.WARN
ERR = T.ERR

PAD = T.PAD
PAD_L = T.PAD_L

UI_FONT = T.UI_FONT_FALLBACK
MONO_FONT = T.MONO_FALLBACK

#: 分区色调（用来在统计卡片上区分两组灯）
ZONE_TINT = {
    "key": "#0067c0",
    "acc": "#8b5cf6",
}


def _hex_to_rgb(hx):
    hx = hx.lstrip("#")
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)


def blend(a, b, t):
    """在 a、b 之间按 t 插值（t=0 取 a）。"""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return "#%02x%02x%02x" % (
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


class _AsyncWorker(object):
    """把耗时的设备操作放到**后台线程**，结果回主线程（审查项 #7）。

    **关键约束**（违反会崩）：

    * 提交的 ``fn()`` 在 worker 线程跑，**只允许碰 ``dev``**（它内部已加
      I/O 锁，线程安全），**绝不允许碰 Tk 控件**（Tk 不是线程安全的）。
    * ``on_done(result)`` / ``on_error(exc)`` 在**主线程**跑，可以安全更新 UI。
    * 每条结果固定回调一次 ``_busy_exit()``，与 ``run()`` 里的 ``_busy_enter()``
      配对，保证忙碌计数不跑偏。
    """

    def __init__(self, app):
        self.app = app
        self._jobs = queue.Queue()
        self._results = queue.Queue()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="vial-async-worker")
        self._thread.start()
        app.after(30, self._drain)

    def run(self, fn, on_done=None, on_error=None, busy=None):
        """提交一个后台任务。``busy`` 是可选的忙碌提示文字。"""
        self.app._busy_enter(busy)
        self._jobs.put((fn, on_done, on_error))

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            try:
                fn, on_done, on_error = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                result = fn()
                self._results.put(("ok", result, on_done, on_error))
            except Exception as exc:
                self._results.put(("err", exc, on_done, on_error))

    def _drain(self):
        """主线程：把 worker 的结果派发回去，并收拢忙碌计数。"""
        try:
            while True:
                status, payload, on_done, on_error = self._results.get_nowait()
                try:
                    if status == "ok":
                        if on_done:
                            on_done(payload)
                    else:
                        if on_error:
                            on_error(payload)
                        else:
                            self.app.log("后台操作失败: %s" % payload, "err")
                finally:
                    self.app._busy_exit()
        except queue.Empty:
            pass
        if not self._stop:
            self.app.after(30, self._drain)


class App(tk.Tk):
    def __init__(self, selftest=False):
        tk.Tk.__init__(self)
        self.selftest = selftest
        self.title("键盘灯光 · vial-matrix-light")
        self.geometry("1180x780")
        self.minsize(1020, 680)
        self.configure(bg=BG)

        # 字体探测要在任何控件创建之前完成
        global UI_FONT, MONO_FONT
        UI_FONT = T._pick_font(self, T.UI_FONT, T.UI_FONT_FALLBACK, "Segoe UI")
        MONO_FONT = T._pick_font(self, T.MONO_FONT, T.MONO_FALLBACK, "Consolas")

        self.dev = None
        self.state = None
        self.layout = None
        self.layout_kle = None
        self.layout_matrix = None
        self.definition = None
        self._active_layout = None
        self.leds = []
        self._pending = None
        self._pending_job = None
        self._state_dirty = False
        self._busy = False
        self._anim_job = None
        self._anim_t = 0
        self._led_colors = []
        self._led_known = set()
        # 差量推送的影子缓冲：``{global_index: (h,s,v)}``，记录当前已在
        # 键盘上的颜色 —— 只把"变了"的灯发下去，避免每次全量 68 次往返。
        self._pushed = {}
        self._pk_push_job = None
        self._pk_pending = set()
        self._paint_tool = tk.StringVar(value="画笔")
        self._current_color = (0, 255, 255)
        self._ignore_scale = False
        self.v_hue = tk.IntVar(value=0)

        # 分区状态
        self._zone_mode = tk.StringVar(value="both")   # both / key / acc
        self._zone_colors = {"key": (171, 255, 255), "acc": (128, 255, 255)}
        self._zone_val = {"key": 255, "acc": 255}
        self._zone_capable = ()       # 可单独寻址的分区
        self._zone_reason = ""
        self._zone_counts = {"key": 0, "acc": 0}
        self._strip_index = None      # 当前选中的配件灯条
        self._strip_sel = None
        self._strip_color_swatch = None
        self._strip_color_hex = None
        self._strip_color_pick = None

        self._build_style()
        self._build_header()
        self._build_tabs()
        self._build_footer()
        self._set_state_dirty(False)

        # 后台 worker：把耗时 HID 操作从主线程挪走（审查项 #7）。
        # 必须在所有 _build_* 之后创建（_apply_busy 要引用 busy_lbl）。
        self._busy_n = 0
        self._async = _AsyncWorker(self)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if selftest:
            self.withdraw()
        self.after(80, self.refresh_devices)
        self.after(200, self._tick)

    # ==================================================================
    # 样式
    # ==================================================================
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=FG, font=(UI_FONT, 9))

        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=FG)

        # 页签：隐藏 ttk 自带 tab 条，改用自绘导航
        style.configure("TNotebook", background=BG, borderwidth=0,
                        tabmargins=(0, 0, 0, 0))
        style.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])

        # 下拉框：Win11 的浅色输入框（白底 + 细描边）
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, arrowcolor=FG_DIM, borderwidth=1,
                        relief="flat", selectbackground=CARD,
                        selectforeground=FG, padding=(8, 5),
                        bordercolor=STROKE)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD)],
                  foreground=[("readonly", FG)],
                  arrowcolor=[("active", ACCENT)],
                  bordercolor=[("focus", ACCENT)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", ACCENT_FG)
        self.option_add("*TCombobox*Listbox.borderWidth", 0)

        style.configure("TSeparator", background=STROKE)
        style.configure("TPanedwindow", background=BG)

    # ==================================================================
    # 顶部条
    # ==================================================================
    def _build_header(self):
        bar = tk.Frame(self, bg=SURFACE_TOP)
        bar.pack(side="top", fill="x")

        inner = tk.Frame(bar, bg=SURFACE_TOP)
        inner.pack(fill="x", padx=PAD_L, pady=11)

        left = tk.Frame(inner, bg=SURFACE_TOP)
        left.pack(side="left", fill="x", expand=True)

        self.brand = tk.Label(left, text="键盘灯光", bg=SURFACE_TOP, fg=FG,
                              font=(UI_FONT, 12))
        self.brand.pack(side="left")
        self.backend_lbl = tk.Label(left, text="未连接", bg=SURFACE_TOP,
                                    fg=FG_FAINT, font=(UI_FONT, 9))
        self.backend_lbl.pack(side="left", padx=(10, 0), pady=(2, 0))

        right = tk.Frame(inner, bg=SURFACE_TOP)
        right.pack(side="right")

        self.dev_var = tk.StringVar()
        self.dev_combo = ttk.Combobox(right, textvariable=self.dev_var, width=36,
                                      state="readonly", font=(UI_FONT, 9))
        self.dev_combo.pack(side="left")
        self.dev_combo.bind("<<ComboboxSelected>>", lambda e: None)

        self.refresh_btn = T.FlatButton(right, "刷新", command=self.refresh_devices,
                                        variant="standard", height=32, bg=SURFACE_TOP)
        self.refresh_btn.pack(side="left", padx=(PAD, 0))

        self.connect_btn = T.FlatButton(right, "连接", command=self.toggle_connect,
                                        variant="accent", height=32, bg=SURFACE_TOP)
        self.connect_btn.pack(side="left", padx=(PAD, 0))

        self.more_btn = T.FlatButton(right, "更多", command=self.pick_all_devices,
                                     variant="subtle", height=32, bg=SURFACE_TOP)
        self.more_btn.pack(side="left", padx=(4, 0))

        tk.Frame(self, bg=STROKE, height=1).pack(side="top", fill="x")

    # ==================================================================
    # 底部状态条
    # ==================================================================
    def _build_footer(self):
        tk.Frame(self, bg=STROKE, height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=SURFACE_TOP)
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=SURFACE_TOP)
        inner.pack(fill="x", padx=PAD_L, pady=6)

        self.dot = tk.Canvas(inner, width=8, height=8, bg=SURFACE_TOP,
                             highlightthickness=0)
        self.dot.pack(side="left", padx=(0, PAD))
        self.dot_id = self.dot.create_oval(0, 0, 7, 7, fill=FG_FAINT, outline="")

        self.status = tk.Label(inner, text="就绪", bg=SURFACE_TOP, fg=FG_DIM,
                               anchor="w", font=(UI_FONT, 9))
        self.status.pack(side="left")

        self.busy_lbl = tk.Label(inner, text="", bg=SURFACE_TOP, fg=WARN,
                                 font=(UI_FONT, 8))
        self.busy_lbl.pack(side="right", padx=(0, 10))

        self.hid_lbl = tk.Label(inner, text="", bg=SURFACE_TOP, fg=FG_FAINT,
                                font=(UI_FONT, 8))
        self.hid_lbl.pack(side="right")

    def log(self, text, level="info"):
        colour = {"info": FG_DIM, "ok": OK, "warn": WARN, "err": ERR}.get(level, FG_DIM)
        dot_colour = {"info": FG_FAINT, "ok": OK, "warn": WARN, "err": ERR}.get(
            level, FG_FAINT)
        try:
            self.dot.itemconfigure(self.dot_id, fill=dot_colour)
        except Exception:
            pass
        try:
            self.console.configure(state="normal")
            self.console.insert("end", text + "\n")
            self.console.tag_add(level, "end-2l", "end-1l")
            self.console.tag_config(level, foreground=colour)
            self.console.see("end")
            self.console.configure(state="disabled")
        except Exception:
            pass
        try:
            self.status.configure(text=text[:150], fg=colour)
        except Exception:
            pass

    # ==================================================================
    # 后台任务忙碌指示（配合 _AsyncWorker）
    # ==================================================================
    def _busy_enter(self, text=None):
        """有一个后台任务开始：计数 +1 并刷新指示。"""
        self._busy_n = getattr(self, "_busy_n", 0) + 1
        self._apply_busy(text)

    def _busy_exit(self):
        """有一个后台任务结束：计数 -1 并刷新指示。"""
        self._busy_n = max(0, getattr(self, "_busy_n", 0) - 1)
        self._apply_busy(None)

    def _apply_busy(self, text):
        """按当前忙碌计数刷新鼠标指针与 footer 提示。"""
        n = getattr(self, "_busy_n", 0)
        try:
            self.configure(cursor="watch" if n > 0 else "")
        except Exception:
            pass
        lbl = getattr(self, "busy_lbl", None)
        if lbl is not None:
            try:
                if n > 0:
                    lbl.configure(text="◌ %s" % (text or "处理中…"), fg=WARN)
                else:
                    lbl.configure(text="", fg=FG_FAINT)
            except Exception:
                pass

    # ==================================================================
    # 页签骨架
    # ==================================================================
    def _build_tabs(self):
        holder = tk.Frame(self, bg=BG)
        holder.pack(fill="both", expand=True)

        nav = tk.Frame(holder, bg=BG)
        nav.pack(fill="x", padx=PAD_L, pady=(PAD + 2, 0))

        # 下划线与标签放在同一容器里，坐标系天然一致
        self._chips = tk.Frame(nav, bg=BG, height=34)
        self._chips.pack(anchor="w", fill="x")
        self._chips.pack_propagate(False)

        self._tab_labels = ["灯光", "分区", "配列", "逐键", "信息"]
        self._tab_widgets = []
        for idx, text in enumerate(self._tab_labels):
            # 用固定宽度的容器包一层，下划线才能和文字**同宽**
            holder_lbl = tk.Frame(self._chips, bg=BG, width=52, height=30)
            holder_lbl.pack(side="left", padx=(0, 12))
            holder_lbl.pack_propagate(False)
            lbl = tk.Label(holder_lbl, text=text, bg=BG, fg=FG_DIM,
                           font=(UI_FONT, 10), cursor="hand2")
            lbl.place(relx=0.5, y=6, anchor="n")
            lbl.bind("<Button-1>", lambda e, i=idx: self.select_tab(i))
            lbl.bind("<Enter>", lambda e, w=lbl: self._tab_hover(w, True))
            lbl.bind("<Leave>", lambda e, w=lbl: self._tab_hover(w, False))
            self._tab_widgets.append(lbl)
            self._tab_holders = getattr(self, "_tab_holders", [])
            self._tab_holders.append(holder_lbl)

        self._tab_underline = tk.Frame(self._chips, bg=ACCENT, height=2, width=0)
        self._tab_underline.place(x=0, y=30)
        self._tab_underline.lift()
        self._tab_underline_pos = 0

        tk.Frame(holder, bg=STROKE, height=1).pack(fill="x", padx=PAD_L)

        self.nb = tk.Frame(holder, bg=BG)
        self.nb.pack(fill="both", expand=True, padx=PAD_L, pady=(10, PAD_L))

        # 页签容器分两类：
        #   * 表单型（灯光 / 分区）—— 内容会随功能增加长高，用 ScrollArea
        #     包起来，内容超出窗口时自动出现细滚动条；
        #   * 画布型（配列 / 逐键 / 信息）—— 内部的 Canvas / PanedWindow 本来
        #     就会自适应视口高度（fill+expand），再套滚动会互相打架，
        #     所以保持普通 Frame。
        # `_tabs[i]` 一律是「外层容器」（负责 grid / tkraise）；
        # 表单型的真正内容容器是 `_tabs[i].inner`。
        form_tabs = [T.ScrollArea(self.nb, bg=BG) for _ in range(2)]
        plain_tabs = [tk.Frame(self.nb, bg=BG) for _ in range(3)]
        #: 页签顺序 —— 自绘容器靠这个列表做 select/index
        self._tabs = form_tabs + plain_tabs
        self._scrolls = form_tabs + [None, None, None]
        self.tab_light, self.tab_zone = form_tabs
        self.tab_layout, self.tab_perkey, self.tab_info = plain_tabs

        # 注意：**不要**用 ttk.Notebook。
        # `style.layout("TNotebook", [("Notebook.client", ...)])` 只隐藏了 tab 条，
        # Notebook 自己仍然在顶部画一段「无标签的空白条」—— 在浅色主题下就是
        # 截图里那条灰带（内有几个空白小方块）。直接用 tk.Frame +
        # grid 叠放、只让当前页 `tkraise()`，行为一样但不会有那个残影。
        self._tabs[0].grid(row=0, column=0, sticky="nsew")
        self.nb.rowconfigure(0, weight=1)
        self.nb.columnconfigure(0, weight=1)
        # 其余页只注册进 grid（保证同格），显示与否由 _raise_tab 控制
        for tab in self._tabs[1:]:
            tab.grid(row=0, column=0, sticky="nsew")

        self._current_tab = 0
        self._raise_tab(0)

        self._build_light_tab()
        self._build_zone_tab()
        self._build_layout_tab()
        self._build_perkey_tab()
        self._build_info_tab()

        self.select_tab(0, animate=False)
        self.after(80, lambda: self._move_underline(0, animate=False))

    def _raise_tab(self, index):
        """只把当前页抬到最上层（其余页仍占网格，但不绘制）。"""
        try:
            self._tabs[index].tkraise()
        except Exception:
            return
        self._current_tab = index

    # 兼容旧调用点（select_tab / selftest 里用过 self.nb.index("current")）
    def _current_index(self):
        return self._current_tab

    def _tab_hover(self, widget, entering):
        idx = self._tab_widgets.index(widget)
        if idx == self._current_tab:
            return
        widget.configure(fg=FG if entering else FG_DIM)

    def select_tab(self, index, animate=True):
        if index < 0 or index >= len(self._tabs):
            return
        self._raise_tab(index)
        for i, w in enumerate(self._tab_widgets):
            w.configure(fg=FG if i == index else FG_DIM)
        self._move_underline(index, animate=animate)
        # 切页后重新量一次：表单型页签的内容高度在这期间可能已变化
        scroll = self._scrolls[index] if getattr(self, "_scrolls", None) else None
        if scroll is not None:
            self.after_idle(scroll.reset_scroll_region)
        if index == 1:
            self.refresh_zone_panel()

    def _underline_geometry(self, index):
        """下划线在 _chips 坐标系里的 (x, width)。"""
        try:
            holder_lbl = self._tab_holders[index]
            x = holder_lbl.winfo_x()
            width = holder_lbl.winfo_width()
            if width <= 1:
                width = holder_lbl.winfo_reqwidth()
        except Exception:
            return None
        return x, width

    def _move_underline(self, index, animate=True):
        if index >= len(self._tab_widgets):
            return
        geo = self._underline_geometry(index)
        if geo is None:
            return
        target_x, width = geo
        try:
            self._tab_underline.configure(width=max(width, 20))
        except Exception:
            pass
        if not animate:
            self._tab_underline.place(x=target_x)
            self._tab_underline_pos = target_x
            return
        self._glide(target_x)

    def _glide(self, target):
        start = self._tab_underline_pos
        delta = target - start
        if abs(delta) < 1:
            self._tab_underline.place(x=target)
            self._tab_underline_pos = target
            return
        steps = 6

        def run(i):
            pos = start + delta * (i / float(steps))
            try:
                self._tab_underline.place(x=pos)
            except Exception:
                return
            self._tab_underline_pos = pos
            if i < steps:
                self.after(14, lambda: run(i + 1))
            else:
                self._tab_underline.place(x=target)
                self._tab_underline_pos = target

        run(1)

    # ------------------------------------------------------------------
    # 基础构件
    # ------------------------------------------------------------------
    def _section(self, parent, title=None, subtitle=None):
        """Win11 卡片：返回 (外层容器, 卡片内容 Frame)。"""
        wrap = tk.Frame(parent, bg=BG)
        if title:
            head = tk.Frame(wrap, bg=BG)
            head.pack(fill="x", pady=(0, 6))
            tk.Label(head, text=title, bg=BG, fg=FG,
                     font=(UI_FONT, 10)).pack(side="left")
            if subtitle:
                tk.Label(head, text=subtitle, bg=BG, fg=FG_FAINT,
                         font=(UI_FONT, 8)).pack(side="left", padx=(PAD, 0),
                                                 pady=(2, 0))
        card = T.Card(wrap, padding=PAD_L)
        card.pack(fill="both", expand=True)
        return wrap, card.inner

    def _row(self, parent, label=None, label_width=6, bg=None):
        bg = bg or SURFACE
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=5)
        if label:
            tk.Label(row, text=label, bg=bg, fg=FG_DIM, width=label_width,
                     anchor="w", font=(UI_FONT, 9)).pack(side="left")
        return row

    def _divider(self, parent, bg=None):
        tk.Frame(parent, bg=STROKE, height=1).pack(fill="x", pady=8)

    # ------------------------------------------------------------------
    def _build_light_tab(self):
        outer = tk.Frame(self.tab_light.inner, bg=BG)
        outer.pack(fill="both", expand=True)

        # --- 全局轴灯 ---
        wrap, card = self._section(outer, "全局轴灯")
        wrap.pack(fill="x")

        row = self._row(card, "模式", label_width=6)
        T.FlatButton(row, "上一档", variant="standard", height=30,
                     command=lambda: self.step_effect(-1), bg=SURFACE).pack(
            side="left", padx=(6, 0))
        self.effect_var = tk.StringVar()
        self.effect_combo = ttk.Combobox(row, textvariable=self.effect_var, width=40,
                                         state="readonly")
        self.effect_combo.pack(side="left", padx=6)
        self.effect_combo.bind("<<ComboboxSelected>>", self._on_effect_combo)
        T.FlatButton(row, "下一档", variant="standard", height=30,
                     command=lambda: self.step_effect(1), bg=SURFACE).pack(side="left")
        self.effect_pos = tk.Label(row, text="—", bg=SURFACE, fg=FG_FAINT,
                                   font=(MONO_FONT, 9))
        self.effect_pos.pack(side="left", padx=(PAD + 4, 0))

        self.effect_hint = tk.Label(card, text="", bg=SURFACE, fg=FG_FAINT,
                                    anchor="w", font=(UI_FONT, 8), justify="left",
                                    wraplength=1000)
        self.effect_hint.pack(fill="x", pady=(0, 2))

        self._divider(card)

        self.s_bright, self.v_bright = self._scale(card, 0, 255, 255,
                                                   self._on_bright, "亮度")
        self.s_speed, self.v_speed = self._scale(card, 0, 255, 0,
                                                 self._on_speed, "速度")

        # --- 颜色 ---
        wrap2, card2 = self._section(outer, "颜色")
        wrap2.pack(fill="x", pady=(10, 0))

        hrow = self._row(card2, "色相")
        self.hue_strip = tk.Canvas(hrow, width=380, height=14, bg=SURFACE,
                                   highlightthickness=0, cursor="hand2")
        self.hue_strip.pack(side="left", padx=(6, 12))
        self._draw_hue_strip()
        self.hue_strip.bind("<Button-1>", self._on_hue_strip)
        self.hue_strip.bind("<B1-Motion>", self._on_hue_strip)
        self.hue_val = tk.Label(hrow, text="0", bg=SURFACE, fg=FG, width=4,
                                anchor="e", font=(MONO_FONT, 10))
        self.hue_val.pack(side="left")

        self.s_sat, self.v_sat = self._scale(card2, 0, 255, 255, self._on_sat,
                                             "饱和度")

        prow = self._row(card2)
        tk.Label(prow, text="取值", bg=SURFACE, fg=FG_DIM, width=6, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self.swatch = tk.Canvas(prow, width=88, height=28, bg=SURFACE,
                                highlightthickness=0, cursor="hand2")
        self.swatch.pack(side="left", padx=(6, 10))
        self.swatch.bind("<Button-1>", lambda e: self._pick_color())
        self.hex_entry = T.Entry(prow, width=104, text="#00ffff", bg=SURFACE,
                                 justify="center", on_submit=self._on_hex)
        self.hex_entry.pack(side="left")
        T.FlatButton(prow, "拾色", variant="standard", height=30, bg=SURFACE,
                     command=self._pick_color).pack(side="left", padx=(PAD, 0))

        tk.Frame(prow, bg=STROKE, width=1).pack(side="left", fill="y", padx=(12, 12))

        for name, hh, ss, vv in C.PALETTE:
            hx = C.hsv_to_hex(hh, ss, vv)
            b = tk.Canvas(prow, width=24, height=24, bg=SURFACE,
                          highlightthickness=0, cursor="hand2")
            b.pack(side="left", padx=3)
            b.create_oval(1, 1, 23, 23, fill=hx, outline=STROKE, tags="sw")
            b.bind("<Button-1>", lambda e, h=hh, s=ss, v=vv: self.apply_color(h, s, v))
            b.bind("<Enter>", lambda e, c=b: c.itemconfigure("sw", outline=FG))
            b.bind("<Leave>", lambda e, c=b: c.itemconfigure("sw", outline=STROKE))

        # --- 操作 ---
        wrap3, card3 = self._section(outer, "预设与操作")
        wrap3.pack(fill="x", pady=(10, 0))

        prow3 = tk.Frame(card3, bg=SURFACE)
        prow3.pack(fill="x", pady=(4, 4))
        tk.Label(prow3, text="预设", bg=SURFACE, fg=FG_DIM, font=(UI_FONT, 9)).pack(
            side="left", padx=(0, PAD))
        for name, mapping in effects.QUICK_PRESETS:
            T.FlatButton(prow3, name, variant="standard", height=30, bg=SURFACE,
                         command=lambda m=mapping: self.apply_preset(m)).pack(
                side="left", padx=(0, 6))

        arow = tk.Frame(card3, bg=SURFACE)
        arow.pack(fill="x", pady=(2, 4))
        tk.Label(arow, text="动作", bg=SURFACE, fg=FG_DIM, font=(UI_FONT, 9)).pack(
            side="left", padx=(0, PAD))

        T.FlatButton(arow, "关灯", variant="standard", height=30, bg=SURFACE,
                     command=lambda: self.set_bright(0)).pack(side="left", padx=(0, 6))
        T.FlatButton(arow, "最亮", variant="standard", height=30, bg=SURFACE,
                     command=lambda: self.set_bright(255)).pack(side="left", padx=(0, 6))
        T.FlatButton(arow, "从键盘读取", variant="standard", height=30, bg=SURFACE,
                     command=self.pull_from_device).pack(side="left", padx=(0, 6))

        self.live_var = tk.IntVar(value=1)
        T.Check(arow, "实时下发", self.live_var,
                command=self._on_live_toggle, bg=SURFACE).pack(
            side="left", padx=(PAD, 0))

        self.state_dirty_lbl = tk.Label(
            arow, text="", bg=SURFACE, fg=FG_FAINT, font=(UI_FONT, 8))
        self.state_dirty_lbl.pack(side="right", padx=(0, PAD))
        self.save_btn = T.FlatButton(
            arow, "保存到固件", variant="standard", height=30, bg=SURFACE,
            command=self.save_to_device)
        self.save_btn.pack(side="right", padx=(6, 0))
        self.apply_state_btn = T.FlatButton(
            arow, "应用到设备", variant="accent", height=30, bg=SURFACE,
            command=self.apply_state_to_device)
        self.apply_state_btn.pack(side="right")

        # --- 预设方案（一条方案 = 一整套灯光） ---
        wrap4, card4 = self._section(outer, "预设方案")
        wrap4.pack(fill="x", pady=(10, 0))

        top4 = tk.Frame(card4, bg=SURFACE)
        top4.pack(fill="x", pady=(2, 6))

        self._preset_name = T.Entry(top4, width=190, bg=SURFACE,
                                    text="", on_submit=self.preset_save)
        self._preset_name.pack(side="left")
        T.FlatButton(top4, "保存为新方案", variant="accent", height=32, bg=SURFACE,
                     command=self.preset_save).pack(side="left", padx=(6, 0))
        T.FlatButton(top4, "覆盖当前", variant="standard", height=32, bg=SURFACE,
                     command=self.preset_overwrite).pack(side="left", padx=(6, 0))
        T.FlatButton(top4, "应用", variant="standard", height=32, bg=SURFACE,
                     command=self.preset_apply).pack(side="left", padx=(12, 0))
        T.FlatButton(top4, "重命名", variant="standard", height=32, bg=SURFACE,
                     command=self.preset_rename).pack(side="left", padx=(6, 0))
        T.FlatButton(top4, "删除", variant="standard", height=32, bg=SURFACE,
                     command=self.preset_delete).pack(side="left", padx=(6, 0))

        body4 = tk.Frame(card4, bg=SURFACE)
        body4.pack(fill="both", expand=True)

        # 左侧：方案列表（自绘，跟主题一致；不用 tk.Listbox 那个老外观）
        self._preset_list = tk.Canvas(body4, bg=CARD, height=124,
                                      highlightthickness=0, bd=0)
        self._preset_list.pack(side="left", fill="both", expand=True)
        self._preset_list.bind("<Button-1>", self._on_preset_click)
        self._preset_list.bind("<Double-Button-1>", lambda e: self.preset_apply())
        self._preset_list.bind("<MouseWheel>", self._on_preset_wheel)
        self._preset_list.bind("<Motion>", self._on_preset_motion)
        self._preset_list.bind("<Leave>", lambda e: self._set_preset_hover(None))
        self._preset_list.bind("<Configure>", lambda e: self._draw_preset_list())

        # 右侧：详情 + 顺序调整
        side4 = tk.Frame(body4, bg=SURFACE)
        side4.pack(side="left", fill="y", padx=(PAD_L, 0))
        self._preset_detail = tk.Label(
            side4, text="", bg=SURFACE, fg=FG_FAINT, font=(UI_FONT, 8),
            justify="left", anchor="nw", wraplength=250)
        self._preset_detail.pack(fill="x")
        nav4 = tk.Frame(side4, bg=SURFACE)
        nav4.pack(fill="x", pady=(6, 0))
        T.FlatButton(nav4, "上移", variant="subtle", height=28, bg=SURFACE,
                     command=lambda: self.preset_move(-1)).pack(side="left")
        T.FlatButton(nav4, "下移", variant="subtle", height=28, bg=SURFACE,
                     command=lambda: self.preset_move(1)).pack(side="left",
                                                              padx=(6, 0))
        T.FlatButton(nav4, "复制", variant="subtle", height=28, bg=SURFACE,
                     command=self.preset_duplicate).pack(side="left", padx=(6, 0))

        self._preset_note = tk.Label(card4, text="", bg=SURFACE, fg=FG_FAINT,
                                     anchor="w", font=(UI_FONT, 8),
                                     justify="left", wraplength=1000)
        self._preset_note.pack(fill="x", pady=(6, 0))

        self._preset_sel = None
        self._preset_hover_row = None
        self._refresh_preset_list()

    def _draw_hue_strip(self):
        w = int(self.hue_strip["width"])
        h = int(self.hue_strip["height"])
        for i in range(w):
            hue = int(i * 255 / max(w - 1, 1))
            self.hue_strip.create_line(i, 0, i, h, fill=C.hsv_to_hex(hue, 255, 255))
        self.hue_strip.create_rectangle(0, 0, w - 1, h - 1, outline=STROKE)

    def _on_hue_strip(self, event):
        w = int(self.hue_strip["width"]) - 1
        h = int(max(0, min(event.x, w)) * 255 / max(w, 1))
        self.v_hue.set(h)
        self._on_hue(str(h))

    def _scale(self, parent, frm, to, value, command, label, width=380):
        """Win11 滑块 + 数字框（自绘）。"""
        row = self._row(parent, label)

        slider = T.Slider(row, frm, to, value, command, bg=SURFACE,
                          track_w=width, number_width=56)
        slider.pack(side="left", padx=(6, 0))
        return slider, slider.var

    # ------------------------------------------------------------------
    # 分区页
    # ------------------------------------------------------------------
    def _build_zone_tab(self):
        outer = tk.Frame(self.tab_zone.inner, bg=BG)
        outer.pack(fill="both", expand=True)

        # --- 能力说明 ---
        wrap, card = self._section(outer, "分区控制",
                                   "选择编辑目标：全局轴灯、配件灯批量或当前灯条")
        wrap.pack(fill="x")

        self.zone_notice = tk.Label(card, text="尚未连接设备", bg=SURFACE,
                                    fg=FG_FAINT, anchor="w", justify="left",
                                    font=(UI_FONT, 9), wraplength=1020)
        self.zone_notice.pack(fill="x", pady=(0, 8))

        mrow = tk.Frame(card, bg=SURFACE)
        mrow.pack(fill="x", pady=(0, 4))
        tk.Label(mrow, text="作用范围", bg=SURFACE, fg=FG_DIM, width=6, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self.zone_seg = T.Segmented(
            mrow,
            [("both", "两侧可编辑"), ("key", "仅轴灯"), ("acc", "仅配件灯")],
            value="both", command=self._on_zone_mode, bg=SURFACE)
        self.zone_seg.pack(side="left", padx=(6, 0))

        self.zone_hint = tk.Label(card, text="", bg=SURFACE, fg=FG_FAINT, anchor="w",
                                  justify="left", font=(UI_FONT, 8), wraplength=1020)
        self.zone_hint.pack(fill="x", pady=(4, 0))

        # --- 配件灯条选择（仅 AMK 后端会显示）---
        self._strip_bar = tk.Frame(card, bg=SURFACE)
        self._strip_bar.pack(fill="x", pady=(10, 0))

        sr1 = tk.Frame(self._strip_bar, bg=SURFACE)
        sr1.pack(fill="x", pady=(0, 6))
        tk.Label(sr1, text="配件灯条", bg=SURFACE, fg=FG_DIM, width=8,
                 anchor="w", font=(UI_FONT, 9)).pack(side="left")
        self._strip_seg = T.Segmented(
            sr1, [("0", "—")], value="0", command=self._on_strip_pick,
            bg=SURFACE)
        self._strip_seg.pack(side="left", padx=(6, 0))

        sr_color = tk.Frame(self._strip_bar, bg=SURFACE)
        sr_color.pack(fill="x", pady=(0, 6))
        tk.Label(sr_color, text="当前颜色", bg=SURFACE, fg=FG_DIM, width=8,
                 anchor="w", font=(UI_FONT, 9)).pack(side="left")
        self._strip_color_swatch = tk.Canvas(
            sr_color, width=38, height=24, bg=SURFACE,
            highlightthickness=0, cursor="hand2")
        self._strip_color_swatch.pack(side="left", padx=(6, 8))
        self._strip_color_swatch.bind(
            "<Button-1>", lambda e: self._pick_strip_color())
        self._strip_color_hex = T.Entry(
            sr_color, width=96, text="#00ffff", bg=SURFACE,
            justify="center", on_submit=self._on_strip_hex)
        self._strip_color_hex.pack(side="left", padx=(0, 8))
        self._strip_color_pick = T.FlatButton(
            sr_color, "拾色", variant="subtle", height=30, bg=SURFACE,
            command=self._pick_strip_color)
        self._strip_color_pick.pack(side="left")

        sr2 = tk.Frame(self._strip_bar, bg=SURFACE)
        sr2.pack(fill="x")
        tk.Label(sr2, text="灯效", bg=SURFACE, fg=FG_DIM, width=8, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self._strip_mode_var = tk.StringVar()
        self._strip_mode_combo = ttk.Combobox(
            sr2, textvariable=self._strip_mode_var, width=26, state="readonly")
        self._strip_mode_combo.pack(side="left", padx=(6, 8))
        self._strip_mode_combo.bind("<<ComboboxSelected>>",
                                    lambda e: self._apply_strip_mode())
        self._strip_apply = T.FlatButton(
            sr2, "应用到该灯条", variant="accent", height=30, bg=SURFACE,
            command=self._apply_strip_mode)
        self._strip_apply.pack(side="left")
        self._strip_all = T.FlatButton(
            sr2, "全部灯条同步", variant="subtle", height=30, bg=SURFACE,
            command=self._apply_strip_mode_all)
        self._strip_all.pack(side="left", padx=(6, 0))
        self._strip_info = tk.Label(
            sr2, text="", bg=SURFACE, fg=FG_FAINT, font=(MONO_FONT, 8))
        self._strip_info.pack(side="right")

        # 第 3 行：亮度 / 速度。这两项在本固件上只能通过「逐灯写」实现，
        # 而逐灯写只在 Custom 档被采用 —— 非 Custom 档时会置灰。
        sr3 = tk.Frame(self._strip_bar, bg=SURFACE)
        sr3.pack(fill="x", pady=(6, 0))
        tk.Label(sr3, text="亮度", bg=SURFACE, fg=FG_DIM, width=8, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self._strip_bright_sld = T.Slider(
            sr3, 0, 255, 255, self._on_strip_bright, bg=SURFACE,
            track_w=170, number_width=46)
        self._strip_bright_sld.pack(side="left", padx=(6, 10))
        tk.Label(sr3, text="速度", bg=SURFACE, fg=FG_DIM, width=4, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        self._strip_speed_sld = T.Slider(
            sr3, 0, effects.STRIP_LED_SPEED_MAX, 8, self._on_strip_speed,
            bg=SURFACE, track_w=120, number_width=38)
        self._strip_speed_sld.pack(side="left", padx=(6, 0))

        self._strip_note = tk.Label(
            self._strip_bar, text="", bg=SURFACE, fg=FG_FAINT, anchor="w",
            justify="left", font=(UI_FONT, 8), wraplength=1020)
        self._strip_note.pack(fill="x", pady=(4, 0))
        self._strip_bar.pack_forget()

        # --- 两组灯并排（更接近 Win11 的设置卡片排布）---
        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure(0, weight=1, uniform="zone")
        row.columnconfigure(1, weight=1, uniform="zone")

        self._zone_cards = {}
        for col, zone in enumerate(("key", "acc")):
            zw, zc = self._build_zone_group(row, zone)
            zw.grid(row=0, column=col, sticky="nsew",
                    padx=(0, 6) if col == 0 else (6, 0))
            self._zone_cards[zone] = (zw, zc)

        # --- 底部动作 ---
        wrap3, card3 = self._section(outer, "动作")
        wrap3.pack(fill="x", pady=(10, 0))

        arow = tk.Frame(card3, bg=SURFACE)
        arow.pack(fill="x", pady=4)
        tk.Label(arow, text="推送", bg=SURFACE, fg=FG_DIM, font=(UI_FONT, 9)).pack(
            side="left", padx=(0, PAD))

        b_key = T.FlatButton(arow, "推送轴灯", variant="standard", height=30,
                             bg=SURFACE, command=lambda: self.push_zone("key"))
        b_key.pack(side="left", padx=(0, 6))
        b_acc = T.FlatButton(arow, "推送配件灯", variant="standard", height=30,
                             bg=SURFACE, command=lambda: self.push_zone("acc"))
        b_acc.pack(side="left", padx=(0, 6))
        b_all = T.FlatButton(arow, "全部推送", variant="accent", height=30,
                             bg=SURFACE, command=lambda: self.push_zone(None))
        b_all.pack(side="left", padx=(0, 6))

        b_swap = T.FlatButton(arow, "两侧互换", variant="subtle", height=30,
                              bg=SURFACE, command=self._swap_zones)
        b_swap.pack(side="right")
        b_sync = T.FlatButton(arow, "从键盘读取", variant="subtle", height=30,
                              bg=SURFACE,
                              command=self._sync_zone_colours_from_device)
        b_sync.pack(side="right", padx=(0, 6))

        # 早期以为固件整组轴灯写色"漏掉了"全局 0 与 74 两颗真实灯，于是加了
        # 一个「修复轴灯漏灯」按钮。逐颗打断测试证明那是误判：0 是幽灵灯、
        # 74 是 Caps Lock 指示灯，键位轴灯根本不由逐灯通道驱动。
        # 这里保留按钮但改成「轴灯通道诊断」，避免继续误导。
        b_fix = T.FlatButton(arow, "轴灯通道诊断", variant="subtle", height=30,
                             bg=SURFACE, command=self.diagnose_axis_leds)
        b_fix.pack(side="right", padx=(0, 6))

        self._zone_action_btns = [b_key, b_acc, b_all, b_swap, b_sync, b_fix]

    def diagnose_axis_leds(self):
        """打印真实的 LED 地址地图与矩阵通道状态。

        真机实测（逐颗打断法）结论：

        * 全局 ``0``      = 可读写但不驱动任何灯的**幽灵灯**；
        * 全局 ``1–73``   = 74 颗键位轴灯（由板载灯效引擎渲染）；
        * 全局 ``74``     = **Caps Lock 指示灯**（键盘定义里明写）；
        * 全局 ``75–92``  = 5 条配件灯条；
        * 全局 ``93+``    = 读不到。

        **并且：键位轴灯是可以逐键上色的** —— 前提是先切到矩阵的
        「逐灯自定义」档（``matrix_mode()["custom"]``，本机 = 45）。
        切档之后用 ``SET_RGB_MATRIX_LED(48)`` 逐颗写 HSV 就能生效；
        在非自定义档下固件自己渲染灯效，写进去的颜色会被无视。
        """
        if self.dev is None:
            self.log("尚未连接设备", "warn")
            return
        if self.dev.lighting_backend != "amk":
            self.log("该诊断仅适用于 AMK 通道（本机固件）", "warn")
            return
        try:
            info = self.dev.matrix_info() or {}
            ghost = self.dev.ghost_led_indexes()
            inds = self.dev.indicator_indexes()
            mm = self.dev.matrix_mode() or {}
            in_custom = self.dev.matrix_in_custom()
            ok_perkey = self.dev.perkey_supported()
            n_map = len(self.dev.matrix_rc_map())
        except Exception as exc:
            self.log("轴灯通道诊断失败: %s" % exc, "err")
            return
        self.log("轴灯颗数 %s；矩阵可映射灯数 %s" % (info.get("count"), n_map), "info")
        self.log("幽灵灯（可读写但无灯）: %s；指示灯（非轴灯）: %s"
                 % (ghost or "无", inds or "无"), "info")
        self.log("矩阵灯效模式: %s" % (mm,), "info")
        self.log("当前在自定义档: %s；可逐键上色: %s"
                 % (in_custom, ok_perkey), "ok" if ok_perkey else "warn")
        if ok_perkey:
            self.log("结论：键位轴灯**可以逐键上色**。请在「逐键」页签取色后"
                     "点按键盘图上色，或先点「进入自定义档」再操作。", "ok")
        else:
            self.log("结论：当前固件/通道不支持逐键上色。", "warn")

    def _build_zone_group(self, parent, zone):
        label = {
            "key": "轴灯（全局）",
            "acc": "配件灯（批量）",
        }.get(zone, ZONE_LABELS.get(zone, zone))
        tint = ZONE_TINT.get(zone, ACCENT)

        wrap, card = self._section(parent, None)

        head = tk.Frame(card, bg=SURFACE)
        head.pack(fill="x", pady=(0, 8))

        dot = tk.Canvas(head, width=10, height=10, bg=SURFACE,
                        highlightthickness=0)
        dot.pack(side="left", pady=(4, 0))
        dot.create_oval(0, 0, 9, 9, fill=tint, outline="")

        tk.Label(head, text=label, bg=SURFACE, fg=FG, font=(UI_FONT, 10)).pack(
            side="left", padx=(8, 0))

        count_lbl = tk.Label(head, text="—", bg=SURFACE, fg=FG_FAINT,
                             font=(MONO_FONT, 9))
        count_lbl.pack(side="left", padx=(PAD, 0), pady=(2, 0))

        # 色块紧跟在标题后面，比悬浮在右上角更符合 Win11 的排布
        swatch = tk.Canvas(head, width=30, height=20, bg=SURFACE,
                           highlightthickness=0, cursor="hand2")
        swatch.pack(side="right")
        swatch.bind("<Button-1>", lambda e, z=zone: self._pick_zone_color(z))

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="颜色", bg=SURFACE, fg=FG_DIM, width=6, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")

        hexbox = T.Entry(row, width=96, text="#00ffff", bg=SURFACE,
                         justify="center",
                         on_submit=lambda z=zone: self._on_zone_hex(z))
        hexbox.pack(side="left", padx=(6, 8))

        pick_btn = T.FlatButton(row, "拾色", variant="subtle", height=30,
                                bg=SURFACE,
                                command=lambda z=zone: self._pick_zone_color(z))
        pick_btn.pack(side="left")

        # 快捷色换到第二行，避免和输入框抢宽度
        srow = tk.Frame(card, bg=SURFACE)
        srow.pack(fill="x", pady=(0, 6))
        swatch_dots = []
        for name, hh, ss, vv in C.PALETTE:
            hx = C.hsv_to_hex(hh, ss, vv)
            b = tk.Canvas(srow, width=24, height=24, bg=SURFACE,
                          highlightthickness=0, cursor="hand2")
            b.pack(side="left", padx=(0, 6))
            b.create_oval(1, 1, 23, 23, fill=hx, outline=STROKE, tags="sw")
            b.bind("<Button-1>", lambda e, z=zone, h=hh, s=ss, v=vv:
                   self._set_zone_colour(z, (h, s, v)))
            b.bind("<Enter>", lambda e, c=b: c.itemconfigure("sw", outline=FG))
            b.bind("<Leave>", lambda e, c=b: c.itemconfigure("sw", outline=STROKE))
            swatch_dots.append((b, hx))

        # 亮度
        brow = tk.Frame(card, bg=SURFACE)
        brow.pack(fill="x", pady=(0, 6))
        tk.Label(brow, text="亮度", bg=SURFACE, fg=FG_DIM, width=6, anchor="w",
                 font=(UI_FONT, 9)).pack(side="left")
        slider = T.Slider(brow, 0, 255, 255,
                          lambda v, z=zone: self._on_zone_bright(z, v),
                          bg=SURFACE, track_w=250, number_width=52)
        slider.pack(side="left", padx=(6, 0))

        info = tk.Label(card, text="", bg=SURFACE, fg=FG_FAINT, anchor="w",
                        justify="left", font=(UI_FONT, 8), wraplength=460)
        info.pack(fill="x")

        self._zone_cards_data = getattr(self, "_zone_cards_data", {})
        self._zone_cards_data[zone] = {
            "swatch": swatch, "hex": hexbox, "slider": slider,
            "count": count_lbl, "info": info, "card": card,
            "buttons": [pick_btn], "dots": swatch_dots,
        }
        return wrap, card

    # ---- 分区逻辑 ----------------------------------------------------
    def refresh_zone_panel(self):
        """根据设备能力刷新整个分区页。"""
        zones = ()
        reason = "尚未连接设备"
        counts = {"key": 0, "acc": 0}

        if self.dev is not None:
            try:
                zones, reason = self.dev.zone_support()
            except Exception as exc:
                zones, reason = (), "分区能力检测失败: %s" % exc
            if zones:
                try:
                    counts = self.dev.zone_counts()
                except Exception:
                    counts = {"key": 0, "acc": 0}

        self._zone_capable = tuple(zones)
        self._zone_reason = reason
        self._zone_counts = counts

        capable = len(self._zone_capable) >= 2
        for z in ("key", "acc"):
            data = self._zone_cards_data.get(z)
            if not data:
                continue
            data["count"].configure(text="%d 颗" % counts.get(z, 0))

        if capable:
            self.zone_notice.configure(
                text="本设备支持分区控制：%s" % reason,
                fg=OK)
            self.zone_hint.configure(text=self._zone_hint_for(self.dev))
            self._refresh_strip_bar()
        elif self.dev is None:
            self.zone_notice.configure(text=reason, fg=FG_FAINT)
            self.zone_hint.configure(text="")
        else:
            detail = "本设备不支持分区控制 —— %s。" % reason
            self.zone_notice.configure(text=detail, fg=WARN, font=(UI_FONT, 9))
            self.zone_hint.configure(
                text="分区页的控件在此设备上已置灰，不会生效。")

        for z in ("key", "acc"):
            self._refresh_zone_widgets(z)

        self._update_zone_enabled()
        self._refresh_zone_buttons()

    def _update_zone_enabled(self):
        capable = len(self._zone_capable) >= 2
        mode = self._zone_mode.get()
        has_dev = self.dev is not None
        zone_ok = capable and has_dev
        # 作用范围分段控件在整机不支持时也要置灰，否则看起来能点、点了没反应
        try:
            self.zone_seg.set_enabled(zone_ok)
        except Exception:
            pass
        for z in ("key", "acc"):
            zw = self._zone_cards.get(z)
            if not zw:
                continue
            active = zone_ok and (mode == "both" or mode == z)
            data = self._zone_cards_data.get(z)
            if not data:
                continue
            # 置灰：不可用时整个卡片降低视觉权重，并禁用交互
            data["slider"].configure(cursor="hand2" if active else "arrow")
            data["slider"].set_enabled(active)
            data["hex"].set_enabled(active)
            for btn in data.get("buttons", []):
                btn.set_enabled(active)
            for dot, base in data.get("dots", []):
                # 不可用时色点变成「灰底灰点」，一眼看出点不动
                dot.configure(cursor="hand2" if active else "arrow")
                dot.itemconfigure("sw", fill=base if active else "#dddddd")
            try:
                data["swatch"].configure(
                    cursor="hand2" if active else "arrow")
            except Exception:
                pass
            data["count"].configure(fg=FG if active else FG_DIS)
            data["info"].configure(fg=FG_FAINT)

        # 配件灯条那一排：模式始终可选；颜色 / 亮度 / 速度只在 Custom 档可写
        acc_on = zone_ok and mode in ("both", "acc") and bool(self._amk_strips())
        seg = getattr(self, "_strip_seg", None)
        if seg is not None:
            seg.set_enabled(acc_on)
        for name in ("_strip_apply", "_strip_all"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.set_enabled(acc_on)
        combo = getattr(self, "_strip_mode_combo", None)
        if combo is not None:
            combo.configure(state="readonly" if acc_on else "disabled")
        editable = acc_on and self._strip_editable()
        color_hex = getattr(self, "_strip_color_hex", None)
        if color_hex is not None:
            color_hex.set_enabled(editable)
        color_pick = getattr(self, "_strip_color_pick", None)
        if color_pick is not None:
            color_pick.set_enabled(editable)
        color_swatch = getattr(self, "_strip_color_swatch", None)
        if color_swatch is not None:
            color_swatch.configure(cursor="hand2" if editable else "arrow")
        for name in ("_strip_bright_sld", "_strip_speed_sld"):
            sl = getattr(self, name, None)
            if sl is not None:
                sl.set_enabled(editable)

    def _refresh_zone_buttons(self):
        """分区页底部动作按钮的可用性。"""
        capable = len(self._zone_capable) >= 2 and self.dev is not None
        for btn in getattr(self, "_zone_action_btns", []):
            btn.set_enabled(capable)

    def _refresh_zone_widgets(self, zone):
        data = self._zone_cards_data.get(zone)
        if not data:
            return
        h, s, v = self._zone_colors.get(zone, (0, 0, 255))
        capable = len(self._zone_capable) >= 2 and self.dev is not None
        active = self._zone_active(zone)
        # 不可用时把颜色降成中性灰，让「这侧现在改不动」一眼可见
        hx = C.hsv_to_hex(h, s, v) if capable else C.hsv_to_hex(0, 0, 214)
        cv = data["swatch"]
        cv.delete("all")
        r = T.rounded_points(0.5, 0.5, 29.5, 19.5, 4)
        cv.create_polygon(r, fill=hx, outline=STROKE, smooth=True,
                          splinesteps=10)
        if data["hex"].get() != hx:
            data["hex"].set(hx)
        if data["slider"].get() != int(v):
            data["slider"].set(int(v))
        if not capable:
            data["info"].configure(text="（此设备不支持分区控制，颜色 / 亮度只作存档）")
        else:
            scope = ""
            if zone == "acc" and self.dev.lighting_backend == "amk":
                scope = "　（应用到全部灯条）"
            data["info"].configure(
                text=("H %d · S %d · V %d%s" % (h, s, v, scope)
                      if active else
                      "H %d · S %d · V %d　（此侧未被当前作用范围选中）"
                      % (h, s, v)))

    def _zone_active(self, zone):
        if len(self._zone_capable) < 2 or self.dev is None:
            return False
        mode = self._zone_mode.get()
        return mode == "both" or mode == zone

    def _on_zone_mode(self, mode):
        self._zone_mode.set(mode)
        self._update_zone_enabled()
        for z in ("key", "acc"):
            self._refresh_zone_widgets(z)
        blocked = self._zone_blocked()
        if blocked:
            self.log("作用范围已切换为「%s」，但%s" % (
                {"both": "两侧可编辑", "key": "仅轴灯",
                 "acc": "仅配件灯"}.get(mode, mode),
                blocked), "warn")
            return
        self.log("分区编辑范围：%s" % {
            "both": "两侧可编辑（单次修改仍只提交当前侧）",
            "key": "仅轴灯",
            "acc": "仅配件灯",
        }.get(mode, mode), "ok")

    def _zone_blocked(self):
        """分区不可用时统一给一句人话，而不是静默无反应。"""
        if self.dev is None:
            return "未连接设备"
        if len(self._zone_capable) < 2:
            return "此固件不支持分区控制：%s" % (
                self._zone_reason or "无独立寻址接口")
        return None

    def _zone_hint_for(self, dev):
        """分区页顶部提示语（按后端给不同的操作说明）。"""
        if dev is not None and dev.lighting_backend == "amk":
            return ("灯光页负责轴灯的全局灯效 / 速度；这里的轴灯卡片只改同一组"
                    "轴灯的颜色 / 亮度。配件灯批量卡片会把颜色 / 亮度应用到"
                    "全部灯条；上方灯条栏则只编辑当前选中的一条灯条。只有"
                    "Custom 模式支持自定义颜色、亮度和速度。")
        return ("两侧卡片分别写入对应 LED 子集；当前编辑范围只决定哪些卡片可改，"
                "单次修改不会自动提交另一侧。VialRGB 分区写入会切换全局灯效为 Direct。")

    def _set_zone_colour(self, zone, colour):
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return
        self._zone_colors[zone] = tuple(colour)
        self._zone_val[zone] = int(colour[2])
        self._refresh_zone_widgets(zone)
        if self._zone_active(zone):
            self.push_zone(zone)

    def _on_zone_bright(self, zone, value):
        if self._zone_blocked():
            return
        h, s, _v = self._zone_colors.get(zone, (0, 0, 255))
        self._zone_colors[zone] = (h, s, int(value))
        self._zone_val[zone] = int(value)
        self._refresh_zone_widgets(zone)
        if self._zone_active(zone):
            self.push_zone(zone)

    def _on_zone_hex(self, zone):
        data = self._zone_cards_data.get(zone)
        if not data:
            return
        try:
            r, g, b = C.hex_to_rgb(data["hex"].get())
        except ValueError as exc:
            self.log(str(exc), "err")
            return
        h, s, v = C.rgb_to_hsv(r, g, b)
        self._set_zone_colour(zone, (h, s, v))

    def _pick_zone_color(self, zone):
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return
        h, s, v = self._zone_colors.get(zone, (0, 0, 255))
        rgb, _ = colorchooser.askcolor(color=C.hsv_to_hex(h, s, v))
        if not rgb:
            return
        r, g, b = [int(x) for x in rgb]
        self._set_zone_colour(zone, C.rgb_to_hsv(r, g, b))

    def _swap_zones(self):
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return
        self._zone_colors["key"], self._zone_colors["acc"] = (
            self._zone_colors["acc"], self._zone_colors["key"])
        self._zone_val["key"], self._zone_val["acc"] = (
            self._zone_colors["key"][2], self._zone_colors["acc"][2])
        for z in ("key", "acc"):
            self._refresh_zone_widgets(z)
        self.push_zone(None)
        self.log("已互换两侧颜色")

    def _sync_zone_colours_from_leds(self):
        """从逐键缓冲区反推两侧代表色（用出现最多的颜色）。"""
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return
        if not self.leds or not self._led_colors:
            self.log("还没有逐键颜色数据", "warn")
            return
        for zone, group in (("key", [l for l in self.leds if l.zone == "key"]),
                            ("acc", [l for l in self.leds if l.zone == "acc"])):
            if not group:
                continue
            tally = {}
            for led in group:
                col = self._led_colors[led.index]
                tally[col] = tally.get(col, 0) + 1
            best = max(tally.items(), key=lambda kv: kv[1])[0]
            self._zone_colors[zone] = tuple(best)
            self._zone_val[zone] = int(best[2])
            self._refresh_zone_widgets(zone)
        self.log("已按逐键页当前配色回填两侧代表色", "ok")

    def _sync_zone_colours_from_device(self):
        """从设备实际可回读的通道同步两侧代表色。"""
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return
        if self.dev.lighting_backend == "amk":
            self.pull_from_device()
            if self.state is not None:
                self._zone_colors["key"] = (
                    self.state.hue, self.state.sat, self.state.val)
                self._zone_val["key"] = self.state.val

            tally = {}
            for strip in self._amk_strips():
                try:
                    led = self.dev.read_strip_led(strip.start)
                except Exception:
                    led = None
                if led is not None:
                    colour = (led.hue, led.sat, led.val)
                    tally[colour] = tally.get(colour, 0) + 1
            if tally:
                colour = max(tally.items(), key=lambda item: item[1])[0]
                self._zone_colors["acc"] = colour
                self._zone_val["acc"] = colour[2]
            for zone in ("key", "acc"):
                self._refresh_zone_widgets(zone)
            self._refresh_strip_bar()
            self.log("已从 AMK 设备状态同步轴灯和配件灯代表色", "ok")
            return

        if self.dev.lighting_backend == "vialrgb":
            self.pull_from_device()
            self.log("VialRGB 协议不能回读逐灯颜色，未修改分区代表色", "warn")
            return

        self.log("当前灯光后端没有可回读的分区颜色通道", "warn")

    # ---- 配件灯条（AMK 后端专用）-------------------------------------
    def _amk_strips(self):
        """当前设备的配件灯条列表；非 AMK 或读不到时返回 ``[]``。"""
        if self.dev is None or self.dev.lighting_backend != "amk":
            return []
        try:
            return self.dev.strips()
        except Exception as exc:
            self.log("读取配件灯条失败: %s" % exc, "warn")
            return []

    def _refresh_strip_bar(self):
        """同步配件灯条选择器与灯效下拉。"""
        strips = self._amk_strips()
        bar = getattr(self, "_strip_bar", None)
        if bar is None:
            return
        if not strips:
            self._strip_index = None
            self._strip_sel = None
            bar.pack_forget()
            self._strip_seg.set_options([("", "—")])
            self._strip_mode_combo.configure(values=[])
            self._strip_mode_combo.set("")
            self._strip_info.configure(text="")
            self._strip_note.configure(text="")
            if self._strip_color_hex is not None:
                self._strip_color_hex.set("#000000")
            if self._strip_color_swatch is not None:
                self._strip_color_swatch.delete("all")
            return
        if not bar.winfo_ismapped():
            bar.pack(fill="x", pady=(10, 0))

        # 选中项：没选过 / 选中的灯条已消失 → 回到第一条
        valid = [s.index for s in strips]
        if self._strip_index not in valid:
            self._strip_index = valid[0]
        opts = [(str(s.index), "灯条 %d（%d 颗）" % (s.index + 1, s.count))
                for s in strips]
        self._strip_seg.set_options(opts, value=str(self._strip_index))

        items = ["%d   %s" % (mid, zh) for mid, _en, zh in effects.STRIP_EFFECTS]
        self._strip_mode_combo.configure(values=items)
        s = strips[valid.index(self._strip_index)]
        if 0 <= s.mode < len(effects.STRIP_EFFECTS):
            self._strip_mode_combo.current(s.mode)
        else:
            self._strip_mode_combo.set("")

        # 逐颗读一次，统计「亮着的」和实际色值，方便一眼看出卡死状态
        lit = 0
        colors = set()
        first_led = None
        for i in range(s.count):
            led = None
            try:
                led = self.dev.read_strip_led(s.start + i)
            except Exception:
                pass
            if led is None:
                continue
            if first_led is None:
                first_led = led
            if led.on and led.val > 0:
                lit += 1
                colors.add((led.hue, led.sat, led.val))
        if first_led is not None:
            # 用首颗灯作为当前灯条的代表值；右侧统计仍会提示是否混色。
            s.hue, s.sat, s.val = (
                first_led.hue, first_led.sat, first_led.val)
            s.speed = first_led.speed
        self._strip_sel = s
        self._strip_bright_sld.set(int(s.val))
        self._strip_speed_sld.set(int(s.speed))
        if self._strip_color_hex is not None:
            hx = C.hsv_to_hex(s.hue, s.sat, s.val)
            self._strip_color_hex.set(hx)
            if self._strip_color_swatch is not None:
                self._strip_color_swatch.delete("all")
                pts = T.rounded_points(0.5, 0.5, 37.5, 23.5, 4)
                self._strip_color_swatch.create_polygon(
                    pts, fill=hx, outline=STROKE, smooth=True,
                    splinesteps=8)
        note = "%d/%d 颗亮着" % (lit, s.count)
        if len(colors) == 1:
            h, sa, v = colors.pop()
            note += " · 同色 H%d S%d V%d" % (h, sa, v)
        elif len(colors) > 1:
            note += " · %d 种颜色" % len(colors)
        self._strip_info.configure(text=note)

        # 关键提示：只有 Custom 档下，颜色 / 亮度 / 速度才由我们说了算
        editable = effects.strip_mode_editable(s.mode)
        if editable:
            self._strip_note.configure(
                text="当前灯条是「自定义（逐灯上色）」档：上方颜色、亮度、"
                     "速度只作用于当前灯条，改完立刻生效。", fg=FG_FAINT)
        else:
            en, zh = effects.strip_effect_name(s.mode)
            self._strip_note.configure(
                text="当前是「%d %s」档：这一档的画面由固件自己渲染，"
                     "颜色、亮度、速度控件已禁用。想自己配色请先切到"
                     "「0 自定义（逐灯上色）」档。"
                     % (s.mode, zh), fg=WARN)
        self._update_zone_enabled()

    def _strip_editable(self):
        """当前选中的灯条是否处在可写颜色的档位。"""
        s = getattr(self, "_strip_sel", None)
        if s is None:
            strips = self._amk_strips()
            if not strips:
                return False
            s = strips[0]
        return effects.strip_mode_editable(s.mode)

    def _on_strip_hex(self, _event=None):
        try:
            r, g, b = C.hex_to_rgb(self._strip_color_hex.get())
        except ValueError as exc:
            self.log(str(exc), "err")
            return
        self._set_strip_color(C.rgb_to_hsv(r, g, b))

    def _pick_strip_color(self):
        s = self._selected_strip()
        if s is None:
            return
        if not self._strip_editable():
            self.log("当前灯效不是 Custom，不能修改自定义颜色", "warn")
            return
        rgb, _ = colorchooser.askcolor(
            color=C.hsv_to_hex(s.hue, s.sat, s.val))
        if not rgb:
            return
        r, g, b = [int(x) for x in rgb]
        self._set_strip_color(C.rgb_to_hsv(r, g, b))

    def _set_strip_color(self, colour):
        s = self._selected_strip()
        if s is None:
            return
        if not self._strip_editable():
            self.log("当前灯效不是 Custom，不能修改自定义颜色", "warn")
            return
        h, sat, val = [int(x) for x in colour]
        try:
            ok = self.dev.set_strip_color(
                s, h, sat, val, on=val > 0, force=False)
        except Exception as exc:
            self.log("设置当前灯条颜色失败: %s" % exc, "err")
            return
        if not ok:
            self.log("当前灯条颜色写入失败", "err")
            return
        self.log("灯条 %d 颜色已更新（仅当前灯条）" % (s.index + 1), "ok")
        self._refresh_strip_bar()

    def _on_strip_bright(self, value):
        s = self._selected_strip()
        if s is None:
            return
        if not self._strip_editable():
            self.log("当前灯效不是 Custom，不能修改亮度", "warn")
            return
        # 用当前灯条的色相 / 饱和度改写整条灯条。
        try:
            ok = self.dev.set_strip_color(
                s, s.hue, s.sat, int(value), on=int(value) > 0, force=False)
        except Exception as exc:
            self.log("设置配件灯亮度失败: %s" % exc, "err")
            return
        if not ok:
            self.log("设置配件灯亮度失败", "err")
            return
        self.log("配件灯条 %d 亮度 = %d（逐灯写）" % (s.index + 1, int(value)), "ok")
        self._refresh_strip_bar()

    def _on_strip_speed(self, value):
        s = self._selected_strip()
        if s is None:
            return
        if not self._strip_editable():
            self.log("当前灯效不是 Custom，不能修改速度", "warn")
            return
        try:
            ok = self.dev.set_strip_led_speed(s, int(value))
        except Exception as exc:
            self.log("设置配件灯速度失败: %s" % exc, "err")
            return
        if not ok:
            self.log("设置配件灯速度失败", "err")
            return
        self.log("配件灯条 %d 速度 = %d（逐灯写，仅自定义档有效）"
                 % (s.index + 1, int(value)), "ok")
        self._refresh_strip_bar()

    def _on_strip_pick(self, value):
        try:
            self._strip_index = int(value)
        except (TypeError, ValueError):
            return
        self._refresh_strip_bar()

    def _apply_strip_mode(self):
        s = self._selected_strip()
        if s is None:
            return
        idx = self._strip_mode_combo.current()
        if idx < 0:
            self.log("请先选一个灯效", "warn")
            return
        mode = effects.STRIP_EFFECTS[idx][0]
        try:
            ok = self.dev.set_strip_mode(s, mode)
        except Exception as exc:
            self.log("设置灯条灯效失败: %s" % exc, "err")
            return
        if not ok:
            self.log("固件拒绝了这次灯效写入", "err")
            return
        zh = effects.strip_effect_name(mode)[1]
        self.log("灯条 %d 已切到灯效 %d（%s）" % (s.index + 1, mode, zh), "ok")
        self._refresh_strip_bar()

    def _apply_strip_mode_all(self):
        strips = self._amk_strips()
        if not strips:
            return
        idx = self._strip_mode_combo.current()
        if idx < 0:
            self.log("请先选一个灯效", "warn")
            return
        mode = effects.STRIP_EFFECTS[idx][0]
        done = 0
        for s in strips:
            try:
                if self.dev.set_strip_mode(s, mode):
                    done += 1
            except Exception as exc:
                self.log("灯条 %d 写入失败: %s" % (s.index + 1, exc), "err")
        zh = effects.strip_effect_name(mode)[1]
        self.log("已把灯效 %d（%s）套用到 %d/%d 条灯条"
                 % (mode, zh, done, len(strips)), "ok")
        self._refresh_strip_bar()

    def _selected_strip(self):
        strips = self._amk_strips()
        if not strips:
            self.log("此设备没有可独立控制的配件灯条", "warn")
            return None
        if self._strip_index is None:
            self._strip_index = strips[0].index
        for strip in strips:
            if strip.index == self._strip_index:
                return strip
        self._strip_index = strips[0].index
        return strips[0]

    def push_zone(self, zone):
        """推送某一侧（``None`` = 按当前作用范围推）。"""
        blocked = self._zone_blocked()
        if blocked:
            self.log(blocked, "warn")
            return

        mode = self._zone_mode.get()
        if zone is None:
            targets = list(self._zone_capable) if mode == "both" else [mode]
        else:
            targets = [zone]

        if self.dev.lighting_backend == "amk":
            self._push_zone_amk(targets)
            return

        if not self.leds:
            self.log("没有逐键 LED 数据，无法分区推送", "warn")
            return

        try:
            # 分区推送基于「逐键直接控制」模式
            if self.state is None:
                self.state = self.dev.read_lighting()
            if self.state.effect != effects.VIALRGB_DIRECT:
                self.dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
                self.state.effect = effects.VIALRGB_DIRECT
                ids = getattr(self, "_effect_ids", [])
                if ids and effects.VIALRGB_DIRECT in ids:
                    self.effect_combo.current(ids.index(effects.VIALRGB_DIRECT))

            # 把要推的侧写进逐键缓冲区，再按分区推
            if not self._led_colors or len(self._led_colors) != len(self.leds):
                self._led_colors = [(0, 0, 0)] * len(self.leds)
            for led in self.leds:
                if led.zone in targets:
                    self._led_colors[led.index] = tuple(self._zone_colors[led.zone])

            total = self.dev.push_zoned(self._led_colors, targets)
        except Exception as exc:
            self.log("分区推送失败: %s" % exc, "err")
            return

        names = "、".join(ZONE_LABELS.get(z, z) for z in targets)
        self.log("已按分区推送 %s（%d 颗 LED）" % (names, total), "ok")
        self.draw_leds()
        self.draw_layout()

    def _push_zone_amk(self, targets):
        """AMK 后端的分区推送。

        轴灯（``0x80``–``0x83``）与配件灯（``0xFD`` STRIP 命令族）是两套
        独立状态，所以「仅轴灯」不会动到配件灯 —— 这正是原厂的行为。
        """
        if self.state is None:
            try:
                self.state = self.dev.read_lighting()
            except Exception as exc:
                self.log("读取灯光状态失败: %s" % exc, "err")
                return

        written = 0
        try:
            if "key" in targets:
                h, s, v = self._zone_colors["key"]
                self.dev.set_color(h, s, v)
                self.state.hue, self.state.sat, self.state.val = h, s, v
                written += self.dev.num_key_leds()
                # 注意：轴灯颜色由**板载灯效引擎**渲染（set_color 写的是
                # 0x81 色相寄存器），而逐灯通道并不驱动键位轴灯 —— 所以
                # 这里既不需要也无法"补灯"。详见 diagnose_axis_leds()。
            if "acc" in targets:
                h, s, v = self._zone_colors["acc"]
                for strip in self._amk_strips():
                    # 这是分区页的批量入口：明确把颜色应用到全部灯条。
                    # 本机只有 Custom 档会采用逐灯色，因此记录切档提示。
                    was_custom = (
                        strip.mode == effects.STRIP_EFFECT_CUSTOM)
                    if self.dev.set_strip_color(strip, h, s, v, on=v > 0,
                                                force=True):
                        written += strip.count
                        if not was_custom:
                            self.log("批量改色：灯条 %d 已从原灯效切到"
                                     "「自定义」" % (strip.index + 1), "warn")
        except Exception as exc:
            self.log("分区推送失败: %s" % exc, "err")
            return

        names = "、".join(ZONE_LABELS.get(z, z) for z in targets)
        self.log("已按分区推送 %s（%d 颗灯）" % (names, written), "ok")
        self._sync_widgets()
        self._refresh_swatch()
        self._refresh_strip_bar()
        self.draw_layout()

    # ------------------------------------------------------------------
    def _build_layout_tab(self):
        top = tk.Frame(self.tab_layout, bg=BG)
        top.pack(fill="x", pady=(0, 8))

        tk.Label(top, text="配列", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(side="left")

        self.layout_view = tk.StringVar(value="matrix")
        self._layout_seg = T.Segmented(
            top, [("matrix", "矩阵网格"), ("kle", "物理配列")], value="matrix",
            command=self._set_layout_view, bg=BG)
        self._layout_seg.pack(side="left", padx=(PAD_L, 0))

        self.show_matrix_var = tk.IntVar(value=1)
        T.Check(top, "显示坐标", self.show_matrix_var, command=self.draw_layout,
                bg=BG).pack(side="left", padx=PAD_L)

        self.layout_info = tk.Label(top, text="未加载", bg=BG, fg=FG_FAINT,
                                    font=(UI_FONT, 8))
        self.layout_info.pack(side="right")

        card = T.Card(self.tab_layout, padding=1, fill=CARD, stroke=STROKE,
                      expand_inside=True)
        card.pack(fill="both", expand=True)
        self.layout_canvas = tk.Canvas(card.inner, bg=CARD,
                                       highlightthickness=0)
        self.layout_canvas.pack(fill="both", expand=True)
        self.layout_canvas.bind("<Configure>", lambda e: self.draw_layout())

        self.layout_warn = tk.Label(self.tab_layout, text="", bg=BG, fg=WARN,
                                    anchor="w", justify="left", wraplength=1040,
                                    font=(UI_FONT, 8))

    def _set_layout_view(self, value):
        self.layout_view.set(value)
        self.draw_layout()

    def draw_layout(self):
        cv = self.layout_canvas
        cv.delete("all")
        view = getattr(self, "layout_view", None)
        view = view.get() if view is not None else "matrix"
        layout = self.layout_matrix if view == "matrix" else self.layout_kle
        self._active_layout = layout

        w = max(cv.winfo_width(), 10)
        h = max(cv.winfo_height(), 10)

        if layout is None or not layout.keys:
            cv.create_text(w // 2, h // 2, text="未连接设备",
                           fill=FG_FAINT, font=(UI_FONT, 10))
            self.layout_info.configure(text="未加载")
            self._update_layout_warn()
            return

        uw = max(layout.width_units, 1e-6)
        uh = max(layout.height_units, 1e-6)
        unit = min((w - 90) / uw, (h - 70) / uh)
        ox = (w - uw * unit) / 2 - layout.min_x * unit
        oy = (h - uh * unit) / 2 - layout.min_y * unit
        gap = max(unit * 0.07, 1.0)
        radius = max(unit * 0.09, 1.0)

        if self.state is not None and self.state.val:
            fill = C.hsv_to_hex(self.state.hue, self.state.sat, self.state.val)
        else:
            fill = "#c9ced6"
        empty = "#ecf0f4"

        shown = 0
        for k in layout.keys:
            mapped = (k.row, k.col) in layout.mapped
            if view == "matrix" and not mapped:
                continue
            shown += 1
            colour = fill if mapped else empty
            if k.rotated:
                # 带旋转的键（Alice 式的斜置字母区）必须画成旋转多边形，
                # 否则几何完全对不上 —— 这正是「物理配列画得不像」的根因。
                pts = []
                for px, py in k.corners():
                    pts.extend((ox + px * unit, oy + py * unit))
                cv.create_polygon(pts, fill=colour, outline=STROKE, width=1,
                                  joinstyle="round")
                cx = sum(pts[0::2]) / 4.0
                cy = sum(pts[1::2]) / 4.0
            else:
                x1 = ox + k.x * unit + gap
                y1 = oy + k.y * unit + gap
                x2 = ox + (k.x + k.w) * unit - gap
                y2 = oy + (k.y + k.h) * unit - gap
                T.round_rect(cv, x1, y1, x2, y2, radius, colour, STROKE, 1)
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
            if self.show_matrix_var.get() and k.row is not None and unit > 13:
                lum = sum(_hex_to_rgb(colour)) / 3.0
                cv.create_text(cx, cy, text=k.label,
                               fill="#3a3a3a" if lum > 130 else "#ffffff",
                               font=(MONO_FONT, max(6, int(unit * 0.19))))

        rot = sum(1 for k in layout.keys if k.rotated)
        self.layout_info.configure(
            text="%s · %d 键 · %s×%s 矩阵 · %g×%gU%s" % (
                layout.name or "?", shown,
                (layout.matrix or {}).get("rows", "?"),
                (layout.matrix or {}).get("cols", "?"),
                layout.width_units, layout.height_units,
                "　·　%d 键带旋转" % rot if rot else ""))
        self._update_layout_warn()

    def _update_layout_warn(self):
        view = getattr(self, "layout_view", None)
        view = view.get() if view is not None else "matrix"
        text = ""
        if view == "kle" and kbdef.layout_looks_montage(self.layout_kle):
            text = ("旋转（r / rx / ry）已按 KLE 规范还原，但这把键盘的定义把配列"
                    "拆成了 20 个区块，且各区块用的坐标系不同 —— 外侧列按平铺坐标、"
                    "字母区按 Alice 旋转坐标，所以拼在一起时左右两组会分家。"
                    "这是定义本身的写法导致的，本程序尚未做跨区块坐标缝合。"
                    "要准确对照灯光请看「矩阵网格」，那里直接对应固件的行 / 列索引。")
        elif view == "matrix" and self.layout_matrix is not None:
            text = "每个格子是一个矩阵单元（行,列），按当前灯光颜色着色。"
        self.layout_warn.configure(text=text)
        if text:
            self.layout_warn.pack(fill="x", pady=(6, 0))
        else:
            self.layout_warn.pack_forget()

    # ------------------------------------------------------------------
    def _build_perkey_tab(self):
        top = tk.Frame(self.tab_perkey, bg=BG)
        top.pack(fill="x", pady=(0, 8))

        tk.Label(top, text="逐键", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(side="left")
        self.pk_hint = tk.Label(top, text="", bg=BG, fg=FG_FAINT, font=(UI_FONT, 8))
        self.pk_hint.pack(side="right")

        tools = tk.Frame(self.tab_perkey, bg=BG)
        tools.pack(fill="x", pady=(0, 8))

        r1 = tk.Frame(tools, bg=BG)
        r1.pack(fill="x")
        tk.Label(r1, text="工具", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), width=4,
                 anchor="w").pack(side="left", padx=(0, 6))
        self._tool_btns = {}
        self._tool_seg = T.Segmented(
            r1, [("画笔", "画笔"), ("取色", "取色"), ("填充", "填充")],
            value="画笔", command=self._set_tool, bg=BG)
        self._tool_seg.pack(side="left")

        tk.Frame(r1, bg=STROKE, width=1, height=18).pack(side="left", padx=12)

        tk.Label(r1, text="图案", bg=BG, fg=FG_DIM, font=(UI_FONT, 9)).pack(
            side="left", padx=(0, 6))
        for name in ("水平渐变", "垂直渐变", "彩虹", "波浪", "全部同色", "清空"):
            T.FlatButton(r1, name, variant="standard", height=30, bg=BG,
                         command=lambda n=name: self.render_pattern(n)).pack(
                side="left", padx=(0, 6))

        r2 = tk.Frame(tools, bg=BG)
        r2.pack(fill="x", pady=(8, 0))
        tk.Label(r2, text="效果", bg=BG, fg=FG_DIM, font=(UI_FONT, 9), width=4,
                 anchor="w").pack(side="left", padx=(0, 6))
        self.anim_var = tk.IntVar(value=0)
        self.anim_check = T.Check(
            r2, "本地动画（仅 VialRGB，由电脑持续推送，约 30fps）",
            self.anim_var, command=self._toggle_anim, bg=BG)
        self.anim_check.pack(side="left")

        T.FlatButton(r2, "从键盘读取", variant="standard", height=30, bg=BG,
                     command=self._pk_pull).pack(side="right", padx=(6, 0))
        T.FlatButton(r2, "进入自定义档", variant="standard", height=30, bg=BG,
                     command=self._pk_enter_custom).pack(side="right")
        T.FlatButton(r2, "立即推送", variant="accent", height=30, bg=BG,
                     command=self.push_leds).pack(side="right", padx=(0, 6))

        card = T.Card(self.tab_perkey, padding=1, fill=CARD, stroke=STROKE,
                      expand_inside=True)
        card.pack(fill="both", expand=True)
        self.pk_canvas = tk.Canvas(card.inner, bg=CARD, highlightthickness=0)
        self.pk_canvas.pack(fill="both", expand=True)
        self.pk_canvas.bind("<Button-1>", self._pk_click)
        self.pk_canvas.bind("<B1-Motion>", self._pk_drag)
        self.pk_canvas.bind("<Configure>", lambda e: self.draw_leds())

    def _set_tool(self, value):
        self._paint_tool.set(value)

    def _pk_enter_custom(self):
        """把轴灯切到「逐灯自定义」档 —— 逐键上色的前置条件。"""
        if self.dev is None:
            self.log("尚未连接设备", "warn")
            return
        if self.dev.lighting_backend != "amk":
            self.log("「自定义档」仅对 AMK 通道有意义", "warn")
            return
        try:
            if self.dev.matrix_in_custom():
                self.log("已在自定义档，可直接逐键上色", "ok")
                return
            if self.dev.enter_matrix_custom():
                self.log("已切到矩阵自定义档，现在逐键上色会生效", "ok")
            else:
                self.log("切换自定义档失败（固件可能不响应）", "err")
        except Exception as exc:
            self.log("切换自定义档失败: %s" % exc, "err")

    def _pk_pull(self):
        """从键盘回读全部键位颜色，刷进画布。"""
        if self.dev is None:
            self.log("尚未连接设备", "warn")
            return
        if self.dev.lighting_backend != "amk":
            self.pull_from_device()
            self.log("VialRGB 协议只能回读全局灯效状态，不能回读逐灯颜色；"
                     "画布颜色保持当前编辑内容", "warn")
            return
        try:
            data = self.dev.perkey_read()
        except Exception as exc:
            self.log("逐键回读失败: %s" % exc, "err")
            return
        if not self._led_colors or len(self._led_colors) != len(self.leds):
            self._led_colors = [(0, 0, 0)] * len(self.leds)
        n = 0
        for led in self.leds:
            if led.row is None:
                continue
            got = data.get(led.global_index)
            if got is None:
                continue
            h, s, v = got[0], got[1], got[2]
            self._led_colors[led.index] = (h, s, v)
            self._led_known.add(led.global_index)
            self._pushed[led.global_index] = (h, s, v)
            if v:
                n += 1
        self.draw_leds()
        self.log("已从键盘回读 %d 颗键位（非黑 %d 颗）"
                 % (len(data), n), "ok")

    def draw_leds(self):
        cv = self.pk_canvas
        cv.delete("all")
        w = max(cv.winfo_width(), 10)
        h = max(cv.winfo_height(), 10)

        if not self.leds:
            cv.create_text(w // 2, h // 2, text="尚未读取到灯位",
                           fill=FG_FAINT, font=(UI_FONT, 10))
            cv.create_text(w // 2, h // 2 + 22,
                           text="请先连接设备，或点「从键盘读取」",
                           fill=FG_FAINT, font=(UI_FONT, 8))
            return

        xs = [l.x for l in self.leds]
        ys = [l.y for l in self.leds]
        span_x = max(max(xs) - min(xs), 1)
        span_y = max(max(ys) - min(ys), 1)
        unit = min((w - 90) / (span_x + 2), (h - 90) / (span_y + 2))
        ox = (w - span_x * unit) / 2 - min(xs) * unit
        oy = (h - span_y * unit) / 2 - min(ys) * unit
        self._pk_unit = unit
        self._pk_origin = (ox, oy)
        size = max(unit * 0.86, 5)
        if not self._led_colors or len(self._led_colors) != len(self.leds):
            self._led_colors = [(0, 0, 0)] * len(self.leds)

        for led in self.leds:
            hh, ss, vv = self._led_colors[led.index]
            cx = ox + led.x * unit
            cy = oy + led.y * unit
            fillc = C.hsv_to_hex(hh, ss, vv) if vv else "#e8ecf1"
            read_only = (
                self.dev is not None
                and self.dev.lighting_backend == "amk"
                and not led.is_matrix)
            cv.create_oval(cx - size / 2, cy - size / 2, cx + size / 2,
                           cy + size / 2, fill=fillc,
                           outline=(FG_DIS if read_only else
                                    (STROKE if led.is_matrix else ACCENT_SOFT)),
                           width=1)

        # 图例：两种灯的边框颜色不同
        cv.create_text(12, 12, anchor="nw", fill=FG_FAINT, font=(UI_FONT, 8),
                       text=("细边 = 可写轴灯　　亮蓝边 = 配件灯"
                             + ("（AMK 配件灯仅显示，不支持逐灯写）"
                                if self.dev is not None
                                and self.dev.lighting_backend == "amk"
                                else "")))

    def _pk_led_at(self, x, y):
        if not self.leds:
            return None
        unit = getattr(self, "_pk_unit", 20)
        ox, oy = getattr(self, "_pk_origin", (0, 0))
        best, bestd = None, (unit * 1.1) ** 2
        for led in self.leds:
            dx = x - (ox + led.x * unit)
            dy = y - (oy + led.y * unit)
            d = dx * dx + dy * dy
            if d < bestd:
                best, bestd = led, d
        return best

    def _pk_click(self, event):
        led = self._pk_led_at(event.x, event.y)
        if led is None:
            return
        if len(self._led_colors) != len(self.leds):
            self._led_colors = [(0, 0, 0)] * len(self.leds)
        if (self.dev is not None and self.dev.lighting_backend == "amk"
                and not led.is_matrix):
            self.log("AMK 配件灯在逐键页仅用于显示，请到分区页编辑灯条", "warn")
            return
        tool = self._paint_tool.get()
        if tool == "取色":
            hh, ss, vv = self._led_colors[led.index]
            if vv:
                self.v_hue.set(hh)
                self.apply_color(hh, ss, vv)
            return
        if tool == "填充":
            changed = set()
            for item in self.leds:
                if (self.dev is not None
                        and self.dev.lighting_backend == "amk"
                        and not item.is_matrix):
                    continue
                self._led_colors[item.index] = tuple(self._current_color)
                changed.add(item.global_index)
        else:
            self._led_colors[led.index] = tuple(self._current_color)
            changed = {led.global_index}
        self._led_known.update(changed)
        self.draw_leds()
        self.push_leds(changed=changed)

    def _pk_drag(self, event):
        led = self._pk_led_at(event.x, event.y)
        if led is None or self._paint_tool.get() != "画笔":
            return
        if (self.dev is not None and self.dev.lighting_backend == "amk"
                and not led.is_matrix):
            return
        self._led_colors[led.index] = tuple(self._current_color)
        self._led_known.add(led.global_index)
        self.draw_leds()
        # 拖动是高频事件：只标记改动、防抖批量推送，不要每帧都全量写
        self._schedule_pk_push(led.global_index)

    def render_pattern(self, name):
        if not self.leds:
            self.log("逐键图案需要 VialRGB 后端", "warn")
            return
        h, s, v = self._current_color
        xs = [l.x for l in self.leds]
        ys = [l.y for l in self.leds]
        if len(self._led_colors) != len(self.leds):
            self._led_colors = [(0, 0, 0)] * len(self.leds)
        results = list(self._led_colors)
        changed = set()
        for led in self.leds:
            if (self.dev is not None and self.dev.lighting_backend == "amk"
                    and not led.is_matrix):
                continue
            if name == "水平渐变":
                t = (led.x - min(xs)) / max(max(xs) - min(xs), 1)
                colour = (int(h + t * 120) % 256, s, v)
            elif name == "垂直渐变":
                t = (led.y - min(ys)) / max(max(ys) - min(ys), 1)
                colour = (int(h + t * 120) % 256, s, v)
            elif name == "彩虹":
                t = (led.x - min(xs)) / max(max(xs) - min(xs), 1)
                colour = (int(t * 255) % 256, 255, v)
            elif name == "波浪":
                t = led.index / max(len(self.leds) - 1, 1)
                colour = (int((h + t * 200) % 256), s,
                          int(v * (0.35 + 0.65 * abs((t * 4) % 2 - 1))))
            elif name == "全部同色":
                colour = (h, s, v)
            else:
                colour = (0, 0, 0)
            results[led.index] = colour
            changed.add(led.global_index)
        self._led_colors = results
        self._led_known.update(changed)
        self.draw_leds()
        self.push_leds(changed=changed)

    def _toggle_anim(self):
        if self.anim_var.get():
            if (self.dev is None or self.dev.lighting_backend != "vialrgb"
                    or not self.leds):
                self.anim_var.set(0)
                self.log("本地动画仅支持已读取 LED 的 VialRGB 设备", "warn")
                return
            self._anim_t = 0
            self.log("本地动画已开启（由电脑持续推送，约 30fps）", "ok")
        else:
            if self._anim_job:
                self.after_cancel(self._anim_job)
                self._anim_job = None
            self.log("本地动画已关闭")

    def _anim_step(self):
        if (not self.anim_var.get() or not self.leds or self.dev is None
                or self.dev.lighting_backend != "vialrgb"):
            self._anim_job = None
            return
        self._anim_t += 4
        xs = [l.x for l in self.leds]
        lo, hi = min(xs), max(xs)
        span = max(hi - lo, 1)
        out = []
        for led in self.leds:
            t = (led.x - lo) / span
            out.append((int((t * 200 + self._anim_t) % 256), 255,
                        self._current_color[2]))
        self._led_colors = out
        try:
            self.dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
            self.dev.vialrgb_push(out)
        except Exception as exc:
            self.log("动画推送失败: %s" % exc, "err")
            self.anim_var.set(0)
            self._anim_job = None
            return
        self.draw_leds()
        self._anim_job = self.after(33, self._anim_step)

    # ------------------------------------------------------------------
    def _build_info_tab(self):
        pane = ttk.PanedWindow(self.tab_info, orient="horizontal")
        pane.pack(fill="both", expand=True)

        left = tk.Frame(pane, bg=BG)
        right = tk.Frame(pane, bg=BG)
        pane.add(left, weight=1)
        pane.add(right, weight=1)

        tk.Label(left, text="设备信息", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(
            anchor="w", pady=(0, 6))
        card = T.Card(left, padding=1, fill=CARD, stroke=STROKE,
                      expand_inside=True)
        card.pack(fill="both", expand=True)
        self.info_text = tk.Text(card.inner, bg=CARD, fg=FG, bd=0, relief="flat",
                                 font=(MONO_FONT, 9), height=10, wrap="none",
                                 insertbackground=FG, padx=12, pady=10,
                                 highlightthickness=0)
        self.info_text.pack(fill="both", expand=True)

        tk.Label(left, text="原始报文", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(
            anchor="w", pady=(PAD + 4, 6))
        rcard = T.Card(left, padding=PAD_L)
        rcard.pack(fill="x")
        rr = tk.Frame(rcard.inner, bg=SURFACE)
        rr.pack(fill="x")
        self.raw_entry = T.Entry(rr, width=220, text="08 80", bg=SURFACE,
                                 on_submit=self.send_raw)
        self.raw_entry.pack(side="left")
        T.FlatButton(rr, "发送", variant="standard", height=30, bg=SURFACE,
                     command=self.send_raw).pack(side="left", padx=(PAD, 0))
        self.raw_out = tk.Label(rcard.inner, text="—", bg=SURFACE, fg=FG_DIM,
                                font=(MONO_FONT, 9), anchor="w", justify="left",
                                wraplength=520)
        self.raw_out.pack(fill="x", pady=(8, 0))

        tk.Label(right, text="日志", bg=BG, fg=FG, font=(UI_FONT, 10)).pack(
            anchor="w", pady=(0, 6))
        ccard = T.Card(right, padding=1, fill=CARD, stroke=STROKE,
                       expand_inside=True)
        ccard.pack(fill="both", expand=True)
        self.console = tk.Text(ccard.inner, bg=CARD, fg=FG_DIM, bd=0,
                               relief="flat", font=(MONO_FONT, 9), height=14,
                               wrap="word", insertbackground=FG, state="disabled",
                               padx=12, pady=10, highlightthickness=0)
        self.console.pack(fill="both", expand=True)

    def send_raw(self):
        if self.dev is None:
            self.log("未连接设备", "warn")
            return
        text = self.raw_entry.get().replace(",", " ").strip()
        try:
            payload = bytes(int(b, 16) for b in text.split() if b)
        except ValueError:
            self.log("十六进制解析失败", "err")
            return
        try:
            resp = self.dev.raw_request(payload)
        except Exception as exc:
            self.log("发送失败: %s" % exc, "err")
            return
        self.raw_out.configure(text="← " + " ".join("%02X" % b for b in resp))
        self.log("原始报文 %d 字节已发送" % len(payload), "ok")

    # ==================================================================
    # 设备连接
    # ==================================================================
    def refresh_devices(self):
        try:
            self._vial_devs = VialDevice.discover()
            self._raw_devs = VialDevice.discover_all_raw()
        except Exception as exc:
            self.log("枚举 HID 失败: %s" % exc, "err")
            self._vial_devs, self._raw_devs = [], []
        items = [d.display_name for d in self._vial_devs]
        self.dev_combo.configure(values=items)
        if items and not self.dev_var.get():
            self.dev_var.set(items[0])
        if not items:
            self.dev_var.set("")
        self.hid_lbl.configure(text=_backend_text())
        self.log("发现 %d 个 Vial 设备 / %d 个 raw HID 接口" % (
            len(self._vial_devs), len(self._raw_devs)),
            "ok" if self._vial_devs else "warn")

    def pick_all_devices(self):
        try:
            devs = VialDevice.discover_all_raw()
        except Exception as exc:
            self.log("枚举失败: %s" % exc, "err")
            return
        if not devs:
            messagebox.showinfo(
                "提示", "没有发现 QMK raw HID 接口（usage page 0xFF60 / usage 0x61）")
            return
        win = tk.Toplevel(self)
        win.title("手动选择 HID 接口")
        win.configure(bg=BG)
        win.geometry("760x400")
        tk.Label(win, text="用于自动识别失败的副厂板子", bg=BG, fg=FG_DIM,
                 font=(UI_FONT, 9)).pack(anchor="w", padx=PAD_L, pady=(PAD_L, 6))
        card = T.Card(win, padding=1, fill=CARD, stroke=STROKE,
                      expand_inside=True)
        card.pack(fill="both", expand=True, padx=PAD_L)
        lb = tk.Listbox(card.inner, bg=CARD, fg=FG, bd=0, relief="flat",
                        font=(MONO_FONT, 9), selectbackground=ACCENT,
                        selectforeground=ACCENT_FG, activestyle="none",
                        highlightthickness=0, padx=12, pady=10)
        lb.pack(fill="both", expand=True)
        self._all_devs = devs
        for i, d in enumerate(devs):
            lb.insert("end", "%2d   %s   usage=%s:%s   serial=%r" % (
                i, d.display_name, hex(d.usage_page or 0), hex(d.usage or 0),
                d.serial))

        def use():
            sel = lb.curselection()
            if not sel:
                return
            win.destroy()
            self.connect_device(self._all_devs[sel[0]])

        brow = tk.Frame(win, bg=BG)
        brow.pack(fill="x", padx=PAD_L, pady=PAD_L)
        T.FlatButton(brow, "连接选中项", variant="accent", height=32, bg=BG,
                     command=use).pack(side="right")

    def toggle_connect(self):
        if self.dev is not None:
            self.disconnect()
            return
        devs = getattr(self, "_vial_devs", [])
        if not devs:
            self.refresh_devices()
            devs = getattr(self, "_vial_devs", [])
        if not devs:
            messagebox.showwarning(
                "没有找到设备",
                "没有检测到 Vial 键盘。\n\n"
                "· 确认键盘已通过数据线连接\n"
                "· 若为副厂板，试试「更多」里手动选择接口")
            return
        idx = self.dev_combo.current()
        self.connect_device(devs[idx if idx >= 0 else 0])

    def connect_device(self, info):
        self.disconnect(quiet=True)
        try:
            dev = VialDevice(info).open()
            dev.read_versions()
            backend = dev.detect_lighting()
            self.dev = dev
        except Exception as exc:
            self.log("连接失败: %s" % exc, "err")
            messagebox.showerror("连接失败", str(exc))
            return

        self.connect_btn.configure_text("断开")
        self.backend_lbl.configure(text=_short_backend(backend),
                                   fg=OK if backend else FG_FAINT)
        self.log("已连接 %s" % info.display_name, "ok")

        self.load_definition()
        self.pull_from_device()
        self.load_leds()
        self.update_info()
        self.refresh_zone_panel()

    def disconnect(self, quiet=False):
        if self.dev is not None:
            self.dev.close()
            self.dev = None
        if self._pending_job is not None:
            try:
                self.after_cancel(self._pending_job)
            except Exception:
                pass
            self._pending_job = None
        self._pending = None
        self.state = None
        self._set_state_dirty(False)
        if self._pk_push_job is not None:
            try:
                self.after_cancel(self._pk_push_job)
            except Exception:
                pass
            self._pk_push_job = None
        self._pk_pending = set()
        self.connect_btn.configure_text("连接")
        self.backend_lbl.configure(text="未连接", fg=FG_FAINT)
        self.leds = []
        self._led_colors = []
        self._led_known = set()
        self._strip_index = None
        self._strip_sel = None
        if self.anim_var.get():
            self.anim_var.set(0)
            self._toggle_anim()
        self._update_anim_enabled()
        self.draw_layout()
        self.draw_leds()
        self.refresh_zone_panel()
        if not quiet:
            self.log("已断开")

    def load_definition(self):
        self.layout = None
        self.layout_kle = None
        self.layout_matrix = None
        if self.dev is None:
            return
        try:
            definition = self.dev.read_definition()
            self.definition = definition
            self.layout_kle = kbdef.parse_layout(definition)
            self.layout_matrix = kbdef.matrix_grid_layout(definition)
            self.layout = self.layout_matrix
            opts = kbdef.count_layout_options(definition)
            extra = ("，%d 项布局选项（按默认组合渲染）" % len(opts)) if opts else ""
            self.log("键盘定义已加载：%s，%d 个键位%s" % (
                definition.get("name"), len(self.layout_kle.keys), extra), "ok")
        except Exception as exc:
            self.log("读取键盘定义失败: %s" % exc, "warn")
        self.draw_layout()

    def load_leds(self):
        self.leds = []
        self._led_colors = []
        self._led_known = set()
        # 影子缓冲一并清空：新设备/新灯位要按全量重新推送
        self._pushed = {}
        if self._pk_push_job is not None:
            try:
                self.after_cancel(self._pk_push_job)
            except Exception:
                pass
            self._pk_push_job = None
        self._pk_pending = set()
        if self.dev is None:
            self.pk_hint.configure(text="未连接")
            self.draw_leds()
            return

        backend = self.dev.lighting_backend
        if backend == "amk":
            # AMK 通道：轴灯位置来自真实矩阵行表，可以逐键上色；
            # 配件灯仍是逻辑灯位（固件不给逐灯坐标）。
            try:
                self.leds = self.dev.amk_leds()
                self._led_colors = [(0, 0, 0)] * len(self.leds)
                key_n = sum(1 for l in self.leds if l.row is not None)
                acc_n = len(self.leds) - key_n
                if self.dev.perkey_supported():
                    self.pk_hint.configure(
                        text="%d 键可逐键上色 / 配件灯 %d 颗（配件灯请到分区页编辑）"
                             % (key_n, acc_n))
                else:
                    self.pk_hint.configure(
                        text="轴灯 %d / 配件灯 %d（矩阵通道不可用）"
                             % (key_n, acc_n))
            except Exception as exc:
                self.log("读取 AMK 灯位失败: %s" % exc, "warn")
                self.pk_hint.configure(text="不支持逐键")
            self._update_anim_enabled()
            self.draw_leds()
            return

        if backend != "vialrgb":
            self.pk_hint.configure(text="不支持逐键")
            self._update_anim_enabled()
            self.draw_leds()
            return
        try:
            self.leds = self.dev.vialrgb_leds()
            self._led_colors = [(0, 0, 0)] * len(self.leds)
            key_n = sum(1 for l in self.leds if l.zone == "key")
            acc_n = len(self.leds) - key_n
            self.pk_hint.configure(
                text="%d 颗灯（轴灯 %d / 配件灯 %d）" % (
                    len(self.leds), key_n, acc_n))
            self.log("已读取 %d 颗 LED 的位置信息（轴灯 %d / 配件灯 %d）" % (
                len(self.leds), key_n, acc_n), "ok")
        except Exception as exc:
            self.log("读取 LED 信息失败: %s" % exc, "err")
        self._update_anim_enabled()
        self.draw_leds()

    def _update_anim_enabled(self):
        supported = (
            self.dev is not None
            and self.dev.lighting_backend == "vialrgb"
            and bool(self.leds)
        )
        try:
            self.anim_check.set_enabled(supported)
        except Exception:
            return
        if not supported and self.anim_var.get():
            self.anim_var.set(0)
            if self._anim_job is not None:
                try:
                    self.after_cancel(self._anim_job)
                except Exception:
                    pass
                self._anim_job = None
            self.log("当前设备不支持电脑本地动画，已自动关闭", "warn")

    # ==================================================================
    # 灯光读写
    # ==================================================================
    def pull_from_device(self):
        if self.dev is None:
            return
        if self.dev.lighting_backend is None:
            self.log("该固件未暴露 raw HID 灯光通道，只能读取其他信息", "warn")
            return
        if self._state_dirty:
            ok = messagebox.askyesno(
                "放弃未应用修改",
                "当前有尚未应用到设备的全局灯光修改。\n"
                "从键盘读取会用设备状态覆盖这些修改，是否继续？",
                parent=self)
            if not ok:
                self.log("已取消读取，保留未应用修改", "info")
                return
        if self._pending_job is not None:
            try:
                self.after_cancel(self._pending_job)
            except Exception:
                pass
            self._pending_job = None
        try:
            self.state = self.dev.read_lighting()
        except Exception as exc:
            self.log("读取灯光状态失败: %s" % exc, "err")
            return
        self._pending = None
        self._set_state_dirty(False)
        self._sync_widgets()
        self.log("已读取灯光：灯效 %s · 亮度 %s · 色相 %s · 饱和 %s · 速度 %s" % (
            self.state.effect, self.state.val, self.state.hue,
            self.state.sat, self.state.speed), "ok")

    def _sync_widgets(self):
        if self.state is None:
            return
        self._ignore_scale = True
        self.v_bright.set(self.state.val)
        self.s_speed.set_range(*self.dev.speed_range())
        self.s_speed.set(self.state.speed)
        self.v_hue.set(self.state.hue)
        self.v_sat.set(self.state.sat)
        self._ignore_scale = False
        self.s_bright.refresh()
        self.s_speed.refresh()
        self.s_sat.refresh()
        self.hue_val.configure(text=str(self.v_hue.get()))
        ids = self.dev.effect_ids()
        self.effect_combo.configure(
            values=["%d   %s" % (i, self.dev.effect_label(i)) for i in ids])
        self._effect_ids = ids
        if self.state.effect in ids:
            self.effect_combo.current(ids.index(self.state.effect))
            self.effect_pos.configure(text="%d / %d" % (self.state.effect, ids[-1]))
        else:
            self.effect_var.set("%d  (不在固件列表内)" % self.state.effect)
            self.effect_pos.configure(text="—")
        self.effect_hint.configure(text=self._hint_text())
        self._refresh_swatch()
        self._current_color = (self.state.hue, self.state.sat, self.state.val)
        self.draw_layout()

    def _hint_text(self):
        if self.dev is None:
            return ""
        b = self.dev.lighting_backend
        if b == "amk":
            return ("AMK 灯光通道：这一页只管**轴灯**（0x80–0x83 整组设色），"
                    "灯效 1–%d、速度 0–%d。配件灯是另一套独立状态，"
                    "到「分区」页可以单独给它选灯效和颜色（本机固件只有"
                    "「自定义」档能吃下自选颜色）。"
                    % (effects.AMK_MODE_MAX, effects.AMK_SPEED_MAX))
        if b == "vialrgb":
            return ("VialRGB 官方协议：支持逐键直接控制（见「逐键」页），"
                    "若固件把轴灯与配件灯拆成两组 LED，还能在「分区」页分开设置。")
        if b == "via":
            return "VIA 标准照明通道：速度通常只有 0–3 四档，不支持逐键与分区。"
        return "该固件没有暴露 raw HID 灯光通道。"

    def _refresh_swatch(self, h=None, s=None, v=None):
        h = self.v_hue.get() if h is None else h
        s = self.v_sat.get() if s is None else s
        v = self.v_bright.get() if v is None else v
        hx = C.hsv_to_hex(h, s, v)
        self.swatch.delete("all")
        pts = T.rounded_points(0.5, 0.5, 87.5, 27.5, 5)
        self.swatch.create_polygon(pts, fill=hx, outline=STROKE, smooth=True,
                                   splinesteps=10)
        if self.hex_entry.get() != hx:
            self.hex_entry.set(hx)

    # ---- 回调 -------------------------------------------------------
    def _on_bright(self, value):
        if self._ignore_scale:
            return
        self.v_bright.set(int(float(value)))
        self.s_bright.refresh()
        self._refresh_swatch()
        self._current_color = (self.v_hue.get(), self.v_sat.get(),
                               int(float(value)))
        self.queue_state(val=int(float(value)))

    def _on_speed(self, value):
        if self._ignore_scale:
            return
        self.v_speed.set(int(float(value)))
        self.s_speed.refresh()
        self.queue_state(speed=int(float(value)))

    def _on_hue(self, value):
        if self._ignore_scale:
            return
        self.hue_val.configure(text=str(int(float(value))))
        self._refresh_swatch()
        self._current_color = (int(float(value)), self.v_sat.get(),
                               self.v_bright.get())
        self.queue_state(hue=int(float(value)))

    def _on_sat(self, value):
        if self._ignore_scale:
            return
        self.v_sat.set(int(float(value)))
        self.s_sat.refresh()
        self._refresh_swatch()
        self._current_color = (self.v_hue.get(), int(float(value)),
                               self.v_bright.get())
        self.queue_state(sat=int(float(value)))

    def _on_effect_combo(self, _event=None):
        idx = self.effect_combo.current()
        if idx < 0:
            return
        eff = self._effect_ids[idx]
        self.queue_state(effect=eff)
        self.effect_pos.configure(text="%d / %d" % (eff, self._effect_ids[-1]))

    def step_effect(self, delta):
        if self.dev is None:
            return
        ids = getattr(self, "_effect_ids", [])
        if not ids:
            return
        cur = self.state.effect if self.state else ids[0]
        pos = ids.index(cur) if cur in ids else 0
        pos = max(0, min(len(ids) - 1, pos + delta))
        self.effect_combo.current(pos)
        self._on_effect_combo()

    def _on_hex(self, _event=None):
        try:
            r, g, b = C.hex_to_rgb(self.hex_entry.get())
        except ValueError as exc:
            self.log(str(exc), "err")
            return
        h, s, v = C.rgb_to_hsv(r, g, b)
        self.apply_color(h, s, v)

    def _pick_color(self):
        rgb, _ = colorchooser.askcolor(color=C.hsv_to_hex(
            self.v_hue.get(), self.v_sat.get(), self.v_bright.get()))
        if not rgb:
            return
        r, g, b = [int(x) for x in rgb]
        h, s, v = C.rgb_to_hsv(r, g, b)
        self.apply_color(h, s, v)

    def apply_color(self, h, s, v):
        self._ignore_scale = True
        self.v_hue.set(h)
        self.v_sat.set(s)
        self.v_bright.set(v)
        self._ignore_scale = False
        self.s_bright.refresh()
        self.s_sat.refresh()
        self.hue_val.configure(text=str(h))
        self._refresh_swatch()
        self._current_color = (h, s, v)
        self.queue_state(hue=h, sat=s, val=v)

    def set_bright(self, v):
        self._ignore_scale = True
        self.v_bright.set(v)
        self._ignore_scale = False
        self.s_bright.refresh()
        self._refresh_swatch()
        self.queue_state(val=v)

    def _set_state_dirty(self, dirty):
        self._state_dirty = bool(dirty)
        label = getattr(self, "state_dirty_lbl", None)
        if label is not None:
            if self.dev is None:
                label.configure(text="")
            elif self._state_dirty:
                label.configure(text="● 有待应用修改", fg=WARN)
            else:
                label.configure(text="已与设备同步", fg=FG_FAINT)

        apply_btn = getattr(self, "apply_state_btn", None)
        if apply_btn is not None:
            apply_btn.set_enabled(self.dev is not None and self._state_dirty)
        save_btn = getattr(self, "save_btn", None)
        if save_btn is not None:
            save_btn.set_enabled(self.dev is not None)

    def _on_live_toggle(self):
        if self.live_var.get():
            if self._pending:
                self.log("实时下发已开启，正在应用待提交修改", "info")
                if self._pending_job is None:
                    self._pending_job = self.after(40, self._flush)
            else:
                self.log("实时下发已开启")
            return
        if self._state_dirty:
            self.log("实时下发已关闭，修改会保留到「应用到设备」或「保存到固件」",
                     "info")
        else:
            self.log("实时下发已关闭")

    # ==================================================================
    # 预设方案（保存 / 切换 / 管理一整套灯光）
    # ==================================================================
    def _preset_store(self):
        """惰性建方案库 —— 磁盘有问题也不能让界面起不来。"""
        store = getattr(self, "_store", None)
        if store is None:
            store = presets.PresetStore()
            self._store = store
            if store.error:
                self.log(store.error, "warn")
        return store

    def _device_key(self):
        """当前设备的标识（用来标注方案是给哪把键盘存的）。

        注意 ``self.dev.info`` 是 ``DeviceInfo`` 对象（``__slots__``），
        不是 dict —— 这里两种都兼容。
        """
        if self.dev is None:
            return {}
        info = self.dev.info

        def _pick(name):
            if info is None:
                return None
            if isinstance(info, dict):
                return info.get(name)
            return getattr(info, name, None)

        key = {}
        for src in ("vendor_id", "product_id", "serial"):
            v = _pick(src)
            if v is not None:
                key[src] = v
        try:
            key["keyboard_uid"] = self.dev.describe().get("keyboard_uid")
        except Exception:
            pass
        key["backend"] = self.dev.lighting_backend
        key["name"] = _pick("product") or _pick("name")
        return key

    def snapshot_lighting(self):
        """把当前一整套灯光状态拍成一个可序列化的字典。

        覆盖的范围（有多少存多少，缺的留空）：
        轴灯灯效 / 速度 / 色相 / 饱和度 / 亮度、分区作用范围、
        两侧分区颜色与亮度、每条配件灯条的灯效 / 颜色 / 亮度 / 速度。
        """
        data = {}
        if self.state is not None:
            st = self.state
            data["effect"] = st.effect
            data["speed"] = st.speed
            data["hue"] = st.hue
            data["sat"] = st.sat
            data["val"] = st.val
            data["brightness_max"] = st.brightness_max
        data["zone_mode"] = self._zone_mode.get()
        data["zone_colors"] = {k: list(v) for k, v in self._zone_colors.items()}
        data["zone_val"] = dict(self._zone_val)

        known_leds = {}
        for led in self.leds:
            if led.global_index not in self._led_known:
                continue
            known_leds[str(led.global_index)] = list(
                self._led_colors[led.index])
        if known_leds:
            data["led_colors"] = known_leds

        strips = []
        for s in self._amk_strips():
            item = {"index": s.index, "mode": s.mode,
                    "hue": s.hue, "sat": s.sat, "val": s.val,
                    "speed": s.speed}
            strips.append(item)
        if strips:
            data["strips"] = strips
        return data

    # ---- 列表绘制 ------------------------------------------------------
    def _preset_rows(self):
        """(名字, 摘要, 是否是给当前键盘存的) 列表。"""
        store = self._preset_store()
        cur = self._device_key()
        cur_uid = cur.get("keyboard_uid")
        cur_vid = cur.get("vendor_id")
        cur_pid = cur.get("product_id")
        rows = []
        for p in store.presets:
            d = p.device or {}
            same = None
            if cur_uid and d.get("keyboard_uid"):
                same = d.get("keyboard_uid") == cur_uid
            elif cur_vid is not None and d.get("vendor_id") is not None:
                same = (d.get("vendor_id") == cur_vid
                        and d.get("product_id") == cur_pid)
            rows.append((p.name, p.summary(), same))
        return rows

    def _draw_preset_list(self):
        cv = getattr(self, "_preset_list", None)
        if cv is None:
            return
        cv.delete("all")
        rows = self._preset_rows()
        w = max(int(cv.winfo_width()), 40)
        row_h = 32
        total = max(len(rows) * row_h, 40)
        cv.configure(scrollregion=(0, 0, w, total))
        if not rows:
            cv.create_text(12, 18, anchor="w", fill=FG_FAINT,
                           text="还没有方案 —— 调好灯光后在上方输入名字并「保存为新方案」",
                           font=(UI_FONT, 9))
            self._preset_detail.configure(text="")
            return

        y = 0
        for name, summary, same in rows:
            selected = (name == self._preset_sel)
            hovered = (name == self._preset_hover_row)
            if selected:
                fill, fg1, fg2 = ACCENT, "#ffffff", "#dce9f7"
            elif hovered:
                fill, fg1, fg2 = T.SUBTLE, FG, FG_DIM
            else:
                fill, fg1, fg2 = CARD, FG, FG_FAINT
            cv.create_rectangle(0, y, w, y + row_h - 2, fill=fill, outline="")
            # 左边一条状态色：给当前键盘存的 = 绿，别的键盘 = 灰
            if same is True:
                cv.create_rectangle(0, y + 4, 3, y + row_h - 6,
                                    fill=OK, outline="")
            elif same is False:
                cv.create_rectangle(0, y + 4, 3, y + row_h - 6,
                                    fill=STROKE_STRONG, outline="")
            cv.create_text(12, y + row_h / 2.0 - 1, anchor="w", fill=fg1,
                           text=name, font=(UI_FONT, 9))
            if summary:
                cv.create_text(w - 12, y + row_h / 2.0 - 1, anchor="e",
                               fill=fg2, text=summary, font=(UI_FONT, 8))
            y += row_h

        idx = self._preset_store().index_of(self._preset_sel) if self._preset_sel else -1
        if idx >= 0:
            p = self._preset_store().presets[idx]
            dev = p.device or {}
            lines = ["设备：%s" % (dev.get("name") or "未记录")]
            if dev.get("backend"):
                lines.append("后端：%s" % dev["backend"])
            if dev.get("keyboard_uid"):
                lines.append("UID：%s" % dev["keyboard_uid"])
            import time as _t
            try:
                lines.append("更新：%s" % _t.strftime(
                    "%Y-%m-%d %H:%M", _t.localtime(p.updated)))
            except Exception:
                pass
            self._preset_detail.configure(text="\n".join(lines))

    def _preset_hit(self, event):
        """把鼠标 y 坐标换算成方案名（没命中返回 None）。"""
        rows = self._preset_rows()
        if not rows:
            return None
        idx = int(event.y // 32)
        if 0 <= idx < len(rows):
            return rows[idx][0]
        return None

    def _on_preset_click(self, event):
        name = self._preset_hit(event)
        if name is None:
            return
        self._preset_sel = name
        self._preset_name.set(name)
        self._draw_preset_list()

    def _on_preset_wheel(self, event):
        cv = self._preset_list
        cv.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _set_preset_hover(self, name):
        if name == self._preset_hover_row:
            return
        self._preset_hover_row = name
        self._draw_preset_list()

    def _on_preset_motion(self, event):
        self._set_preset_hover(self._preset_hit(event))

    def _refresh_preset_list(self):
        self._draw_preset_list()

    def _selected_preset(self):
        if not self._preset_sel:
            return None
        return self._preset_store().get(self._preset_sel)

    # ---- 动作 ----------------------------------------------------------
    def preset_save(self):
        """保存为新方案（重名时自动加序号，不覆盖）。"""
        if self.dev is None:
            self.log("还没连接键盘", "warn")
            return
        store = self._preset_store()
        name = self._preset_name.get().strip()
        if not name:
            self.log("请先给方案起个名字", "warn")
            return
        data = self.snapshot_lighting()
        ok, result, _over = store.add(name, data, self._device_key(),
                                      overwrite=False, unique=True)
        if not ok:
            self.log("保存失败：%s" % result, "err")
            return
        self._preset_sel = result
        self._preset_name.set(result)
        self._draw_preset_list()
        note = "" if result == name else "（原名已存在，自动改为「%s」）" % result
        self.log("已保存方案「%s」%s" % (result, note), "ok")
        self._preset_note.configure(
            text="方案存到：%s" % store.path, fg=FG_FAINT)

    def preset_overwrite(self):
        """覆盖当前选中的方案。"""
        name = self._preset_sel or self._preset_name.get().strip()
        p = self._preset_store().get(name) if name else None
        if p is None:
            self.log("请先在列表里选中要覆盖的方案", "warn")
            return
        if not messagebox.askyesno(
                "覆盖方案", "确定用当前灯光覆盖方案「%s」？" % name, parent=self):
            return
        ok, result, was_over = self._preset_store().add(
            name, self.snapshot_lighting(), self._device_key(), overwrite=True)
        if not ok:
            self.log("覆盖失败：%s" % result, "err")
            return
        self._preset_sel = result
        self._draw_preset_list()
        self.log("已覆盖方案「%s」" % result, "ok")

    def preset_apply(self, name=None, silent=False):
        """把选中的方案下发到键盘。"""
        name = name or self._preset_sel
        p = self._preset_store().get(name) if name else None
        if p is None:
            self.log("请先在列表里选中要应用的方案", "warn")
            return False
        if self.dev is None:
            self.log("还没连接键盘", "warn")
            return False

        cur = self._device_key()
        d = p.device or {}
        if cur.get("keyboard_uid") and d.get("keyboard_uid") \
                and cur["keyboard_uid"] != d["keyboard_uid"]:
            self.log("注意：方案「%s」是给另一把键盘存的，仍会尝试应用"
                     % name, "warn")

        data = p.data or {}
        applied, failed = [], []

        # 1) 分区作用范围 + 两侧颜色
        try:
            zm = data.get("zone_mode")
            if zm in ("both", "key", "acc"):
                self._zone_mode.set(zm)
            for z, colour in (data.get("zone_colors") or {}).items():
                if z in self._zone_colors and isinstance(colour, (list, tuple)) \
                        and len(colour) == 3:
                    self._zone_colors[z] = tuple(int(x) for x in colour)
            for z, v in (data.get("zone_val") or {}).items():
                if z in self._zone_val:
                    self._zone_val[z] = int(v)
            for z in self._zone_capable:
                self.push_zone(z)
            applied.append("分区")
        except Exception as exc:
            failed.append("分区（%s）" % exc)

        # 2) 轴灯：灯效 / 速度 / 颜色亮度
        try:
            if self.state is None:
                self.state = self.dev.read_lighting()
            changes = {}
            for k in ("effect", "speed", "hue", "sat", "val"):
                if data.get(k) is not None:
                    changes[k] = data[k]
            if changes:
                if self.queue_state(**changes) \
                        and self.apply_state_to_device(quiet=True):
                    applied.append("轴灯")
        except Exception as exc:
            failed.append("轴灯（%s）" % exc)

        # 3) 已知的逐键颜色：未知灯位不会以默认黑色写回设备
        try:
            led_data = data.get("led_colors") or {}
            if led_data:
                by_global = {led.global_index: led for led in self.leds}
                changed = set()
                for raw_index, colour in led_data.items():
                    try:
                        global_index = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    led = by_global.get(global_index)
                    if led is None or not isinstance(colour, (list, tuple)) \
                            or len(colour) != 3:
                        continue
                    hsv = tuple(int(x) for x in colour)
                    self._led_colors[led.index] = hsv
                    self._led_known.add(global_index)
                    changed.add(global_index)
                if changed:
                    self.push_leds(changed=changed)
                    applied.append("逐键颜色×%d" % len(changed))
        except Exception as exc:
            failed.append("逐键颜色（%s）" % exc)

        # 4) 配件灯：逐条灯条的灯效 / 颜色 / 亮度 / 速度
        try:
            strips_data = data.get("strips") or []
            if strips_data:
                live = {s.index: s for s in self._amk_strips()}
                done = 0
                for item in strips_data:
                    s = live.get(item.get("index"))
                    if s is None:
                        continue
                    mode = item.get("mode")
                    if mode is not None:
                        self.dev.set_strip_mode(s, int(mode))
                    h = item.get("hue")
                    sa = item.get("sat")
                    v = item.get("val")
                    sp = item.get("speed")
                    if h is not None and sa is not None and v is not None:
                        # 只有自定义档才吃自选色 → force 自动切档
                        self.dev.set_strip_color(s, int(h), int(sa), int(v),
                                                 on=int(v) > 0, force=True)
                    if sp is not None:
                        self.dev.set_strip_led_speed(s, int(sp))
                    done += 1
                if done:
                    applied.append("配件灯×%d" % done)
                    self._refresh_strip_bar()
        except Exception as exc:
            failed.append("配件灯（%s）" % exc)

        if applied:
            self.log("已应用方案「%s」：%s" % (name, "、".join(applied)), "ok")
        if failed:
            self.log("方案「%s」部分未生效：%s" % (name, "；".join(failed)), "warn")
        self._sync_widgets()
        self.refresh_zone_panel()
        if not silent:
            self._draw_preset_list()
        return bool(applied)

    def preset_delete(self):
        name = self._preset_sel
        p = self._preset_store().get(name) if name else None
        if p is None:
            self.log("请先在列表里选中要删除的方案", "warn")
            return
        if not messagebox.askyesno(
                "删除方案", "确定删除方案「%s」？此操作不可撤销。" % name,
                parent=self):
            return
        ok, err = self._preset_store().remove(name)
        if not ok:
            self.log("删除失败：%s" % err, "err")
            return
        self.log("已删除方案「%s」" % name, "ok")
        self._preset_sel = None
        self._preset_name.set("")
        self._draw_preset_list()

    def preset_rename(self):
        name = self._preset_sel
        p = self._preset_store().get(name) if name else None
        if p is None:
            self.log("请先在列表里选中要重命名的方案", "warn")
            return
        new = self._preset_name.get().strip()
        if not new:
            self.log("请在上方输入框里填新名字，再点「重命名」", "warn")
            return
        ok, err, result = self._preset_store().rename(name, new)
        if not ok:
            self.log("重命名失败：%s" % err, "err")
            return
        if result == name:
            self.log("名字没有变化", "warn")
            return
        self.log("已把「%s」重命名为「%s」" % (name, result), "ok")
        self._preset_sel = result
        self._preset_name.set(result)
        self._draw_preset_list()

    def preset_duplicate(self):
        name = self._preset_sel
        p = self._preset_store().get(name) if name else None
        if p is None:
            self.log("请先在列表里选中要复制的方案", "warn")
            return
        ok, result, _ = self._preset_store().duplicate(name)
        if not ok:
            self.log("复制失败：%s" % result, "err")
            return
        self._preset_sel = result
        self._preset_name.set(result)
        self._draw_preset_list()
        self.log("已复制为「%s」" % result, "ok")

    def preset_move(self, delta):
        name = self._preset_sel
        if not name:
            self.log("请先选中一个方案", "warn")
            return
        if self._preset_store().move(name, delta):
            self._draw_preset_list()

    def apply_preset(self, mapping):
        if self.dev is None:
            return
        backend = self.dev.lighting_backend
        if backend not in mapping:
            self.log("当前后端没有该预设", "warn")
            return
        if backend == "amk":
            axis_eff = mapping.get("amk")
            strip_eff = mapping.get("strip")
            applied = []
            if axis_eff is not None:
                ids = getattr(self, "_effect_ids", [])
                if ids and axis_eff in ids:
                    self.effect_combo.current(ids.index(axis_eff))
                if self.queue_state(effect=axis_eff) \
                        and self.apply_state_to_device(quiet=True):
                    applied.append("轴灯")
            if strip_eff is not None:
                strips = self._amk_strips()
                done = 0
                for strip in strips:
                    try:
                        if self.dev.set_strip_mode(strip, strip_eff):
                            done += 1
                    except Exception as exc:
                        self.log("灯条 %d 写入失败: %s"
                                 % (strip.index + 1, exc), "err")
                if done:
                    self._refresh_strip_bar()
                    applied.append("配件灯 %d/%d 条" % (done, len(strips)))
            if applied:
                self.log("快捷预设已应用：%s" % "、".join(applied), "ok")
            else:
                self.log("当前固件没有可用的快捷预设通道", "warn")
            return

        eff = mapping.get(backend)
        if eff is None:
            self.log("当前后端不支持这个快捷预设", "warn")
            return
        ids = getattr(self, "_effect_ids", [])
        if ids and eff in ids:
            self.effect_combo.current(ids.index(eff))
        self.queue_state(effect=eff)

    # ---- 状态下发（节流） -------------------------------------------
    def queue_state(self, **changes):
        if self.dev is None or self.dev.lighting_backend is None:
            return False
        if self.state is None:
            try:
                self.state = self.dev.read_lighting()
            except Exception as exc:
                self.log("读取当前灯光状态失败: %s" % exc, "err")
                return False
        for key, value in changes.items():
            setattr(self.state, key, value)
        if self._pending is None:
            self._pending = {}
        self._pending.update(changes)
        self._set_state_dirty(True)
        if self.live_var.get():
            if self._pending_job is None:
                self._pending_job = self.after(40, self._flush)
        self.draw_layout()
        return True

    def _flush(self):
        self._pending_job = None
        self.apply_state_to_device(quiet=True)

    def apply_state_to_device(self, quiet=False):
        """把全局灯光草稿提交到设备，但不请求固件持久化。"""
        if self.dev is None or self.dev.lighting_backend is None:
            self.log("尚未连接可写灯光后端", "warn")
            return False

        if self._pending_job is not None:
            try:
                self.after_cancel(self._pending_job)
            except Exception:
                pass
            self._pending_job = None
        pending = dict(self._pending or {})
        if not pending:
            if not self._state_dirty:
                if not quiet:
                    self.log("没有待应用的全局灯光修改", "info")
                return True
            if self.state is None:
                self.log("没有可提交的全局灯光状态", "err")
                return False
            pending = {
                key: getattr(self.state, key)
                for key in ("effect", "speed", "hue", "sat", "val")
            }

        try:
            self.state = self.dev.set_all(**pending)
        except Exception as exc:
            self.log("应用全局灯光失败: %s" % exc, "err")
            self._set_state_dirty(True)
            return False

        self._pending = None
        self._set_state_dirty(False)
        self._sync_widgets()
        if not quiet:
            self.log("全局灯光已应用到设备", "ok")
        return True

    def save_to_device(self):
        if self.dev is None:
            return
        if self._state_dirty and not self.apply_state_to_device():
            return
        try:
            ok, resp = self.dev.save()
        except Exception as exc:
            self.log("保存失败: %s" % exc, "err")
            return
        if ok:
            self.log("已请求固件保存当前灯光配置", "ok")
        else:
            self.log("固件未处理保存命令（响应 %s）。AMK 固件通常是即改即存，"
                     "可忽略。" % resp[:2], "warn")

    def push_leds(self, changed=None):
        """把画布颜色推到键盘。

        ``changed`` 可选：一组"刚被画笔涂过"的 ``global_index``，只推送
        这些灯（拖拽场景避免全量）。内部再做一次**与上次推送的差量**，
        所以即便不传 ``changed`` 也不会重复写没变的灯。
        """
        if self.dev is None or not self.leds:
            return

        # AMK 通道：走矩阵逐键上色（会自动切到自定义档）
        if self.dev.lighting_backend == "amk":
            if not self.dev.perkey_supported():
                self.log("该固件不支持逐键上色（矩阵通道不可用）", "warn")
                return
            if not hasattr(self, "_pushed"):
                self._pushed = {}
            try:
                target = {}
                allowed = (set(self._led_known)
                           if changed is None else set(changed))
                if changed is None and not allowed:
                    self.log("还没有已知的逐键颜色，请先点按、填充或从键盘读取",
                             "warn")
                    return
                for led in self.leds:
                    if led.row is None or led.col is None:
                        continue          # 配件灯不走这里
                    gi = led.global_index
                    if gi not in allowed:
                        continue
                    target[gi] = tuple(self._led_colors[led.index])
                # 只发与上次推送**不一样**的灯
                diff = {gi: hsv for gi, hsv in target.items()
                        if self._pushed.get(gi) != hsv}
                if not diff:
                    return
                ok, switched = self.dev.perkey_set(diff)
                if switched:
                    self._pk_custom_on = True
                self._pushed.update(diff)
                self._led_known.update(diff)
                self.log("逐键推送 %d 颗%s"
                         % (ok, "（已切到自定义档）" if switched else ""), "ok")
            except Exception as exc:
                self.log("逐键推送失败: %s" % exc, "err")
            return

        try:
            if self.state is None:
                self.state = self.dev.read_lighting()
            if self.state.effect != effects.VIALRGB_DIRECT:
                self.dev.vialrgb_set_mode(effects.VIALRGB_DIRECT)
                self.state.effect = effects.VIALRGB_DIRECT
                ids = getattr(self, "_effect_ids", [])
                if ids and effects.VIALRGB_DIRECT in ids:
                    self.effect_combo.current(ids.index(effects.VIALRGB_DIRECT))
                    self.effect_pos.configure(
                        text="%d / %d" % (effects.VIALRGB_DIRECT, ids[-1]))
            if changed is None:
                sent = self.dev.vialrgb_push(self._led_colors)
                changed_leds = self.leds
            else:
                wanted = set(changed)
                pairs = [
                    (led.index, tuple(self._led_colors[led.index]))
                    for led in self.leds
                    if led.global_index in wanted
                ]
                sent = self.dev.vialrgb_push_pairs(pairs)
                changed_leds = [
                    led for led in self.leds if led.global_index in wanted]
            self._led_known.update(led.global_index for led in changed_leds)
            self.log("逐键推送 %d 颗灯" % sent, "ok")
        except Exception as exc:
            self.log("逐键推送失败: %s" % exc, "err")

    def _schedule_pk_push(self, global_index):
        """拖拽时的**防抖推送**：攒 120ms 的改动，一次发出去。"""
        if not hasattr(self, "_pk_pending") or self._pk_pending is None:
            self._pk_pending = set()
        self._pk_pending.add(global_index)
        if getattr(self, "_pk_push_job", None) is not None:
            try:
                self.after_cancel(self._pk_push_job)
            except Exception:
                pass
        self._pk_push_job = self.after(120, self._flush_pk_push)

    def _flush_pk_push(self):
        self._pk_push_job = None
        changed = self._pk_pending
        self._pk_pending = set()
        self.push_leds(changed=changed)

    # ==================================================================
    def update_info(self):
        if self.dev is None:
            self.info_text.delete("1.0", "end")
            return
        try:
            data = self.dev.describe()
        except Exception as exc:
            self.log("读取设备信息失败: %s" % exc, "err")
            return
        lines = []
        order = [
            ("name", "键盘名称"), ("manufacturer", "厂商"), ("product", "产品"),
            ("vendor_id", "VID"), ("product_id", "PID"), ("serial", "序列号"),
            ("via_protocol", "VIA 协议版本"), ("vial_protocol", "Vial 协议版本"),
            ("keyboard_uid", "键盘 UID"), ("vialrgb_flag", "VialRGB 标志"),
            ("definition_lighting", "定义里的 lighting 字段"),
            ("lighting_backend", "启用后端"), ("rgb_protocol", "VialRGB 协议"),
            ("rgb_max_brightness", "最大亮度"), ("num_leds", "LED 数量"),
            ("matrix", "矩阵"), ("definition_keys", "定义键数"),
            ("amk_rgb_matrix", "amk_rgb_matrix"),
            ("indicator", "指示灯定义"),
            ("zones", "分区 LED 数"),
            ("path", "HID 路径"),
        ]
        for key, label in order:
            val = data.get(key)
            if key in ("vendor_id", "product_id") and val is not None:
                val = "0x%04X" % val
            lines.append("%-22s %s" % (label, val))
        if data.get("definition_error"):
            lines.append("")
            lines.append("⚠ 键盘定义读取失败（不影响其他功能，重插一次通常可恢复）：")
            lines.append("   %s" % data["definition_error"])
        if data.get("supported_effects"):
            lines.append("")
            lines.append("固件支持的 VialRGB 灯效：")
            for eid in data["supported_effects"]:
                lines.append("   %3d  %s" % (eid, self.dev.effect_label(eid)))
        lines.append("")
        lines.append("探测过程：")
        for name, note in data.get("detect_log", []):
            lines.append("   %-9s %s" % (name, note))
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(lines))

    # ==================================================================
    def _tick(self):
        try:
            if self.anim_var.get() and self._anim_job is None:
                self._anim_job = self.after(33, self._anim_step)
        except Exception:
            pass
        self.after(500, self._tick)

    def _on_close(self):
        try:
            if self._anim_job:
                self.after_cancel(self._anim_job)
                self._anim_job = None
        except Exception:
            pass
        try:
            async_w = getattr(self, "_async", None)
            if async_w is not None:
                async_w.stop()
        except Exception:
            pass
        if self.dev is not None:
            self.dev.close()
        self.destroy()

    def run_selftest(self):
        """构建完整界面并真连一次设备，报告各控件状态后退出。"""
        report = []
        self.update_idletasks()
        self.update()
        self.refresh_devices()
        self.update()
        devs = getattr(self, "_vial_devs", [])
        report.append("UI 字体: %s / 等宽 %s" % (UI_FONT, MONO_FONT))
        report.append("页签数: %d (%s)" % (len(self._tab_labels),
                                          " / ".join(self._tab_labels)))
        report.append("发现 Vial 设备: %d" % len(devs))
        if devs:
            report.append("设备: %s" % devs[0].display_name)
            self.connect_device(devs[0])
            self.update()
            report.append("启用后端: %s" % self.dev.lighting_backend)
            report.append("灯光状态: %r" % (self.state,))
            report.append("灯效下拉项: %d" % len(self.effect_combo.cget("values")))
            report.append("矩阵网格键数: %d" % (
                len(self.layout_matrix.keys) if self.layout_matrix else 0))
            report.append("KLE 键数: %d" % (
                len(self.layout_kle.keys) if self.layout_kle else 0))
            report.append("配列画布图元: %d" % len(self.layout_canvas.find_all()))
            report.append("信息页字符数: %d" % len(self.info_text.get("1.0", "end")))
            self.draw_leds()
            report.append("逐键画布图元: %d" % len(self.pk_canvas.find_all()))

            # --- 分区页 ---
            self.select_tab(1, animate=False)
            self.update()
            zones, reason = self.dev.zone_support()
            report.append("分区能力: %r" % (zones,))
            report.append("分区说明: %s" % reason)
            report.append("分区 LED 计数: %r" % (self._zone_counts,))
            report.append("分区提示文字: %s" % self.zone_notice.cget("text")[:60])
            notice_fg = self.zone_notice.cget("fg")
            report.append("分区提示颜色: %s (%s)" % (
                notice_fg, "绿=支持" if notice_fg == OK else "黄=不支持"))
            for z in ("key", "acc"):
                d = self._zone_cards_data.get(z, {})
                report.append("  %s 卡片: 计数=%s 色=%s 亮度=%s" % (
                    ZONE_LABELS.get(z, z), d.get("count") and d["count"].cget("text"),
                    d.get("hex") and d["hex"].get(),
                    d.get("slider") and d["slider"].get()))
            self._zone_mode.set("key")
            self._on_zone_mode("key")
            self.update()
            report.append("切到「仅轴灯」后 key.on=%s acc.on=%s" % (
                self._zone_active("key"), self._zone_active("acc")))
            self._zone_mode.set("both")
            self._on_zone_mode("both")
            self.push_zone(None)
            self.update()

            # --- 其余页签 ---
            self._set_layout_view("kle")
            self.select_tab(2, animate=False)
            self.update()
            report.append("物理配列画布图元: %d" % len(self.layout_canvas.find_all()))
            report.append("告警文本: %s" % (
                self.layout_warn.cget("text")[:30] or "(空)"))

            # --- 预设方案面板 ---
            store = self._preset_store()
            report.append("预设方案库: %s (%d 条)" % (
                store.path, len(store.presets)))
            self._refresh_preset_list()
            self.update()
            rows = self._preset_rows()
            report.append("预设列表行数: %d" % len(rows))
            snap = self.snapshot_lighting()
            report.append("快照字段: %s" % ",".join(
                sorted(k for k in snap if k != "strips")))
            report.append("快照配件灯条: %d" % len(snap.get("strips") or []))
            self._draw_preset_list()
            report.append("预设画布图元: %d" % len(
                self._preset_list.find_all()))

            self.select_tab(4, animate=False)
            self.update()
            report.append("页签切换: %d" % self._current_index())
            self.disconnect()
        else:
            self.select_tab(1, animate=False)
            self.update()
            report.append("未连接：分区提示=%s" % self.zone_notice.cget("text"))
        self.update()
        self.destroy()
        print("SELFTEST OK")
        for line in report:
            print("  " + line)
        return 0


def _short_backend(backend):
    return {
        "amk": "AMK 通道",
        "vialrgb": "VialRGB",
        "via": "VIA 通道",
        None: "无灯光通道",
    }.get(backend, str(backend))


def _backend_text():
    from .transport import backend_description

    return backend_description()


def main(selftest=False):
    app = App(selftest=selftest)
    if selftest:
        return app.run_selftest()
    app.mainloop()
    return 0
