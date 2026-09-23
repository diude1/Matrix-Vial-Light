#!/usr/bin/env python
"""vial-matrix-light 命令行工具。

方便脚本化 / 批处理调用，也方便在图形界面不可用时排查。

示例::

    python mvl.py list                       # 列出 Vial 设备
    python mvl.py info                       # 设备与后端信息
    python mvl.py get                        # 读当前灯光
    python mvl.py set --effect 5 --val 200   # 设置灯效与亮度
    python mvl.py color --hex "#ff5500"      # 设置颜色
    python mvl.py preset 呼吸                # 应用快捷预设
    python mvl.py effects                    # 列出可选灯效
    python mvl.py raw "08 80"                # 发原始报文看响应
    python mvl.py def --save vial.json       # 导出键盘定义
    python mvl.py off / on                   # 关灯 / 最亮
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _force_utf8_when_redirected():
    """输出被重定向到文件/管道时，强制 UTF-8，避免中文乱码。"""
    try:
        if sys.stdout.isatty():
            return
        import io

        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


_force_utf8_when_redirected()

from vial_light import colors as C  # noqa: E402
from vial_light import effects  # noqa: E402
from vial_light import presets as P  # noqa: E402
from vial_light.device import VialDevice, VialError, open_first_vial  # noqa: E402
from vial_light.transport import backend_description  # noqa: E402


def _open(args):
    devs = VialDevice.discover() if not args.all else VialDevice.discover_all_raw()
    if not devs:
        raise VialError("没有找到 Vial 设备（可加 --all 列出所有 raw HID 接口）")
    # --device 显式指定时优先；否则用全局 --index。
    # 注意子命令也有 --index（灯条编号），解析后会把全局值覆盖掉，
    # 所以这里对越界/None 做兜底，回落到第 0 个设备。
    idx = args.device
    if idx is None:
        idx = getattr(args, "index", 0)
    if not isinstance(idx, int) or not (0 <= idx < len(devs)):
        idx = 0
    info = devs[idx]
    dev = VialDevice(info).open()
    dev.read_versions()
    dev.detect_lighting()
    return dev


def _print_state(state, dev):
    if state is None:
        print("没有可用的照明后端")
        return
    en, zh = effects.vialrgb_name(state.effect) if state.backend == "vialrgb" else effects.qmk_mode_name(state.effect)
    print("后端      : %s" % state.backend)
    print("灯效      : %s  (%s)" % (state.effect, zh))
    print("亮度      : %s / %s" % (state.val, state.brightness_max))
    print("速度      : %s" % state.speed)
    print("色相/饱和 : %s / %s" % (state.hue, state.sat))
    print("等效颜色  : %s" % C.hsv_to_hex(state.hue, state.sat, state.val))


def cmd_list(args):
    devs = VialDevice.discover_all_raw() if args.all else VialDevice.discover()
    print("HID 后端: %s" % backend_description())
    if not devs:
        print("没有找到设备")
        return 1
    for i, d in enumerate(devs):
        print("[%d] %s" % (i, d.display_name))
        print("     usage_page=0x%04X usage=0x%02X serial=%r" % (
            d.usage_page or 0, d.usage or 0, d.serial))
    return 0


def cmd_info(args):
    dev = _open(args)
    try:
        data = dev.describe()
        width = max(len(k) for k in data)
        for key in sorted(data):
            if key in ("supported_effects", "detect_log"):
                continue
            print("%-*s : %s" % (width, key, data[key]))
        print("")
        print("探测过程:")
        for name, note in data.get("detect_log", []):
            print("   %-9s %s" % (name, note))
        if data.get("supported_effects"):
            print("")
            print("固件支持的 VialRGB 灯效: %s" % data["supported_effects"])
    finally:
        dev.close()
    return 0


def cmd_get(args):
    dev = _open(args)
    try:
        if dev.lighting_backend is None:
            print("该固件没有暴露 raw HID 灯光通道")
            return 1
        _print_state(dev.read_lighting(), dev)
    finally:
        dev.close()
    return 0


def cmd_set(args):
    dev = _open(args)
    try:
        kwargs = {}
        if args.effect is not None:
            kwargs["effect"] = args.effect
        if args.val is not None:
            kwargs["val"] = args.val
        if args.speed is not None:
            kwargs["speed"] = args.speed
        if args.hue is not None:
            kwargs["hue"] = args.hue
        if args.sat is not None:
            kwargs["sat"] = args.sat
        if not kwargs:
            print("没有需要设置的字段")
            return 1
        state = dev.set_all(**kwargs)
        _print_state(state, dev)
        if args.save:
            ok, resp = dev.save()
            print("保存: %s" % ("已请求" if ok else "固件未处理 (%s)" % resp[:2]))
    finally:
        dev.close()
    return 0


def cmd_color(args):
    dev = _open(args)
    try:
        if args.hex:
            r, g, b = C.hex_to_rgb(args.hex)
            h, s, v = C.rgb_to_hsv(r, g, b)
        elif args.hsv:
            parts = [int(x) for x in args.hsv.replace(",", " ").split()]
            h, s, v = (parts + [255, 255])[:3]
        else:
            print("需要 --hex 或 --hsv")
            return 1
        if args.no_val:
            v = None
        state = dev.set_all(hue=h, sat=s, val=v)
        _print_state(state, dev)
    finally:
        dev.close()
    return 0


def cmd_preset(args):
    dev = _open(args)
    try:
        name = args.name
        mapping = None
        for preset_name, m in effects.QUICK_PRESETS:
            if preset_name == name:
                mapping = m
                break
        if mapping is None:
            print("未知预设: %s" % name)
            print("可用: %s" % ", ".join(p for p, _ in effects.QUICK_PRESETS))
            return 1
        backend = dev.lighting_backend
        if backend not in mapping:
            print("当前后端 %s 没有该预设" % backend)
            return 1
        state = dev.set_all(effect=mapping[backend])
        _print_state(state, dev)
    finally:
        dev.close()
    return 0


def cmd_effects(args):
    dev = _open(args)
    try:
        backend = dev.lighting_backend
        print("后端: %s" % backend)
        ids = dev.effect_ids()
        for i in ids:
            print("  %3d  %s" % (i, dev.effect_label(i)))
    finally:
        dev.close()
    return 0


def cmd_off(args):
    dev = _open(args)
    try:
        state = dev.set_all(val=0)
        _print_state(state, dev)
    finally:
        dev.close()
    return 0


def cmd_on(args):
    dev = _open(args)
    try:
        state = dev.set_all(val=255)
        _print_state(state, dev)
    finally:
        dev.close()
    return 0


def cmd_raw(args):
    dev = _open(args)
    try:
        text = args.hex.replace(",", " ").strip()
        payload = bytes(int(b, 16) for b in text.split() if b)
        resp = dev.raw_request(payload)
        print("发送 %d 字节: %s" % (len(payload), " ".join("%02X" % b for b in payload)))
        print("响应 %d 字节: %s" % (len(resp), " ".join("%02X" % b for b in resp)))
    finally:
        dev.close()
    return 0


def cmd_def(args):
    dev = _open(args)
    try:
        definition = dev.read_definition()
        text = json.dumps(definition, ensure_ascii=False, indent=2)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("已写入 %s (%d 字节)" % (args.save, len(text.encode("utf-8"))))
        else:
            print(text)
    finally:
        dev.close()
    return 0


def cmd_write_test(args):
    """往指定 value_id 写一个值再读回，用于自家固件的行为验证。"""
    dev = _open(args)
    try:
        vid = int(args.value_id, 0)
        vals = [int(x, 0) for x in args.values] if args.values else None
        getter = dev._send
        import struct as _s

        from vial_light.device import CMD_LIGHTING_GET_VALUE, CMD_LIGHTING_SET_VALUE

        before = getter(_s.pack("BB", CMD_LIGHTING_GET_VALUE, vid))[2:8]
        print("写入前: %s" % " ".join("%02X" % b for b in before))
        if vals:
            getter(_s.pack("BB", CMD_LIGHTING_SET_VALUE, vid) + bytes(vals))
            after = getter(_s.pack("BB", CMD_LIGHTING_GET_VALUE, vid))[2:8]
            print("写入后: %s" % " ".join("%02X" % b for b in after))
            if args.restore:
                getter(_s.pack("BB", CMD_LIGHTING_SET_VALUE, vid) + bytes(before[: len(vals)]))
                back = getter(_s.pack("BB", CMD_LIGHTING_GET_VALUE, vid))[2:8]
                print("还原后: %s" % " ".join("%02X" % b for b in back))
    finally:
        dev.close()
    return 0


# ---------------------------------------------------------------------------
# 配件灯（AMK 扩展协议）
# ---------------------------------------------------------------------------
def _require_strips(dev):
    if dev.lighting_backend != "amk":
        sys.stderr.write(
            "配件灯需要 AMK 灯光通道，当前后端是 %s\n" % dev.lighting_backend)
        return None
    strips = dev.strips()
    if not strips:
        sys.stderr.write("没有读到配件灯条（固件可能没编译 STRIP 功能）\n")
        return None
    return strips


def cmd_strips(args):
    """列出配件灯条及其当前灯效。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        print("AMK 扩展协议版本: %s" % dev.amk_protocol_version)
        print("配件灯条 %d 组，共 %d 颗灯"
              % (len(strips), sum(s.count for s in strips)))
        print()
        print("  #   起始灯号  灯数  当前灯效")
        for s in strips:
            print("  %-3d %6d %6d  %s"
                  % (s.index + 1, s.start, s.count,
                     effects.strip_effect_label(s.mode)))
        print()
        print("灯效编号表（用 `strips-mode <编号> <灯效>` 切换）:")
        for i, en, zh in effects.STRIP_EFFECTS:
            print("  %2d  %-14s %s" % (i, en, zh))
    finally:
        dev.close()
    return 0


