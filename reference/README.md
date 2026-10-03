# reference/ — 权威参考资料

本目录存放**逆向出来的权威依据**，不是运行时代码，不参与打包。

## matrix-vialweb/

矩阵官方配置网页 `https://config.matrix-lab.com/`（Vial Web）的 **118 个 Python 源文件**。

**提取方法**：该站是 Pyodide/Emscripten 打包的单页应用。

1. 取首页 HTML，里面引用的 `main-<hash>.js` 就是主 JS；
2. 主 JS 里有一份 `{"filename": ..., "start": N, "end": M}` 的**文件清单**（122 条）；
3. 同名的 `main-<hash>.data` 包里就是源文件明文，**按偏移量直接切**即可。

> 清单里的 `/usr/local/lib/python3.11/` 前缀剥掉就是相对路径。
> 字体（`wqy-microhei.ttc` 5MB）、图片、`python311.zip` 不必切。
> 全部条目都在 `reference/matrix-vialweb/`，路径结构与官网一致。

**为什么重要**：这份源码是本项目协议层的**唯一权威依据**，也是配列渲染的权威依据。

| 文件 | 用途 |
|---|---|
| `amk/protocol.py` | **命令表 + 全部帧格式**（首选查阅对象） |
| `amk/rgb.py` | 配件灯灯效表 `RL_EFFECT_*` |
| `amk/rgb_strip.py` | 配件灯 UI 逻辑 |
| `amk/rgb_matrix.py` | 轴灯 |
| `amk/rgb_grid.py` | 点阵屏 |
| `amk/rgb_indicator.py` | 指示灯 |
| `amk/animation.py` | `.anm`/`.sml`/`.bkg`/`.crs`/`.sts` 定制动画格式 |
| **`kle_serial.py`** | **配列几何解析（`Serial.deserialize`）—— 与上游 `ijprest/kle-serial` 不同，见下** |
| `widgets/keyboard_widget.py` | 配列绘制（直接用 `desc.x/y/rotation_*`，无后处理） |
| `widgets/key_widget.py` | 单个按键的绘制 |
| `editor/keymap_editor.py` / `editor/layout_editor.py` | 键位图 / 配列编辑器 |
| `editor/rgb_configurator.py` | `QMK_RGBLIGHT_EFFECTS` / `VIALRGB_EFFECTS` 表 |
| `util.py` | `hid_send()` —— 证明扩展协议与标准 Vial 共用同一条 32 字节通道 |

### `kle_serial.py` 与上游 kle-serial 的三处差异（重要）

官方这份**不是**上游原版，它引入了「旋转簇」语义，这正是 Alice 式配列能否
画对的关键（`vial_light/kbdef.py` 的 `parse_layout` 按它对齐）：

1. 行尾 `x = rotation_x` —— 也就是**行首 x 回到当前旋转中心，不是 0**；
2. `rx` / `ry` 会把 **x 和 y 一起**拽回簇中心（`rx` 定簇心 x、`ry` 定簇心 y，
   只声明其中一个时另一维沿用上次）；
3. `rx` / `ry` 在 `x` / `y` **之前**处理（所以 `{"ry":3.5,"y":1}` → `y=4.5`）。

拿 Faukwaa 实测：按官方语义解析是 **23.25 × 7.33 U** 的正常键盘，
按上游语义是 28.75 × 15.84 U 的碎片，而本项目旧实现（行首 `x=0`、
`ry` 放最后、`w` 用完不复位）会得到 30.26 × 10.71 U。

## strip_backup_faukwaa.json

Faukwaa 出厂状态下 5 条配件灯条逐颗的 HSV+param 快照，
可以用 `python mvl.py strips-restore reference/strip_backup_faukwaa.json` 恢复。
