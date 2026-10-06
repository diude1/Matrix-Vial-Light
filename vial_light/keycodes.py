# -*- coding: utf-8 -*-
"""QMK / Vial 基础键位码 → 键名 与 显示缩写。

数据来源
--------
``reference/matrix-vialweb/keycodes/keycodes_v6.py``（从官网
``config.matrix-lab.com`` 逆向出的官方键位码表）里落在 ``0x00-0xFF``
的全部条目，由脚本**程序化提取**生成 —— 不要手抄，手抄必错
（``KC_ENTER`` / ``KC_ESCAPE`` / ``KC_BSPACE`` 这几个名字和直觉写法
就不一样，方向键也是 ``0x4F=右 0x50=左 0x51=下 0x52=上``）。

为什么需要它
------------
矩阵网格每格只有约 34px 宽，塞不下 ``KC_LEFT_BRACKET`` 这种全名。
所以额外提供一份**显示缩写表**（``KC_LSFT`` → ``LShift``），
保证一格一词、扫一眼就认出物理键。

和官网的关系
------------
官网「键位」页做的正是这件事：``editor/keymap_editor.py`` 的
``refresh_layer_display()`` 逐个按键调 ``Key.setText()``，数据来自
``DYNAMIC_KEYMAP_GET_KEYCODE (0x04)`` —— 本项目的
``VialDevice.get_keycode()`` 用的是**同一条命令**，所以行为与官网对齐。

v5 / v6 差异
------------
官方 v5 与 v6 表在 ``0x00-0xFF`` 区间只有 18 处不同，且全部集中在
鼠标键（``KC_BTN2`` / ``KC_MS_L`` / ``KC_WH_*`` / ``KC_ACL*``）
与 ``QK_MOD_*`` 修饰键上，字母数字区完全一致。
本表按 v6 建；v5 设备若读到那几个码位，显示为通用名，不会认错成字母。
"""

# 键位码 → 官方键名（0x00-0xFF，187 条，由 keycodes_v6.py 提取）
KC_NAME = {
    0x00: 'KC_NO',
    0x01: 'KC_TRNS',
    0x02: 'MOD_LSFT',
    0x04: 'KC_A',
    0x05: 'QMK_LM_SHIFT',
    0x06: 'KC_C',
    0x07: 'KC_D',
    0x08: 'KC_E',
    0x09: 'KC_F',
    0x0A: 'KC_G',
    0x0B: 'KC_H',
    0x0C: 'KC_I',
    0x0D: 'KC_J',
    0x0E: 'KC_K',
    0x0F: 'KC_L',
    0x10: 'KC_M',
    0x11: 'KC_N',
    0x12: 'KC_O',
    0x13: 'KC_P',
    0x14: 'KC_Q',
    0x15: 'KC_R',
    0x16: 'KC_S',
    0x17: 'KC_T',
    0x18: 'KC_U',
    0x19: 'KC_V',
    0x1A: 'KC_W',
    0x1B: 'KC_X',
    0x1C: 'KC_Y',
    0x1D: 'KC_Z',
    0x1E: 'KC_1',
    0x1F: 'KC_2',
    0x20: 'KC_3',
    0x21: 'KC_4',
    0x22: 'KC_5',
    0x23: 'KC_6',
    0x24: 'KC_7',
    0x25: 'KC_8',
    0x26: 'KC_9',
    0x27: 'KC_0',
    0x28: 'KC_ENTER',
    0x29: 'KC_ESCAPE',
    0x2A: 'KC_BSPACE',
    0x2B: 'KC_TAB',
    0x2C: 'KC_SPACE',
    0x2D: 'KC_MINUS',
    0x2E: 'KC_EQUAL',
    0x2F: 'KC_LEFT_BRACKET',
    0x30: 'KC_RIGHT_BRACKET',
    0x31: 'KC_BACKSLASH',
    0x32: 'KC_NONUS_HASH',
    0x33: 'KC_SEMICOLON',
    0x34: 'KC_QUOTE',
    0x35: 'KC_GRAVE',
    0x36: 'KC_COMMA',
    0x37: 'KC_DOT',
    0x38: 'KC_SLASH',
    0x39: 'KC_CAPSLOCK',
    0x3A: 'KC_F1',
    0x3B: 'KC_F2',
    0x3C: 'KC_F3',
    0x3D: 'KC_F4',
    0x3E: 'KC_F5',
    0x3F: 'KC_F6',
    0x40: 'KC_F7',
    0x41: 'KC_F8',
    0x42: 'KC_F9',
    0x43: 'KC_F10',
    0x44: 'KC_F11',
    0x45: 'KC_F12',
    0x46: 'KC_PRTSC',
    0x47: 'KC_SCROLLLOCK',
    0x48: 'KC_PAUSE',
    0x49: 'KC_INSERT',
    0x4A: 'KC_HOME',
    0x4B: 'KC_PAGEUP',
    0x4C: 'KC_DELETE',
    0x4D: 'KC_END',
    0x4E: 'KC_PAGEDOWN',
    0x4F: 'KC_RIGHT',
    0x50: 'KC_LEFT',
    0x51: 'KC_DOWN',
    0x52: 'KC_UP',
    0x53: 'KC_NUMLOCK',
    0x54: 'KC_KP_SLASH',
    0x55: 'KC_KP_ASTERISK',
    0x56: 'KC_KP_MINUS',
    0x57: 'KC_KP_PLUS',
    0x58: 'KC_KP_ENTER',
    0x59: 'KC_KP_1',
    0x5A: 'KC_KP_2',
    0x5B: 'KC_KP_3',
    0x5C: 'KC_KP_4',
    0x5D: 'KC_KP_5',
    0x5E: 'KC_KP_6',
    0x5F: 'KC_KP_7',
    0x60: 'KC_KP_8',
    0x61: 'KC_KP_9',
    0x62: 'KC_KP_0',
    0x63: 'KC_DOT',
    0x64: 'KC_F13',
    0x65: 'KC_F14',
    0x66: 'KC_F15',
    0x67: 'KC_F16',
    0x68: 'KC_F17',
    0x69: 'KC_F18',
    0x6A: 'KC_F19',
    0x6B: 'KC_F20',
    0x6C: 'KC_F21',
    0x6D: 'KC_F22',
    0x6E: 'KC_F23',
    0x6F: 'KC_F24',
    0x77: 'MOD_LCTL',
    0x78: 'MOD_LSFT',
    0x79: 'MOD_LALT',
    0x7A: 'MOD_LGUI',
    0x7B: 'MOD_HYPR',
    0x7C: 'MOD_MEH',
    0x7D: 'KC_LEFT_CTRL',
    0x7E: 'KC_LEFT_SHIFT',
    0x7F: 'KC_LEFT_ALT',
    0x80: 'KC_LEFT_GUI',
    0x81: 'KC_LEFT_WIN',
    0x82: 'KC_RIGHT_WIN',
    0xE0: 'KC_LANG1',
    0xE1: 'KC_LANG2',
    0xE4: 'KC_LANG3',
    0xF2: 'KC_BTN2',
    0xF3: 'KC_ACL0',
    0xF4: 'KC_BTN3',
    0xF5: 'KC_BTN4',
    0xF6: 'KC_BTN5',
    0xF7: 'KC_BTN6',
    0xF8: 'KC_BTN7',
    0xF9: 'KC_BTN8',
    0xFA: 'KC_BTN9',
    0xFB: 'KC_WH_L',
    0xFC: 'KC_WH_R',
    0xFD: 'KC_MS_L',
    0xFE: 'KC_MS_R',
}