def cmd_strips_color(args):
    """给一条（或全部）灯条上色。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        if args.hsv:
            parts = [int(x) for x in args.hsv.replace(",", " ").split()]
            if len(parts) != 3:
                sys.stderr.write("--hsv 需要三个数：色相,饱和度,亮度\n")
                return 1
            hue, sat, val = parts
        elif args.hex:
            from vial_light import colors as _C
            _hue, _sat, _val = _C.rgb_to_hsv(*_C.hex_to_rgb(args.hex))
            hue, sat, val = int(_hue), int(_sat), int(_val)
        else:
            sys.stderr.write("请给 --hsv 或 --hex\n")
            return 1

        targets = strips if args.strip is None else [strips[args.strip]]
        if args.strip is not None and not (0 <= args.strip < len(strips)):
            sys.stderr.write("灯条编号 %d 不存在\n" % args.strip)
            return 1

        for s in targets:
            ok = dev.set_strip_color(s, hue, sat, val,
                                     on=not args.off, force=args.force)
            print("  %-10s hsv=(%d,%d,%d) -> %s"
                  % (s.name, hue, sat, val, "成功" if ok else "失败"))
        if args.force:
            print("  提示：已强制切到「自定义」档（本机固件只有这一档会采用"
                  "逐灯自选色）。")
        else:
            print("  提示：若灯条当前不是「自定义」档，固件会忽略逐灯颜色。"
                  "加 --force 可自动切档。")
        if args.save:
            ok, _ = dev.save()
            print("  已请求固件保存:", "成功" if ok else "失败")
    finally:
        dev.close()
    return 0


def cmd_strips_mode(args):
    """切换灯条灯效。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        if not (0 <= args.strip < len(strips)):
            sys.stderr.write("灯条编号 %d 不存在（共 %d 条）\n"
                             % (args.strip, len(strips)))
            return 1
        if not (effects.STRIP_EFFECT_MIN <= args.mode <= effects.STRIP_EFFECT_MAX):
            sys.stderr.write("灯效编号需在 %d-%d\n"
                             % (effects.STRIP_EFFECT_MIN, effects.STRIP_EFFECT_MAX))
            return 1
        s = strips[args.strip]
        ok = dev.set_strip_mode(s, args.mode)
        back = dev.strips(force=True)[args.strip].mode
        en, zh = effects.strip_effect_name(args.mode)
        print("  %s 灯效 -> %d %s (%s)   %s"
              % (s.name, args.mode, zh, en, "成功" if ok else "失败"))
        if back == args.mode:
            print("  回读 = %d 一致" % back)
        else:
            print("  回读 = %d 不一致（本机固件对灯效编号会按 10 取模，"
                  "有效范围 %d-%d）"
                  % (back, effects.STRIP_EFFECT_MIN, effects.STRIP_EFFECT_MAX))
        if effects.strip_mode_editable(back):
            print("  该档可被本软件上色（逐灯写生效）。")
        else:
            print("  该档由固件自己渲染；本机固件没有灯条级颜色接口，"
                  "想自选颜色请用灯效 0（自定义）。")
        if args.save:
            ok, _ = dev.save()
            print("  已请求固件保存:", "成功" if ok else "失败")
    finally:
        dev.close()
    return 0


