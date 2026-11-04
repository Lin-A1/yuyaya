"""记录最近的事件，给自检和排障用（也能证明"输出只是一次订阅"）。"""
from __future__ import annotations

import collections
import threading

from ..base import OutputAdapter


class RecorderOutput(OutputAdapter):
    type = "recorder"

    def __init__(self, conf: dict | None, bus) -> None:
        super().__init__(conf, bus)
        limit = int(self.conf.get("maxlen", 500))
        self._events: collections.deque = collections.deque(maxlen=limit)
        self._lock = threading.Lock()

    def on_event(self, event: dict) -> None:
        with self._lock:
            self._events.append(dict(event))

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
