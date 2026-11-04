"""HTTP 文本输入：/api/input、/api/chat、/api/say 的文本部分。"""
from __future__ import annotations

from ..base import InputAdapter


class HttpInputAdapter(InputAdapter):
    type = "http"

    def __init__(self, conf: dict | None = None, dispatcher=None) -> None:
        super().__init__(conf)
        self.dispatcher = dispatcher

    def can_handle(self, event: dict) -> bool:
        return event.get("type") in ("text", "say")

    def handle(self, event: dict) -> dict:
        return self.dispatcher.route(event)

    def info(self) -> dict:
        return {**super().info(), "transport": "http"}