def cmd_strips_read(args):
    """逐颗读出配件灯状态，用于确认和备份。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        for s in strips:
            print("%s  (起始 %d, %d 颗, 灯效 %s)"
                  % (s.name, s.start, s.count,
                     effects.strip_effect_label(s.mode)))
            for i in range(s.count):
                led = dev.read_strip_led(s.start + i)
                if led is None:
                    print("    [%3d] 读取失败" % (s.start + i))
                    continue
                print("    [%3d] h=%-3d s=%-3d v=%-3d  param=0x%02X  %s"
                      % (led.index, led.hue, led.sat, led.val, led.param,
                         led.flag_text()))
        if args.json:
            import json as _json
            data = dev.original_colors()
            path = args.json
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_json.dumps(data, ensure_ascii=False, indent=2))
            print("\n已导出备份: %s" % path)
        for which in ("caps_lock", "num_lock", "scroll_lock"):
            led = dev.read_indicator(which)
            if led is not None:
                print("指示灯 %-12s h=%-3d s=%-3d v=%-3d param=0x%02X"
                      % (which, led.hue, led.sat, led.val, led.param))
    finally:
        dev.close()
    return 0


def cmd_strips_restore(args):
    """从备份文件恢复配件灯状态。"""
    import json as _json

    from vial_light.device import AmkStripLed

    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        with open(args.file, encoding="utf-8") as fh:
            data = _json.load(fh)
        n = 0
        for key, rows in (data.get("strips") or {}).items():
            idx = int(key)
            if idx >= len(strips):
                continue
            for row in rows:
                led = AmkStripLed(*row[:5])
                if dev.set_strip_led(led):
                    n += 1
        for which, row in (data.get("indicators") or {}).items():
            if row:
                dev.set_indicator(which, AmkStripLed(*row[:5]))
        ok, _ = dev.save()
        print("已恢复 %d 颗配件灯，保存请求: %s" % (n, "成功" if ok else "失败"))
    finally:
        dev.close()
    return 0


def cmd_strips_off(args):
    """关闭全部配件灯（逐颗 on=0；这是唯一可靠的关灯方式）。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        total = 0
        for s in strips:
            for i in range(s.count):
                led = dev.read_strip_led(s.start + i)
                if led is None:
                    continue
                if dev.set_strip_led(led.with_flags(on=0)):
                    total += 1
        print("已关闭 %d 颗配件灯" % total)
        print("提示: 轴灯无法用 0x81 关掉（写 0 会被固件改回 1），"
              "要么把亮度设 0，要么逐颗写 on=0。")
    finally:
        dev.close()
    return 0


