# vial-matrix-light：Tkinter 前端 → Web 前端 迁移方案

## Context（为什么做这件事）

当前项目是一个键盘灯光调整工具，**核心协议层与界面层已经分离得很干净**：

- `vial_light/device.py`（1565 行）— VIA / Vial / VialRGB / AMK 全部协议，**完全不含 Tkinter**
- `vial_light/transport.py`、`effects.py`、`kbdef.py`、`colors.py`、`presets.py` — 纯逻辑，也无 Tkinter
- `vial_light/gui.py`（3192 行）+ `vial_light/theme.py`（961 行）— 仅这两处依赖 Tkinter

所以本次改造是**替换表现层**，不动协议层。目标：用本地 Web 服务 + 浏览器界面取代 Tkinter 桌面窗口，
五个页签（灯光 / 分区 / 配列 / 逐键 / 信息）功能对齐，并新增浅色 / 深色双主题切换。

用户已确认：**完全替换并删除** `gui.py` / `theme.py`；**两套主题可切换**。

## 框架选型（已定）

选 **Flask 1.1.2 + 免构建前端（原生 ES Module + Canvas/SVG）**，理由基于本机实测：

| 约束 | 实测结果 | 结论 |
|---|---|---|
| Python 版本 | **3.6.8**（`C:\Program Files\Python36`） | FastAPI/uvicorn 需要 3.7+，不可用 |
| 已装 Web 库 | **Flask 1.1.2 + Werkzeug 1.0.1 已装**；flask_socketio / gevent / eventlet / websockets / waitress **均未装** | 用 Flask，**零新增依赖** |
| Node 版本 | v12.22.9（当前 Vite/React 需 Node 18+） | 不引入构建链，避免升级 Node |
| 项目哲学 | README 强调「Windows 零依赖即可运行」 | 免构建前端最贴合 |

**通信方式**：命令走 REST（POST/GET JSON）；实时状态走 **SSE**（`text/event-stream`，
Flask 流式响应即可，无需 WebSocket 库）。这与原 `_AsyncWorker` + `_tick` 的推送模型天然对应。

## 架构设计

### 后端：新增 `vial_light/webapp/` 包

```
vial_light/webapp/
├── __init__.py
├── session.py     DeviceSession：单工作线程 + 任务队列 + 快照缓存 + 事件总线
├── server.py      Flask 应用工厂 + 路由（REST + SSE）
└── static/
    ├── index.html
    ├── styles.css      两套主题（CSS 变量 + [data-theme]）
    └── js/
        ├── api.js        fetch 封装 + SSE 订阅 + 日志
        ├── theme.js      主题切换（localStorage 记忆）
        ├── app.js        外壳：页签、设备选择、状态栏、日志面板
        ├── light.js      灯光页
        ├── zone.js       分区页（含配件灯条栏）
        ├── layout.js     配列页（SVG）
        ├── perkey.js     逐键页（Canvas + 动画）
        ├── info.js       信息页（含原始报文台）
        └── presets.js    方案库
```

