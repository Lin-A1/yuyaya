"""可插拔输入/输出适配层的契约。

- InputAdapter：把某一种输入（HTTP、浏览器语音、未来的 WebSocket 音频…）翻译成
  规范输入事件（见 events.py），交给 InputDispatcher。
- OutputAdapter：订阅 EventBus，把事件送到某一种输出（SSE、日志、未来的 webhook…）。
- TTSProvider：一种语音合成实现（本机 SAPI、未来的云端 TTS…）。

适配器只负责"搬运"，不认识业务。加一种输入/输出 = 加一个文件 + 一条配置。
"""
from __future__ import annotations

from typing import Any


class InputError(ValueError):
    """输入不合法（对应 HTTP 400）。"""


class InputAdapter:
    type = "input"

    def __init__(self, conf: dict | None = None):
        self.conf = conf or {}
        self.enabled = bool(self.conf.get("enabled", True))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def can_handle(self, event: dict) -> bool:
        return False

    def handle(self, event: dict) -> dict:
        raise NotImplementedError

    def info(self) -> dict:
        return {"type": self.type, "enabled": self.enabled}


class OutputAdapter:
    type = "output"

    def __init__(self, conf: dict | None, bus) -> None:
        self.conf = conf or {}
        self.bus = bus
        self.enabled = bool(self.conf.get("enabled", True))
        self._unsubscribe = None

    def start(self) -> None:
        if self.enabled and self._unsubscribe is None:
            self._unsubscribe = self.bus.subscribe(self.on_event)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def on_event(self, event: dict) -> None:
        """必须非阻塞；事件已经在 EventBus 的锁里，别在这里做 IO。"""

    def info(self) -> dict:
        return {"type": self.type, "enabled": self.enabled}


class TTSProvider:
    type = "tts"

    def __init__(self, conf: dict | None = None):
        self.conf = conf or {}

    def available(self) -> bool:
        raise NotImplementedError

    def synth_wav(self, text: str, voice: str = "", rate: int = 0,
                  volume: int = 100) -> bytes:
        raise NotImplementedError

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available()}


def as_dict(obj: Any) -> dict:
    return obj if isinstance(obj, dict) else {}