def cmd_strips_on(args):
    """点亮全部配件灯。"""
    dev = _open(args)
    try:
        strips = _require_strips(dev)
        if strips is None:
            return 1
        total = 0
        for s in strips:
            for i in range(s.count):
                led = dev.read_strip_led(s.start + i)
                if led is None:
                    continue
                if dev.set_strip_led(led.with_flags(on=1)):
                    total += 1
        print("已点亮 %d 颗配件灯" % total)
    finally:
        dev.close()
    return 0


def _strip_targets(dev, index):
    """解析 `--index`：None = 全部灯条，否则那一条。"""
    strips = _require_strips(dev)
    if strips is None:
        return None
    if index is None:
        return strips
    if not (0 <= index < len(strips)):
        sys.stderr.write("灯条编号 %d 不存在（共 %d 条）\n" % (index, len(strips)))
        return None
    return [strips[index]]


def cmd_strips_bright(args):
    """调配件灯亮度 —— 逐灯写每颗灯的 HSV 的 V 分量。

    这是本机固件上唯一能改配件灯亮度的办法：灯条级的
    ``RGB_PARAM_BRIGHT`` 命令（62）实测一律被拒（0x55）。
    """
    dev = _open(args)
    try:
        targets = _strip_targets(dev, args.strip)
        if targets is None:
            return 1
        val = max(0, min(255, args.value))
        n = 0
        for s in targets:
            if dev.set_strip_color(s, s.hue, s.sat, val,
                                   on=val > 0, force=True):
                n += s.count
        print("已把 %d 颗配件灯的亮度设为 %d（逐灯写）" % (n, val))
        print("注意：亮度写进逐灯的 V 分量，因此会顺带把灯条切到"
              "「自定义」档（0）—— 本机固件只有这一档会采用自选值。")
    finally:
        dev.close()
    return 0


def cmd_strips_speed(args):
    """调配件灯的逐灯速度（0-15，4 bit 位域）。"""
    dev = _open(args)
    try:
        targets = _strip_targets(dev, args.strip)
        if targets is None:
            return 1
        val = max(0, min(effects.STRIP_LED_SPEED_MAX, args.value))
        n = 0
        for s in targets:
            if dev.set_strip_led_speed(s, val):
                n += s.count
        print("已把 %d 颗配件灯的速度设为 %d" % (n, val))
        print("注意：逐灯速度只在「自定义」档下被固件采用。")
    finally:
        dev.close()
    return 0


def cmd_axis_diag(args):
    """诊断轴灯通道，并打印真实的 LED 地址地图。

    真机实测（逐颗打断法）得到的权威地图：

    * 全局 **0**      读写都成功但**板上没有灯响应** —— 幽灵灯；
    * 全局 **1–73**   74 颗键位轴灯（由板载灯效引擎渲染）；
    * 全局 **74**     **Caps Lock 指示灯**（键盘定义里明写
      ``"indicator": {"caps_lock": {"index": 74}}``）；
    * 全局 **75–92**  5 条配件灯条；
    * 全局 **93+**    读不到。

    **重要**：键位轴灯**可以逐键上色**，但必须先切到「逐灯自定义」档
    （``matrix_mode()["custom"]``，本机 = 45），否则固件自己渲染灯效、
    写进去的 HSV 会被无视。用 ``mvl.py perkey-fill`` / ``perkey-set``
    即可逐键写色。
    """
    dev = _open(args)
    try:
        if dev.lighting_backend != "amk":
            sys.stderr.write("仅适用于 AMK 通道（当前 %s）\n"
                             % dev.lighting_backend)
            return 1
        info = dev.matrix_info() or {}
        mm = dev.matrix_mode() or {}
        rcmap = dev.matrix_rc_map()
        print("轴灯颗数        : %s" % (info.get("count"),))
        print("矩阵映射灯数    : %s" % (len(rcmap),))
        print("幽灵灯（可读写但无灯）: %s" % (dev.ghost_led_indexes(),))
        print("指示灯（非轴灯）      : %s" % (dev.indicator_indexes(),))
        print("矩阵灯效模式          : %s" % (mm,))
        print("当前是否在自定义档    : %s" % (dev.matrix_in_custom(),))
        print("可逐键上色            : %s" % (dev.perkey_supported(),))
        print("")
        print("逐键上色用法（用数字灯号，键名不可靠）：")
        base = os.path.basename(sys.argv[0])
        print("  %s perkey-fill --hex '#ff0000'      # 所有键刷红" % base)
        print("  %s perkey-set 0=#0000ff 1=#00ff00   # 指定灯号上色" % base)
        print("  %s perkey-read                      # 回读全部键位" % base)
        print("  %s matrix-reset --clear             # 测完还原默认档" % base)
        print("")
        print("已知无效地址（不在矩阵表、且不受控）: 最左列 Esc/Tab/Caps/Shift/Ctrl")
    finally:
        dev.close()
    return 0


