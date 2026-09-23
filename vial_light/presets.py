"""灯光配置方案的持久化存储。

用户可以把自己调好的一整套灯光（轴灯灯效 / 速度 / 颜色 + 配件灯每条灯条的
灯效 / 颜色 / 亮度 / 速度 + 分区作用范围 …）存成一个**命名方案**，
之后一键切回。

存储形态是**一个 JSON 文件**，放在用户的配置目录里：

``%APPDATA%\\vial-matrix-light\\presets.json``  （Windows）
``~/.config/vial-matrix-light/presets.json``   （其它平台）

文件结构::

    {
      "version": 1,
      "presets": [
        {"name": "夜深人静", "created": 1695000000, "updated": 1695000000,
         "device": {"vendor_id": 19800, "product_id": 19019,
                    "keyboard_uid": "0x...", "backend": "amk"},
         "data": { ... 见 snapshot 的字段 ... }}
      ]
    }

``data`` 里的字段**全部是可选的**：读回时缺哪个就用当前设备的默认值补，
这样即使以后加了新字段，老文件也不会读崩。

设计取舍：

* **方案按键盘限定**（用 ``keyboard_uid`` / VID:PID 匹配），但**不强制**——
  换了键盘仍然能看到、能应用，只是会给出提示。用户的方案不该因为换了把
  键盘就凭空消失。
* **纯 JSON，不用 pickle**：可读、可手改、可分享，也不会有反序列化风险。
* **原子写入**（先写临时文件再 ``os.replace``）：避免写一半断电把整个
  方案库写坏。
"""

import json
import os
import shutil
import tempfile
import time

#: 当前文件格式版本。以后结构变了就 +1，读取时按版本做兼容。
SCHEMA_VERSION = 1

#: 允许出现在方案名里的最大长度（防止 UI 被撑爆）
NAME_MAX = 40


