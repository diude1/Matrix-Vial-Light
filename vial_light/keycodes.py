# -*- coding: utf-8 -*-
"""QMK / Vial 基础键位码→ 键名 与 显示缩写。

数据来源
--------
`reference/matrix-vialweb/keycodes/keycodes_v6.py`（从官网
`config.matrix-lab.com` 逆向出的官方键位码表）中落在 `0x00-0xFF`
的全部**基础键**，由脚本**程序化提取**生成 —— 不要手抄。

.. warning::
   **必须排除 `QK_MOD_*` 条目。** 它们是「修饰键位域」
   （`0x01`=Mod_LCtrl、`0x11`=Mod_RCtl…），与基础键是**不同的语义层**，
   只是恰好落在同一字节区间。第一版没排除，导致 41 处错标：
   `0xE0` 被标成 `Hang`（实为 `KC_LCTRL`）、`0x05` 标成 `LMShift`
   （实为 `KC_B`）—— 用户看到「LShift 键显示成 Hanja、左Ctrl 显示成 Hang」，
   根因就在这里。

为什么需要显示缩写
------------------
矩阵网格每格只有约 34px 宽，塞不下 `KC_LEFT_BRACKET` 这种全名。
额外提供**显示缩写表**（`KC_LSFT` → `LShift`），保证一格一词、
扫一眼就认出物理键。

和官网的关系
------------
官网「键位」页做的正是这件事：`editor/keymap_editor.py` 的
`refresh_layer_display()` 逐个按键调 `Key.setText()`，数据来自
`DYNAMIC_KEYMAP_GET_KEYCODE (0x04)` —— 本项目的
`VialDevice.get_keycode()` 用的是**同一条命令**，行为与官网对齐。

⚠️ **注意语义**：这里显示的是**固件当前层实际绑定的键位码**，
不是物理键帽丝印。两者不一定一致 —— 比如固件把 LShift 位置改成
`KC_LANG2`，那读出来就是 Hanja。这是固件状态，不是本表的问题。
"""