def _parse_hsv_hex(text):
    """``#rrggbb`` -> ``(h, s, v)``（0-255 量纲，与设备一致）。"""
    from vial_light import colors as C
    r, g, b = C.hex_to_rgb(text)
    return C.rgb_to_hsv(r, g, b)


def cmd_perkey_fill(args):
    """把所有键位轴灯刷成同一颜色（逐键写，自动切自定义档）。"""
    dev = _open(args)
    try:
        if not dev.perkey_supported():
            sys.stderr.write("当前设备不支持逐键上色（需 AMK 通道 + 矩阵模式）\n")
            return 1
        hue, sat, val = _parse_hsv_hex(args.hex)
        if args.bright is not None:
            val = max(0, min(255, int(args.bright)))
        ok, switched = dev.perkey_fill(hue, sat, val)
        print("逐键写入 %s 颗（目标 %s 颗）%s"
              % (ok, len(dev.matrix_map()),
                 "；已自动切到自定义档" if switched else ""))
    finally:
        dev.close()
    return 0


def cmd_perkey_set(args):
    """按灯号或按键名逐颗上色，形如 ``0=#0000ff q=#00ff00 tab=#ff0000``。"""
    dev = _open(args)
    try:
        if not dev.perkey_supported():
            sys.stderr.write("当前设备不支持逐键上色（需 AMK 通道 + 矩阵模式）\n")
            return 1
        rcmap = dev.matrix_rc_map()
        colors = {}
        for item in args.pairs:
            if "=" not in item:
                sys.stderr.write("格式错误：%s（应为 灯号=#颜色）\n" % item)
                return 1
            key, hexv = item.split("=", 1)
            key = key.strip()
            idx = None
            if key.lstrip("-").isdigit():
                idx = int(key)
            else:
                rc = _key_label_to_rc(dev, key)
                if rc is not None:
                    idx = rcmap.get(rc)
            if idx is None:
                sys.stderr.write("无法解析灯号/键名：%s\n" % key)
                return 1
            colors[idx] = _parse_hsv_hex(hexv)
        ok, switched = dev.perkey_set(colors)
        print("逐键写入 %s/%s 颗%s"
              % (ok, len(colors), "；已自动切到自定义档" if switched else ""))
    finally:
        dev.close()
    return 0


def cmd_perkey_read(args):
    """回读全部键位轴灯的 HSV（非自定义档下大概率全 0）。"""
    dev = _open(args)
    try:
        if not dev.perkey_supported():
            sys.stderr.write("当前设备不支持逐键读取\n")
            return 1
        data = dev.perkey_read()
        rcmap = dev.matrix_rc_map()
        non_zero = [(i, v) for i, v in sorted(data.items())
                    if v[:3] != (0, 0, 0)]
        print("在自定义档: %s" % dev.matrix_in_custom())
        print("读到 %s 颗，其中非黑 %s 颗" % (len(data), len(non_zero)))
        for idx, (h, s, v, p) in non_zero[:40]:
            rc = rcmap.get(idx)
            print("  led %-4s rc=%-8s h=%-4s s=%-4s v=%-4s p=%s"
                  % (idx, rc, h, s, v, p))
    finally:
        dev.close()
    return 0


def _key_label_to_rc(dev, label):
    """按键名 -> ``(row, col)``。

    .. warning::
       本机（Matrix Faukwaa）**不要用键名上色**。真机逐颗打断法实测：

       * ``GET_RGB_MATRIX_ROW_INFO`` 返回的矩阵表里 **col 0 全是 0xFF
         （无灯）** —— 最左列的 Esc / Tab / Caps / Shift / Ctrl **不在表内**；
       * Esc、Tab 实测**不受 ``SET_MATRIX_LED`` 控制**（写了不生效）。

       因此键名无法可靠映射到灯号，请一律用**数字灯号**：
       ``mvl.py perkey-set 0=#0000ff``。
    """
    return None


