# reference/ — 权威参考资料

本目录存放**逆向出来的权威依据**，不是运行时代码，不参与打包。

## matrix-vialweb/

Matrix 官方配置网页 `https://config.matrix-lab.com/`（Vial Web）的 Python 源码明文。

**提取方法**：该站是 Pyodide/Emscripten 打包的单页应用，主 JS 里有一份
`{"filename": ..., "start": N, "end": M}` 的文件清单，配套的 `.data` 包里
就是源文件明文，按偏移量直接切出来即可。

**为什么重要**：这份源码是 `vial_light/device.py` 里 `0xFD` 扩展协议的
**唯一权威依据**。`amk/protocol.py` 定义了全部命令号与帧格式，
`amk/rgb.py` 定义了配件灯灯效表（`RL_EFFECT_*`）。

关键文件：

| 文件 | 用途 |
|---|---|
| `amk/protocol.py` | **命令表 + 全部帧格式**（首选查阅对象） |
| `amk/rgb.py` | 配件灯灯效表 `RL_EFFECT_*` |
| `amk/rgb_strip.py` | 配件灯 UI 逻辑 |
| `amk/rgb_matrix.py` | 轴灯 |
| `amk/rgb_grid.py` | 点阵屏 |
| `amk/rgb_indicator.py` | 指示灯 |
| `amk/animation.py` | `.anm`/`.sml`/`.bkg`/`.crs`/`.sts` 定制动画格式 |
| `editor/rgb_configurator.py` | `QMK_RGBLIGHT_EFFECTS` / `VIALRGB_EFFECTS` 表 |
| `util.py` | `hid_send()` —— 证明扩展协议与标准 Vial 共用同一条 32 字节通道 |

## strip_backup_faukwaa.json

Faukwaa 出厂状态下 5 条配件灯条逐颗的 HSV+param 快照，
可以用 `python mvl.py strips-restore reference/strip_backup_faukwaa.json` 恢复。