# 键位码 → 官方键名（0x00-0xFF 基础键，187 条，由 keycodes_v6.py 提取）
KC_NAME = {
    0x00: 'KC_NO',
    0x01: 'KC_TRNS',
    0x02: 'MOD_LSFT',
    0x04: 'KC_A',
    0x05: 'KC_B',
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
    0x2F: 'KC_LBRACKET',
    0x30: 'KC_RBRACKET',
    0x31: 'KC_BSLASH',
    0x32: 'KC_NONUS_HASH',
    0x33: 'KC_SCOLON',
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
    0x46: 'KC_PSCREEN',
    0x47: 'KC_SCROLLLOCK',
    0x48: 'KC_PAUSE',
    0x49: 'KC_INSERT',
    0x4A: 'KC_HOME',
    0x4B: 'KC_PGUP',
    0x4C: 'KC_DELETE',
    0x4D: 'KC_END',
    0x4E: 'KC_PGDOWN',
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
    0x63: 'KC_KP_DOT',
    0x64: 'KC_NONUS_BSLASH',
    0x65: 'KC_APPLICATION',
    0x67: 'KC_KP_EQUAL',
    0x68: 'KC_F13',
    0x69: 'KC_F14',
    0x6A: 'KC_F15',
    0x6B: 'KC_F16',
    0x6C: 'KC_F17',
    0x6D: 'KC_F18',
    0x6E: 'KC_F19',
    0x6F: 'KC_F20',
    0x70: 'KC_F21',
    0x71: 'KC_F22',
    0x72: 'KC_F23',
    0x73: 'KC_F24',
    0x74: 'KC_EXEC',
    0x75: 'KC_HELP',
    0x77: 'KC_SLCT',
    0x78: 'KC_STOP',
    0x79: 'KC_AGIN',
    0x7A: 'KC_UNDO',
    0x7B: 'KC_CUT',
    0x7C: 'KC_COPY',
    0x7D: 'KC_PSTE',
    0x7E: 'KC_FIND',
    0x80: 'KC__VOLUP',
    0x81: 'KC__VOLDOWN',
    0x82: 'KC_LCAP',
    0x83: 'KC_LNUM',
    0x84: 'KC_LSCR',
    0x85: 'KC_KP_COMMA',
    0x87: 'KC_RO',
    0x88: 'KC_KANA',
    0x89: 'KC_JYEN',
    0x8A: 'KC_HENK',
    0x8B: 'KC_MHEN',
    0x90: 'KC_LANG1',
    0x91: 'KC_LANG2',
    0xA5: 'KC_PWR',
    0xA6: 'KC_SLEP',
    0xA7: 'KC_WAKE',
    0xA8: 'KC_MUTE',
    0xA9: 'KC_VOLU',
    0xAA: 'KC_VOLD',
    0xAB: 'KC_MNXT',
    0xAC: 'KC_MPRV',
    0xAD: 'KC_MSTP',
    0xAE: 'KC_MPLY',
    0xAF: 'KC_MSEL',
    0xB0: 'KC_EJCT',
    0xB1: 'KC_MAIL',
    0xB2: 'KC_CALC',
    0xB3: 'KC_MYCM',
    0xB4: 'KC_WSCH',
    0xB5: 'KC_WHOM',
    0xB6: 'KC_WBAK',
    0xB7: 'KC_WFWD',
    0xB8: 'KC_WSTP',
    0xB9: 'KC_WREF',
    0xBA: 'KC_WFAV',
    0xBB: 'KC_MFFD',
    0xBC: 'KC_MRWD',
    0xBD: 'KC_BRIU',
    0xBE: 'KC_BRID',
    0xCD: 'KC_MS_U',
    0xCE: 'KC_MS_D',
    0xCF: 'KC_MS_L',
    0xD0: 'KC_MS_R',
    0xD1: 'KC_BTN1',
    0xD2: 'KC_BTN2',
    0xD3: 'KC_BTN3',
    0xD4: 'KC_BTN4',
    0xD5: 'KC_BTN5',
    0xD9: 'KC_WH_U',
    0xDA: 'KC_WH_D',
    0xDB: 'KC_WH_L',
    0xDC: 'KC_WH_R',
    0xDD: 'KC_ACL0',
    0xDE: 'KC_ACL1',
    0xDF: 'KC_ACL2',
    0xE0: 'KC_LCTRL',
    0xE1: 'KC_LSHIFT',
    0xE2: 'KC_LALT',
    0xE3: 'KC_LGUI',
    0xE4: 'KC_RCTRL',
    0xE5: 'KC_RSHIFT',
    0xE6: 'KC_RALT',
    0xE7: 'KC_RGUI',}


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
    "KC_PAGEUP": "PgUp", "KC_PGUP": "PgUp", "KC_PGUP_BRK": "PgUp",
    "KC_PAGEDOWN": "PgDn", "KC_PGDN": "PgDn", "KC_PGDOWN": "PgDn",
    "KC_UP": "\u2191", "KC_DOWN": "\u2193",
    "KC_LEFT": "\u2190", "KC_RIGHT": "\u2192", "KC_RGHT": "\u2192",
    "KC_PRTSC": "PrtSc", "KC_PSCREEN": "PrtSc", "KC_PSCR": "PrtSc",
    "KC_SCROLLLOCK": "ScrLk", "KC_SLCK": "ScrLk",
    "KC_PAUSE": "Pause", "KC_PAUS": "Pause", "KC_BRK": "Pause",
    "KC_MENU": "Menu", "KC_APPLICATION": "Menu", "KC_COMPOSE": "Compose",
    # 符号
    "KC_MINUS": "-", "KC_MINS": "-", "KC_EQUAL": "=", "KC_EQL": "=",
    "KC_LEFT_BRACKET": "[", "KC_LBRC": "[",
    "KC_RIGHT_BRACKET": "]", "KC_RBRC": "]",
    "KC_BACKSLASH": "\\", "KC_BSLS": "\\",
    "KC_NONUS_HASH": "#", "KC_NUHS": "#",
    "KC_SEMICOLON": ";", "KC_SCOLON": ";", "KC_SCLN": ";",
    "KC_QUOTE": "'", "KC_APOSTROPHE": "'",
    "KC_GRAVE": "`", "KC_NONUS_GRAVE": "~",
    "KC_COMMA": ",", "KC_COMM": ",",
    "KC_DOT": ".", "KC_KP_DOT": ".",
    "KC_SLASH": "/", "KC_SLSH": "/",
    # 修饰键
    "KC_LCTRL": "LCtrl", "KC_LCTL": "LCtrl",
    "KC_RCTRL": "RCtrl", "KC_RCTL": "RCtrl",
    "KC_LSHIFT": "LShift", "KC_LSFT": "LShift",
    "KC_RSHIFT": "RShift", "KC_RSFT": "RShift",
    "KC_LALT": "LAlt", "KC_LOPT": "LAlt",
    "KC_RALT": "RAlt", "KC_ROPT": "RAlt",
    "KC_LGUI": "LWin", "KC_LWIN": "LWin", "KC_LCMD": "LWin",
    "KC_RGUI": "RWin", "KC_RWIN": "RWin", "KC_RCMD": "RWin",
    "KC_LCAP": "LCaps", "KC_RCAP": "RCaps",
    # 小键盘
    "KC_KP_0": "0", "KC_KP_1": "1", "KC_KP_2": "2", "KC_KP_3": "3",
    "KC_KP_4": "4", "KC_KP_5": "5", "KC_KP_6": "6", "KC_KP_7": "7",
    "KC_KP_8": "8", "KC_KP_9": "9",
    "KC_KP_PLUS": "+", "KC_PPLS": "+",
    "KC_KP_MINUS": "-", "KC_PMNS": "-",
    "KC_KP_ASTERISK": "*", "KC_PAST": "*",
    "KC_KP_SLASH": "/", "KC_PSLS": "/",
    "KC_KP_ENTER": "Ent", "KC_PENT": "Ent",
    "KC_KP_EQUAL": "=", "KC_PEQL": "=",
    "KC_NUMLOCK": "Num", "KC_NLCK": "Num",
    # F 键
    "KC_F1": "F1", "KC_F2": "F2", "KC_F3": "F3", "KC_F4": "F4",
    "KC_F5": "F5", "KC_F6": "F6", "KC_F7": "F7", "KC_F8": "F8",
    "KC_F9": "F9", "KC_F10": "F10", "KC_F11": "F11", "KC_F12": "F12",
    "KC_F13": "F13", "KC_F14": "F14", "KC_F15": "F15", "KC_F16": "F16",
    "KC_F17": "F17", "KC_F18": "F18", "KC_F19": "F19", "KC_F20": "F20",
    "KC_F21": "F21", "KC_F22": "F22", "KC_F23": "F23", "KC_F24": "F24",
    # 鼠标 / 滚轮
    "KC_MS_L": "LClick", "KC_MS_R": "RClick",
    "KC_BTN2": "M2", "KC_BTN3": "M3", "KC_BTN4": "M4", "KC_BTN5": "M5",
    "KC_BTN6": "M6", "KC_BTN7": "M7", "KC_BTN8": "M8", "KC_BTN9": "M9",
    "KC_WH_L": "WhL", "KC_WH_R": "WhR", "KC_WH_U": "WhU", "KC_WH_D": "WhD",
    # 系统 / 编辑（0x74-0x7F 段）
    "KC_STOP": "Stop", "KC_AGIN": "Again", "KC_UNDO": "Undo",
    "KC_CUT": "Cut", "KC_COPY": "Copy", "KC_PSTE": "Paste",
    "KC_FIND": "Find", "KC_SLCT": "SelAll", "KC_EXEC": "Exec",
    "KC_HELP": "Help", "KC_CAPS": "Caps", "KC_CAPSLOCK": "Caps",
    # 语言 / 其它
    "KC_LANG1": "Hang", "KC_LANG2": "Hanja", "KC_LANG3": "Kana",
    "KC_LANG4": "Lang4", "KC_LANG5": "Lang5",
    "KC_LANG6": "Lang6", "KC_LANG7": "Lang7", "KC_LANG8": "Lang8",
    "KC_LANG9": "RAlt", "KC_LANG10": "RWin",
    "KC_NO": "\u2014", "KC_TRNS": "\u25bd",
}


def keycode_label(code):
    """把固件键位码翻成一格放得下的短标签。

    返回 ``(短标签, 完整键名)``。认不出来时短标签退化成 ``#NN``，
    至少还能看出「这里有个键、码值是多少」—— **绝不瞎猜成字母**。
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ("", "")
    if code <= 0:
        return ("\u2014", "KC_NO" if code == 0 else "")
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