def cmd_matrix_reset(args):
    """把矩阵灯效模式还原到固件默认档，并可选清空所有灯。

    **安全收尾命令** —— 逐键上色测试完务必跑一次。实机踩过的坑：
    键盘若一直停在 ``custom`` 档（45），会出现异常（包括 USB 无法枚举）。
    """
    dev = _open(args)
    try:
        if dev.lighting_backend != "amk":
            sys.stderr.write("仅适用于 AMK 通道（当前 %s）\n"
                             % dev.lighting_backend)
            return 1
        before = dev.matrix_mode()
        if args.clear:
            n = dev.clear_matrix_leds()
            print("已关闭 %d 颗灯（地址 0-92）" % n)
        ok = dev.restore_matrix_default()
        after = dev.matrix_mode()
        print("矩阵模式 之前 : %s" % (before,))
        print("矩阵模式 之后 : %s" % (after,))
        print("还原默认档    : %s" % ("成功" if ok else "失败"))
    finally:
        dev.close()
    return 0


def cmd_matrix_mode(args):
    """读取 / 切换矩阵灯效模式。"""
    dev = _open(args)
    try:
        if dev.lighting_backend != "amk":
            sys.stderr.write("仅适用于 AMK 通道（当前 %s）\n"
                             % dev.lighting_backend)
            return 1
        if args.set is not None:
            ok = dev.set_matrix_mode(args.set)
            print("切换模式 -> %s : %s" % (args.set, "成功" if ok else "失败"))
        m = dev.matrix_mode()
        print("矩阵灯效模式     : %s" % (m,))
        if m:
            print("当前是否自定义档 : %s"
                  % (m.get("current") == m.get("custom"),))
    finally:
        dev.close()
    return 0


def _device_key(dev):
    """当前设备的标识（写进方案里，标注是给哪把键盘存的）。

    注意 ``dev.info`` 是 ``DeviceInfo`` 对象（``__slots__``），不是 dict，
    所以这里既支持属性也支持 dict，避免换后端时炸掉。
    """
    if dev is None:
        return {}
    info = dev.info

    def _pick(name):
        if info is None:
            return None
        if isinstance(info, dict):
            return info.get(name)
        return getattr(info, name, None)

    key = {}
    for k in ("vendor_id", "product_id", "serial"):
        v = _pick(k)
        if v is not None:
            key[k] = v
    try:
        key["keyboard_uid"] = dev.describe().get("keyboard_uid")
    except Exception:
        pass
    key["backend"] = dev.lighting_backend
    key["name"] = _pick("product") or _pick("name")
    return key


def _snapshot(dev):
    """把当前一整套灯光拍成可序列化的字典（和 GUI 的 snapshot 对齐）。"""
    data = {}
    try:
        st = dev.read_lighting()
        data.update(effect=st.effect, speed=st.speed, hue=st.hue,
                    sat=st.sat, val=st.val)
    except Exception:
        pass
    strips = []
    try:
        for s in dev.strips():
            strips.append({"index": s.index, "mode": s.mode, "hue": s.hue,
                           "sat": s.sat, "val": s.val, "speed": s.speed})
    except Exception:
        pass
    if strips:
        data["strips"] = strips
    return data


def _apply_snapshot(dev, data):
    """把方案里的字段下发到设备。返回 (成功的部分, 失败的部分)。"""
    applied, failed = [], []
    try:
        changes = {k: data[k] for k in ("effect", "speed", "hue", "sat", "val")
                   if data.get(k) is not None}
        if changes:
            dev.set_all(**changes)
            applied.append("轴灯")
    except Exception as exc:
        failed.append("轴灯（%s）" % exc)

    try:
        strips = data.get("strips") or []
        if strips:
            live = {s.index: s for s in dev.strips()}
            done = 0
            for item in strips:
                s = live.get(item.get("index"))
                if s is None:
                    continue
                if item.get("mode") is not None:
                    dev.set_strip_mode(s, int(item["mode"]))
                h, sa, v = item.get("hue"), item.get("sat"), item.get("val")
                if h is not None and sa is not None and v is not None:
                    dev.set_strip_color(s, int(h), int(sa), int(v),
                                        on=int(v) > 0, force=True)
                if item.get("speed") is not None:
                    dev.set_strip_led_speed(s, int(item["speed"]))
                done += 1
            if done:
                applied.append("配件灯×%d" % done)
    except Exception as exc:
        failed.append("配件灯（%s）" % exc)
    return applied, failed


def cmd_profile_list(args):
    store = P.PresetStore(args.file)
    print("方案库：%s" % store.path)
    if store.error:
        print("  ! %s" % store.error)
    if not store.presets:
        print("  （还没有方案）")
        return 0
    print("  共 %d 条：\n" % len(store.presets))
    for i, p in enumerate(store.presets, 1):
        print("  %2d. %-20s %s" % (i, p.name, p.summary()))
    return 0