# 键名 → 显示缩写（矩阵网格一格只放得下一个词）
KC_SHORT = {
    # 字母
    "KC_A": "A", "KC_B": "B", "KC_C": "C", "KC_D": "D", "KC_E": "E",
    "KC_F": "F", "KC_G": "G", "KC_H": "H", "KC_I": "I", "KC_J": "J",
    "KC_K": "K", "KC_L": "L", "KC_M": "M", "KC_N": "N", "KC_O": "O",
    "KC_P": "P", "KC_Q": "Q", "KC_R": "R", "KC_S": "S", "KC_T": "T",
    "KC_U": "U", "KC_V": "V", "KC_W": "W", "KC_X": "X", "KC_Y": "Y",
    "KC_Z": "Z",
    # 数字
    "KC_1": "1", "KC_2": "2", "KC_3": "3", "KC_4": "4", "KC_5": "5",
    "KC_6": "6", "KC_7": "7", "KC_8": "8", "KC_9": "9", "KC_0": "0",
    # 编辑 / 导航
    "KC_ESCAPE": "Esc", "KC_ESC": "Esc", "KC_BSPACE": "Bksp",
    "KC_BSPC": "Bksp", "KC_TAB": "Tab", "KC_ENTER": "Enter",
    "KC_ENT": "Enter", "KC_SPACE": "Space", "KC_SPCE": "Space",
    "KC_DELETE": "Del", "KC_DEL": "Del", "KC_INSERT": "Ins",
    "KC_INS": "Ins", "KC_HOME": "Home", "KC_END": "End",
    "KC_PAGEUP": "PgUp", "KC_PGUP": "PgUp",
    "KC_PAGEDOWN": "PgDn", "KC_PGDN": "PgDn", "KC_PGDOWN": "PgDn",
    "KC_UP": "↑", "KC_DOWN": "↓",
    "KC_LEFT": "←", "KC_RIGHT": "→", "KC_RGHT": "→",
    "KC_PRTSC": "PrtSc", "KC_PSCR": "PrtSc",
    "KC_SCROLLLOCK": "ScrLk", "KC_SLCK": "ScrLk",
    "KC_PAUSE": "Pause", "KC_PAUS": "Pause",
    # 符号
    "KC_MINUS": "-", "KC_MINS": "-", "KC_EQUAL": "=", "KC_EQL": "=",
    "KC_LEFT_BRACKET": "[", "KC_LBRC": "[",
    "KC_RIGHT_BRACKET": "]", "KC_RBRC": "]",
    "KC_BACKSLASH": "\\", "KC_BSLS": "\\",
    "KC_NONUS_HASH": "#", "KC_NUHS": "#",
    "KC_SEMICOLON": ";", "KC_SCLN": ";",
    "KC_QUOTE": "'", "KC_APOSTROPHE": "'",
    "KC_GRAVE": "`", "KC_NONUS_GRAVE": "~",
    "KC_COMMA": ",", "KC_COMM": ",",
    "KC_DOT": ".", "KC_SLASH": "/", "KC_SLSH": "/",
    # 修饰键
    "KC_LEFT_CTRL": "LCtrl", "KC_LCTRL": "LCtrl", "KC_LCTL": "LCtrl",
    "KC_RIGHT_CTRL": "RCtrl", "KC_RCTRL": "RCtrl", "KC_RCTL": "RCtrl",
    "KC_LEFT_SHIFT": "LShift", "KC_LSHIFT": "LShift", "KC_LSFT": "LShift",
    "KC_RIGHT_SHIFT": "RShift", "KC_RSHIFT": "RShift", "KC_RSFT": "RShift",
    "KC_LEFT_ALT": "LAlt", "KC_LALT": "LAlt",
    "KC_RIGHT_ALT": "RAlt", "KC_RALT": "RAlt",
    "KC_LEFT_GUI": "LWin", "KC_LGUI": "LWin", "KC_LEFT_WIN": "LWin",
    "KC_RIGHT_GUI": "RWin", "KC_RGUI": "RWin", "KC_RIGHT_WIN": "RWin",
    "MOD_LCTL": "LCtl", "MOD_LSFT": "LSft", "MOD_LALT": "LAlt",
    "MOD_RALT": "RAlt", "MOD_LGUI": "LWin", "MOD_RGUI": "RWin",
    "MOD_HYPR": "Hypr", "MOD_MEH": "Meh", "QMK_LM_SHIFT": "LMShift",
    # 小键盘
    "KC_KP_0": "0", "KC_KP_1": "1", "KC_KP_2": "2", "KC_KP_3": "3",
    "KC_KP_4": "4", "KC_KP_5": "5", "KC_KP_6": "6", "KC_KP_7": "7",
    "KC_KP_8": "8", "KC_KP_9": "9",
    "KC_KP_PLUS": "+", "KC_PPLS": "+",
    "KC_KP_MINUS": "-", "KC_PMNS": "-",
    "KC_KP_ASTERISK": "*", "KC_PAST": "*",
    "KC_KP_SLASH": "/", "KC_PSLS": "/",
    "KC_KP_ENTER": "Ent", "KC_PENT": "Ent",
    "KC_NUMLOCK": "Num", "KC_NLCK": "Num",
    # F 键
    "KC_F1": "F1", "KC_F2": "F2", "KC_F3": "F3", "KC_F4": "F4",
    "KC_F5": "F5", "KC_F6": "F6", "KC_F7": "F7", "KC_F8": "F8",
    "KC_F9": "F9", "KC_F10": "F10", "KC_F11": "F11", "KC_F12": "F12",
    "KC_F13": "F13", "KC_F14": "F14", "KC_F15": "F15", "KC_F16": "F16",
    "KC_F17": "F17", "KC_F18": "F18", "KC_F19": "F19", "KC_F20": "F20",
    "KC_F21": "F21", "KC_F22": "F22", "KC_F23": "F23", "KC_F24": "F24",
    # 鼠标
    "KC_MS_L": "LClick", "KC_MS_R": "RClick",
    "KC_BTN2": "M2", "KC_BTN3": "M3", "KC_BTN4": "M4", "KC_BTN5": "M5",
    "KC_BTN6": "M6", "KC_BTN7": "M7", "KC_BTN8": "M8", "KC_BTN9": "M9",
    "KC_WH_L": "WhL", "KC_WH_R": "WhR",
    # 其它
    "KC_NO": "—", "KC_TRNS": "▽",
    "KC_LANG1": "Hang", "KC_LANG2": "Hanja", "KC_LANG3": "Kana",
    "KC_CAPSLOCK": "Caps", "KC_CAPS": "Caps",
}


def keycode_label(code):
    """把固件键位码翻成一格放得下的短标签。

    返回 ``(短标签, 完整键名)``。认不出来时短标签退化成 ``#NN``，
    至少还能看出"这里有个键、码值是多少"—— **绝不瞎猜成字母**。
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("", "")
    if code <= 0:
        return ("—", "KC_NO" if code == 0 else "")
    name = KC_NAME.get(code)
    if not name:
        return ("#%02X" % code, "")
    return (KC_SHORT.get(name, name.replace("KC_", "")), name)


def keycode_name(code):
    """只取完整键名（tooltip / 调试用）。"""
    try:
        return KC_NAME.get(int(code), "")
    except (TypeError, ValueError):
        return ""
