"""vial-matrix-light -- 灯光调整核心库。

支持三类键盘照明后端，自动识别：

* ``amk``     -- Matrix Lab / AMK 固件（Faukwaa 等）的私有照明通道
* ``vialrgb`` -- Vial 官方 VialRGB 协议（副厂 Vial RGB 板子，支持逐键直接控制）
* ``via``     -- VIA 协议 10 及更早的标准照明通道

对外主要入口是 :class:`vial_light.device.VialDevice`。
"""

__all__ = ["colors", "effects", "transport", "device", "kbdef"]

__version__ = "1.0.0"
