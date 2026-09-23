# -*- coding: utf-8 -*-
"""安全恢复：等键盘枚举出现后，立刻把矩阵模式切回默认档。

用途：键盘被越界写写死后，拔插只有很短的可操作窗口。
      本脚本会**持续轮询**，一发现设备就马上还原，避免错过窗口。

**本脚本只发一条命令**（SET_RGB_MATRIX_MODE -> 默认档），
不写任何 LED，不会再次触发问题。

用法：
    python restore_safe.py            # 最多等 180 秒
    python restore_safe.py --wait 60  # 最多等 60 秒
"""
from __future__ import print_function

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vial_light.device import VialDevice          # noqa: E402


def restore_once():
    """发现设备就还原；返回 True 表示成功。"""
    try:
        devs = VialDevice.discover()
    except Exception as exc:
        print("  枚举异常: %s" % exc)
        return False
    if not devs:
        return False

    print("  发现设备: %s" % (devs[0],))
    dev = None
    try:
        dev = VialDevice(devs[0]).open()
        dev.read_versions()
        dev.detect_lighting()
        if dev.lighting_backend != "amk":
            print("  非 AMK 通道（%s），无需处理" % dev.lighting_backend)
            return True

        before = dev.matrix_mode()
        print("  当前矩阵模式: %s" % (before,))
        if before and before.get("current") == before.get("default"):
            print("  已经在默认档，无需改动")
            return True

        ok = dev.restore_matrix_default()
        time.sleep(0.3)
        after = dev.matrix_mode()
        print("  还原默认档  : %s" % ("成功" if ok else "失败"))
        print("  还原后模式  : %s" % (after,))
        return bool(ok)
    except Exception as exc:
        print("  操作失败: %s" % exc)
        return False
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass


def main():
    wait = 180
    if "--wait" in sys.argv:
        try:
            wait = int(sys.argv[sys.argv.index("--wait") + 1])
        except (IndexError, ValueError):
            pass

    print("等待键盘出现（最多 %d 秒）…… 请现在拔插键盘" % wait)
    print("提示：插上后**不要按任何键**，脚本一发现设备就会立刻还原。")
    print("")

    deadline = time.time() + wait
    tick = 0
    while time.time() < deadline:
        tick += 1
        if restore_once():
            print("")
            print(">>> 完成。请确认键盘是否恢复正常。")
            return 0
        # 每 5 秒给一次心跳，避免看起来像卡住
        if tick % 25 == 0:
            print("  ...仍在等待（已 %.0f 秒）" % (time.time() - (deadline - wait)))
        time.sleep(0.2)

    print("")
    print("超时：%d 秒内没等到设备。" % wait)
    print("请再试一次拔插，或直接跑：python restore_safe.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