def config_dir():
    """返回存放方案的目录（不存在则创建）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = os.path.join(appdata, "vial-matrix-light")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config",
                            "vial-matrix-light")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def config_path():
    return os.path.join(config_dir(), "presets.json")


def clean_name(name):
    """把用户输入洗成一个安全的方案名。

    允许中文、字母、数字、空格、``-`` ``_`` ``.`` ``(`` ``)``；
    其它字符（尤其是路径分隔符与控制字符）一律剔除。
    """
    if name is None:
        return ""
    out = []
    for ch in str(name).strip():
        if ch in "\\/:*?\"<>|":
            continue
        if ord(ch) < 32:
            continue
        out.append(ch)
    return "".join(out).strip()[:NAME_MAX]


class Preset(object):
    """一条命名方案。"""

    __slots__ = ("name", "data", "device", "created", "updated")

    def __init__(self, name, data=None, device=None, created=None, updated=None):
        self.name = name
        self.data = data or {}
        self.device = device or {}
        self.created = created or time.time()
        self.updated = updated or self.created

    def as_dict(self):
        return {
            "name": self.name,
            "created": self.created,
            "updated": self.updated,
            "device": dict(self.device),
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            return None
        name = clean_name(raw.get("name"))
        if not name:
            return None
        data = raw.get("data")
        device = raw.get("device")
        return cls(
            name,
            data=data if isinstance(data, dict) else {},
            device=device if isinstance(device, dict) else {},
            created=raw.get("created") or time.time(),
            updated=raw.get("updated") or raw.get("created") or time.time(),
        )

    def summary(self):
        """一句话描述这条方案（给列表用）。"""
        d = self.data
        bits = []
        axis = d.get("effect")
        if axis is not None:
            bits.append("轴灯 %s" % axis)
        strips = d.get("strips") or []
        if strips:
            modes = []
            for s in strips:
                m = s.get("mode")
                if m is not None and m not in modes:
                    modes.append(m)
            if len(modes) == 1:
                bits.append("配件灯 %s" % modes[0])
            elif modes:
                bits.append("配件灯 %d 种档位" % len(modes))
        if d.get("zone_mode"):
            bits.append({"both": "联动", "key": "仅轴灯",
                         "acc": "仅配件灯"}.get(d["zone_mode"], d["zone_mode"]))
        return " · ".join(bits) if bits else "（空方案）"


class PresetStore(object):
    """方案库：读 / 写 / 增删改。"""

    def __init__(self, path=None):
        self.path = path or config_path()
        self.presets = []
        self.error = None
        self.load()

    # ---- 磁盘 ----------------------------------------------------------
    def load(self):
        self.presets = []
        self.error = None
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            # 文件坏了不能让程序起不来 —— 记下原因，当作空库
            self.error = "方案文件无法解析（%s），已按空库载入" % exc
            return
        if isinstance(raw, list):
            raw = {"presets": raw}          # 兼容最早期只有数组的形态
        if not isinstance(raw, dict):
            self.error = "方案文件结构不是对象，已按空库载入"
            return
        for item in raw.get("presets") or []:
            p = Preset.from_dict(item)
            if p is not None:
                self.presets.append(p)

    def save(self):
        """原子写入，失败返回 (False, 原因)。"""
        payload = {"version": SCHEMA_VERSION,
                   "presets": [p.as_dict() for p in self.presets]}
        try:
            d = os.path.dirname(self.path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".presets-", suffix=".tmp",
                                       dir=d or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:
            return False, str(exc)
        return True, ""

    def backup(self):
        """把现有文件备份成 ``presets.bak.json``（存在才备份）。"""
        if not os.path.exists(self.path):
            return None
        dst = self.path + ".bak"
        try:
            shutil.copy2(self.path, dst)
            return dst
        except Exception:
            return None

    # ---- 查询 ----------------------------------------------------------
    def names(self):
        return [p.name for p in self.presets]

    def get(self, name):
        for p in self.presets:
            if p.name == name:
                return p
        return None

    def has(self, name):
        return self.get(name) is not None

    def index_of(self, name):
        for i, p in enumerate(self.presets):
            if p.name == name:
                return i
        return -1

    @staticmethod
    def unique_name(base, taken):
        """在 ``taken`` 里找一个不和 ``base`` 冲突的名字。"""
        name = clean_name(base) or "未命名方案"
        if name not in taken:
            return name
        for i in range(2, 1000):
            cand = "%s (%d)" % (name, i)
            if cand not in taken:
                return cand
        return "%s %d" % (name, int(time.time()))

    # ---- 增删改 --------------------------------------------------------
    def add(self, name, data, device=None, overwrite=False, unique=True):
        """新增方案。

        返回 ``(ok, 实际名字 或 错误信息, 是否覆盖了已有项)``。

        * ``overwrite=False`` 且重名 → ``unique=True`` 时自动改名，
          否则返回失败。
        * ``overwrite=True`` 且重名 → 覆盖（``created`` 保留，``updated`` 刷新）。
        """
        name = clean_name(name)
        if not name:
            return False, "方案名不能为空", False
        existing = self.get(name)
        if existing is not None:
            if not overwrite:
                if not unique:
                    return False, "已存在同名方案「%s」" % name, False
                name = self.unique_name(name, self.names())
                existing = None
        if existing is not None:
            existing.data = dict(data)
            existing.device = dict(device or {})
            existing.updated = time.time()
            ok, err = self.save()
            if not ok:
                return False, "写入失败：%s" % err, False
            return True, name, True
        p = Preset(name, data=dict(data), device=dict(device or {}))
        self.presets.append(p)
        ok, err = self.save()
        if not ok:
            self.presets.pop()            # 落盘失败就回滚内存，保持一致
            return False, "写入失败：%s" % err, False
        return True, name, False

    def remove(self, name):
        idx = self.index_of(name)
        if idx < 0:
            return False, "没有找到方案「%s」" % name
        gone = self.presets.pop(idx)
        ok, err = self.save()
        if not ok:
            self.presets.insert(idx, gone)   # 回滚
            return False, "写入失败：%s" % err
        return True, ""

    def rename(self, old, new):
        """改名。重名时返回失败（不改名去撞别人）。"""
        idx = self.index_of(old)
        if idx < 0:
            return False, "没有找到方案「%s」" % old, old
        new = clean_name(new)
        if not new:
            return False, "新名字不能为空", old
        if new == old:
            return True, "", old
        if self.has(new):
            return False, "已存在同名方案「%s」" % new, old
        self.presets[idx].name = new
        self.presets[idx].updated = time.time()
        ok, err = self.save()
        if not ok:
            self.presets[idx].name = old     # 回滚
            return False, "写入失败：%s" % err, old
        return True, "", new

    def duplicate(self, name):
        """复制一份（名字自动加「副本」后缀）。"""
        src = self.get(name)
        if src is None:
            return False, "没有找到方案「%s」" % name, name
        new = self.unique_name("%s 副本" % name, self.names())
        return self.add(new, src.data, src.device, overwrite=False, unique=True)

    def move(self, name, delta):
        """在列表里上移 / 下移（``delta`` 为 -1 / +1），保持用户自定义顺序。"""
        idx = self.index_of(name)
        if idx < 0:
            return False
        new_idx = max(0, min(len(self.presets) - 1, idx + delta))
        if new_idx == idx:
            return False
        self.presets.insert(new_idx, self.presets.pop(idx))
        self.save()
        return True

    def reorder(self, names):
        """按给定的名字顺序重排（缺失的保持在后）。"""
        by_name = {p.name: p for p in self.presets}
        ordered, seen = [], set()
        for n in names:
            if n in by_name and n not in seen:
                ordered.append(by_name[n])
                seen.add(n)
        for p in self.presets:
            if p.name not in seen:
                ordered.append(p)
        self.presets = ordered
        return self.save()