def cmd_profile_save(args):
    store = P.PresetStore(args.file)
    dev = _open(args)
    try:
        data = _snapshot(dev)
        ok, result, over = store.add(args.name, data, _device_key(dev),
                                     overwrite=args.overwrite,
                                     unique=not args.no_unique)
        if not ok:
            sys.stderr.write("保存失败：%s\n" % result)
            return 1
        print("已%s方案「%s」" % ("覆盖" if over else "保存", result))
        print("  方案库：%s" % store.path)
        return 0
    finally:
        dev.close()


def cmd_profile_apply(args):
    store = P.PresetStore(args.file)
    p = store.get(args.name)
    if p is None:
        sys.stderr.write("没有找到方案「%s」\n" % args.name)
        return 1
    dev = _open(args)
    try:
        applied, failed = _apply_snapshot(dev, p.data or {})
        if applied:
            print("已应用方案「%s」：%s" % (p.name, "、".join(applied)))
        if failed:
            sys.stderr.write("部分未生效：%s\n" % "；".join(failed))
            return 1
        return 0
    finally:
        dev.close()


def cmd_profile_delete(args):
    store = P.PresetStore(args.file)
    ok, err = store.remove(args.name)
    if not ok:
        sys.stderr.write("%s\n" % err)
        return 1
    print("已删除方案「%s」" % args.name)
    return 0


def cmd_profile_rename(args):
    store = P.PresetStore(args.file)
    ok, err, result = store.rename(args.name, args.new_name)
    if not ok:
        sys.stderr.write("%s\n" % err)
        return 1
    print("已把「%s」重命名为「%s」" % (args.name, result))
    return 0


def cmd_profile_duplicate(args):
    store = P.PresetStore(args.file)
    ok, result, _ = store.duplicate(args.name)
    if not ok:
        sys.stderr.write("%s\n" % result)
        return 1
    print("已复制为「%s」" % result)
    return 0


