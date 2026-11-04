"""浏览器语音输入：前端 SpeechRecognition 发来的 speech_start/partial/final。

ASR 本身仍在浏览器里做；这里只负责把"开口/中间结果/说完"翻译成规范输入。
以后接外部 STT 或 WebSocket 音频，只要再写一个同类型的适配器。
"""
from __future__ import annotations

from ..base import InputAdapter


class BrowserSpeechInputAdapter(InputAdapter):
    type = "browser_speech"

    def __init__(self, conf: dict | None = None, dispatcher=None) -> None:
        super().__init__(conf)
        self.dispatcher = dispatcher
        self.barge_in = (self.conf.get("barge_in") or "speech_start")

    def can_handle(self, event: dict) -> bool:
        return event.get("type") in ("speech_start", "speech_partial", "speech_final")

    def handle(self, event: dict) -> dict:
        if event.get("type") == "speech_start" and self.barge_in == "final":
            # 配置成"说完再打断"时不处理开口事件
            return {"ok": True, "ignored": "barge_in=final"}
        return self.dispatcher.route(event)

    def info(self) -> dict:
        return {**super().info(), "provider": "browser", "continuous": True,
                "barge_in": self.barge_in}
