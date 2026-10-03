"""本地 Web 界面（Flask）。

结构::

    webapp/
    ├── session.py   DeviceSession：单工作线程 + 快照缓存 + 事件总线
    ├── server.py    Flask 应用工厂 + REST / SSE 路由
    └── static/      免构建前端（原生 ES Module + Canvas/SVG）

前端不再依赖 Tkinter，核心协议层（device / transport / effects / kbdef /
colors / presets）**一行都没改** —— 这里只是它的另一个表现层。
"""

__all__ = ["session", "server"]