def cmd_profile_show(args):
    store = P.PresetStore(args.file)
    p = store.get(args.name)
    if p is None:
        sys.stderr.write("没有找到方案「%s」\n" % args.name)
        return 1
    print(json.dumps(p.as_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="vial-matrix-light 命令行")
    p.add_argument("--all", action="store_true", help="列出所有 raw HID 接口（不限定 Vial 固件）")
    p.add_argument("--index", type=int, default=0, help="选择第几个设备（默认 0）")
    p.add_argument("--device", type=int, default=None, help="同 --index，优先级更高")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("info").set_defaults(func=cmd_info)
    sub.add_parser("get").set_defaults(func=cmd_get)

    s = sub.add_parser("set")
    s.add_argument("--effect", type=int)
    s.add_argument("--val", type=int)
    s.add_argument("--speed", type=int)
    s.add_argument("--hue", type=int)
    s.add_argument("--sat", type=int)
    s.add_argument("--save", action="store_true")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("color")
    s.add_argument("--hex")
    s.add_argument("--hsv", help="形如 '213,255,255'")
    s.add_argument("--no-val", action="store_true", help="不改动亮度")
    s.set_defaults(func=cmd_color)

    s = sub.add_parser("preset")
    s.add_argument("name")
    s.set_defaults(func=cmd_preset)

    sub.add_parser("effects").set_defaults(func=cmd_effects)
    sub.add_parser("off").set_defaults(func=cmd_off)
    sub.add_parser("on").set_defaults(func=cmd_on)

    s = sub.add_parser("raw")
    s.add_argument("hex", help='例如 "08 80"')
    s.set_defaults(func=cmd_raw)

    s = sub.add_parser("def")
    s.add_argument("--save", help="导出到文件")
    s.set_defaults(func=cmd_def)

    s = sub.add_parser("write-test", help="写 value_id 再回读（自带还原）")
    s.add_argument("value_id")
    s.add_argument("values", nargs="*")
    s.add_argument("--restore", action="store_true", default=True)
    s.set_defaults(func=cmd_write_test)

    # --- 配件灯 ---------------------------------------------------------
    sub.add_parser("strips", help="列出配件灯条与灯效编号表"
                   ).set_defaults(func=cmd_strips)

    s = sub.add_parser("strips-read", help="逐颗读出配件灯状态，可导出备份")
    s.add_argument("--json", help="导出备份到该 JSON 文件")
    s.set_defaults(func=cmd_strips_read)

    s = sub.add_parser("strips-color", help="给灯条上色（本机需处于自定义档）")
    s.add_argument("--strip", "--index", dest="strip", type=int, default=None,
                   help="灯条编号（从 0 开始）；不填则全部")
    s.add_argument("--hsv", help="形如 '213,255,255'")
    s.add_argument("--hex", help="形如 '#00bfff'")
    s.add_argument("--off", action="store_true", help="顺便关闭该灯条")
    s.add_argument("--force", action="store_true",
                   help="先把灯条切到「自定义」档（0），否则固件会忽略自选颜色")
    s.add_argument("--save", action="store_true")
    s.set_defaults(func=cmd_strips_color)

    s = sub.add_parser("strips-mode", help="切换灯条灯效（0-9，共 10 档）")
    s.add_argument("strip", type=int, help="灯条编号（从 0 开始）")
    s.add_argument("mode", type=int, help="灯效编号，见 strips 输出")
    s.add_argument("--save", action="store_true")
    s.set_defaults(func=cmd_strips_mode)

    s = sub.add_parser("strips-off", help="逐颗关闭全部配件灯")
    s.set_defaults(func=cmd_strips_off)

    s = sub.add_parser("strips-bright", help="调配件灯亮度（逐灯写 V 分量，0-255）")
    s.add_argument("value", type=int, help="亮度 0-255")
    s.add_argument("--strip", "--index", dest="strip", type=int, default=None,
                   help="灯条编号（从 0 开始）；不填则全部")
    s.set_defaults(func=cmd_strips_bright)

    s = sub.add_parser("strips-speed", help="调配件灯逐灯速度（0-15，仅自定义档）")
    s.add_argument("value", type=int, help="速度 0-%d" % effects.STRIP_LED_SPEED_MAX)
    s.add_argument("--strip", "--index", dest="strip", type=int, default=None,
                   help="灯条编号（从 0 开始）；不填则全部")
    s.set_defaults(func=cmd_strips_speed)

    s = sub.add_parser("strips-on", help="逐颗点亮全部配件灯")
    s.set_defaults(func=cmd_strips_on)

    s = sub.add_parser("axis-diag",
                       help="诊断轴灯通道 + 打印 LED 地址地图（不改灯）")
    s.set_defaults(func=cmd_axis_diag)

    # --- 逐键轴灯上色（需切到矩阵自定义档） ------------------------------
    s = sub.add_parser("perkey-fill",
                       help="把所有键位轴灯刷成同一颜色（逐键写）")
    s.add_argument("--hex", required=True, help="颜色，如 '#ff0000'")
    s.add_argument("--bright", type=int, help="覆盖亮度 0-255")
    s.set_defaults(func=cmd_perkey_fill)

    s = sub.add_parser("perkey-set",
                       help="按**数字灯号**逐颗上色，如 0=#0000ff 1=#00ff00"
                            "（键名不可靠，见 axis-diag）")
    s.add_argument("pairs", nargs="+", metavar="KEY=HEX")
    s.set_defaults(func=cmd_perkey_set)

    s = sub.add_parser("perkey-read", help="回读全部键位轴灯的 HSV")
    s.set_defaults(func=cmd_perkey_read)

    s = sub.add_parser("matrix-reset",
                       help="还原矩阵模式到默认档（逐键测试后的安全收尾）")
    s.add_argument("--clear", action="store_true", help="顺便关闭所有灯")
    s.set_defaults(func=cmd_matrix_reset)

    s = sub.add_parser("matrix-mode", help="读取/切换矩阵灯效模式")
    s.add_argument("--set", type=int, help="要切换到的模式编号")
    s.set_defaults(func=cmd_matrix_mode)

    # 兼容旧名（早期是"补灯"，现在是诊断）
    s = sub.add_parser("axis-fix", help="[旧名] 等同 axis-diag")
    s.set_defaults(func=cmd_axis_diag)

    s = sub.add_parser("strips-restore", help="从 strips-read --json 的备份恢复")
    s.add_argument("file")
    s.set_defaults(func=cmd_strips_restore)

    # --- 预设方案（一整套灯光的命名存档） -------------------------------
    s = sub.add_parser("profile-list", help="列出已保存的灯光方案")
    s.add_argument("--file", help="方案库路径（默认 %s）" % P.config_path())
    s.set_defaults(func=cmd_profile_list)

    s = sub.add_parser("profile-save", help="把当前整套灯光存成命名方案")
    s.add_argument("name")
    s.add_argument("--overwrite", action="store_true", help="同名则覆盖")
    s.add_argument("--no-unique", action="store_true",
                   help="同名时不自动改名，直接失败")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_save)

    s = sub.add_parser("profile-apply", help="应用已保存的方案")
    s.add_argument("name")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_apply)

    s = sub.add_parser("profile-delete", help="删除方案")
    s.add_argument("name")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_delete)

    s = sub.add_parser("profile-rename", help="重命名方案")
    s.add_argument("name")
    s.add_argument("new_name")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_rename)

    s = sub.add_parser("profile-duplicate", help="复制方案")
    s.add_argument("name")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_duplicate)

    s = sub.add_parser("profile-show", help="以 JSON 打印方案内容")
    s.add_argument("name")
    s.add_argument("--file", help="方案库路径")
    s.set_defaults(func=cmd_profile_show)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        try:
            dev = open_first_vial()
            dev.close()
        except Exception:
            pass
        return 0
    try:
        return args.func(args) or 0
    except VialError as exc:
        sys.stderr.write("错误: %s\n" % exc)
        return 1
    except BlockingIOError as exc:
        sys.stderr.write("设备忙（Vial/VIA 网页版可能正在占用）: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