**`session.py` 是本次改造的关键**，直接对应原 `_AsyncWorker`（[gui.py L80-141](file:///d:/Codes/vial-matrix-light/vial_light/gui.py#L80-L141)）：

- **单工作线程 + `queue.Queue`**：所有设备 I/O 都排到这一个线程串行执行。
  原因：`VialDevice._send` 虽有 `_io_lock` 保证单报文原子，但一次「逐键推 74 颗」是
  多条报文，多线程直连会交错。串行化才能保持原 GUI 的语义。Flask 用 `threaded=True`，
  HTTP 请求线程只负责入队 + 等待结果。
- **快照缓存 `snapshot`**：保存连接状态、后端、`LightState`、zone、strips、leds、layout、
  capabilities。`GET /api/state` **直接返回缓存、不碰设备**，保证 UI 永不卡死；
  真正读设备由显式的 `/api/light/read`、`/api/leds/refresh` 触发。
- **事件总线**：`log(text, level)` / `state` / `busy`（对应原 `_busy_enter/_exit` 计数）
  三类事件推给所有 SSE 订阅者，替代原 `App.log()` 与底部状态栏。
- **节流下发**：`queue_state()` + 40ms 定时 flush，等价于
  [gui.py L3171-L3235](file:///d:/Codes/vial-matrix-light/vial_light/gui.py#L3171-L3235) 的「实时下发」开关。
- **逐键推送差量**：保留原 `_pushed` 影子缓冲思路，只发变化的灯号。
- **关闭**：`atexit` / 信号处理里 `dev.close()`，与原 `_on_close` 等价。

复用（不重写）的核心 API：`VialDevice.discover()/discover_all_raw()/open()/detect_lighting()/
read_lighting()/set_all()/save()/describe()/raw_request()`、`zone_groups()/push_zoned()`、
`strips()/set_strip_mode()/set_strip_color()/set_strip_led_speed()`、`matrix_map()/perkey_set()/
perkey_fill()/perkey_read()/enter_matrix_custom()`、`effects.*`、`presets.PresetStore`、
`kbdef.*`、`colors.*`、`transport.backend_description()`。

### REST + SSE 接口清单

| 方法 | 路径 | 对应原实现 |
|---|---|---|
| GET | `/api/devices` | `refresh_devices` / `pick_all_devices` |
| POST | `/api/connect` `{index, all}` | `connect_device` |
| POST | `/api/disconnect` | `disconnect` |
| GET | `/api/state` | `_sync_widgets` + `refresh_zone_panel` + `_refresh_strip_bar` |
| POST | `/api/light` `{effect,speed,hue,sat,val}` | `queue_state` + `apply_state_to_device` |
| POST | `/api/light/read` · `/api/light/save` | `pull_from_device` · `save_to_device` |
| GET | `/api/effects` | `effect_ids` + `effect_label` |
| POST | `/api/zone/push` `{zones,colors,val}` · `/api/zone/swap` · `/api/zone/sync` | `push_zone` / `_push_zone_amk` / `_swap_zones` / `_sync_zone_colours_from_device` |
| POST | `/api/strip/mode` · `/api/strip/color` · `/api/strip/all` · `/api/strip/bright` · `/api/strip/speed` | `_apply_strip_mode` / `_set_strip_color` / `_on_strip_bright` / `_on_strip_speed` |
| GET | `/api/layout` | `draw_layout`（返回矩阵网格 + KLE 几何，前端画 SVG） |
| GET | `/api/leds` | `load_leds` |
| POST | `/api/perkey/push` · `/api/perkey/fill` · `/api/perkey/read` | `push_leds` / `render_pattern` / `_pk_pull` |
| POST | `/api/raw` `{hex}` | `send_raw` |
| GET | `/api/info` | `update_info`（复用其字段顺序与标签） |
| GET/POST | `/api/presets` `...` | `_preset_store` / `snapshot_lighting` / `preset_*` |
| GET | `/api/events` | **SSE**：`log` / `state` / `busy` |

### 前端：免构建 SPA

- `index.html` 用 `<script type="module">` 引入 `js/app.js`，无打包步骤。
- **双主题**：`styles.css` 定义 CSS 变量，`:root[data-theme="light"]` = Win11 Mica 浅色
  （浅灰底 `#f3f3f3` + 圆角卡片 + 天蓝强调 + Segoe UI），`[data-theme="dark"]` = 深色霓虹
  （参考 `reference/` 与 `samples/*.html` 的 `#1e1f29` 底 + 紫/青强调）。右上角切换，localStorage 记忆。
- **图形**：色相条 / 分区概览 / 逐键 LED 用 `<canvas>`；配列用 SVG（已验证可行，
  见 [faukwaa-layout-preview.html](file:///d:/Codes/vial-matrix-light/samples/faukwaa-layout-preview.html)）。
- **本地动画**：放前端 `requestAnimationFrame`（节流 ~30fps）+ 防抖 POST 差量，
  等价于原 `_anim_step` + `_schedule_pk_push`（[gui.py L3330-L3346](file:///d:/Codes/vial-matrix-light/vial_light/gui.py#L3330-L3346)）。
  这样 SSE 只承担日志与低频状态，避免高频推送。
- 保留原界面里所有**用户可见的提示文案**（如「配件灯非自定义档改不了颜色」「AMK 轴灯关不掉」
  「物理配列与实物不符，请用矩阵网格」等），这是这个工具的核心价值，不能丢。

### 入口与脚本改动

- `main.py` 重写为 Web 启动器：建 app → 选空闲端口（默认 8765）→ `app.run(threaded=True, use_reloader=False)`
  → `webbrowser.open` 打开浏览器。保留 `--selftest`（构建路由 + 校验静态资源后立即退出，返回码语义不变）
  与全局异常兜底、`run.log` 写入。
- 新增 `--port` / `--no-browser` 参数。
- `run.bat`：文案改为启动 Web 服务，保留自动挑解释器 + `run.log` 逻辑。
- `requirements.txt`：Flask 标注为 Web 界面依赖（Python 3.6 下用 `flask>=1.1,<2`）。
- `README.md`：第 2、3、8 节改为 Web 版的启动方式、界面说明、目录结构；截图待补。

### 删除

- `vial_light/gui.py`
- `vial_light/theme.py`

（保留 `mvl.py` 命令行与全部核心协议模块不动。）

## 实施步骤

1. 建 `vial_light/webapp/` 骨架，先写 `session.py`（工作线程 + 快照缓存 + 事件总线 + 节流）。
2. 写 `server.py` 的 REST 路由与 SSE，逐个接口对接 `VialDevice`；加 JSON 序列化辅助
   （`LightState.as_dict()`、`Led`、`AmkStrip`、matrix map、KLE layout）。
3. 重写 `main.py` 启动器与 `--selftest`；打通「无设备」降级路径。
4. 前端外壳：`index.html` + `styles.css`（双主题）+ `app.js`（页签、设备选择、日志、状态栏、SSE）。
5. 逐页实现：灯光 → 分区（含配件灯条栏）→ 配列 → 逐键 → 信息 → 方案库。
6. 删除 `gui.py` / `theme.py`，更新 `run.bat` / `requirements.txt` / `README.md`。

## 验证方式

- `python main.py --selftest` → 退出码 0（无设备也应通过）。
- `python main.py --no-browser` 起服务，`GET /api/state`、`/api/devices`、`/api/info` 返回合法 JSON；
  `/api/events` 能收到 SSE 心跳与日志。
- 用 browser_use 子代理打开页面：五个页签可切换、主题可切换并记忆、无设备时给出明确提示而非报错。
- 有实物键盘时人工回归：连接 → 改灯（实时下发）→ 逐键上色 → 配件灯条切档 / 上色 → 保存 → 方案存/取。
- 本轮无硬件，重点保证「无设备」路径不崩、协议层零改动（`device.py` 等文件 diff 应为空）。

## 风险与取舍

- **Flask 开发服务器**：单机本地工具足够；`threaded=True` 下 SSE 每连接占一个线程，
  订阅者只有本机浏览器，无压力。不引入 waitress/gunicorn（未安装且非必要）。
- **Python 3.6**：代码避免 f-string 之外的 3.7+ 语法（如 `dataclasses` 已装 0.8 可用，但优先用普通类）。
- **动画/高频推送**：放前端以减轻服务端与 SSE 压力（与原实现等价节流）。
- **设备并发**：全部 I/O 收敛到单工作线程，避免多报文操作交错。