"""Turn：一次"她正在说/正在想"的生命周期。

打断的最小单位。每个 session 同一时刻只有一个 active turn；barge-in 就是
把旧的 turn 标成 stale 并置位它的 cancel 事件，然后（可选）发一条 interrupt。
"""
from __future__ import annotations

import itertools
import threading
import time


class Turn:
    __slots__ = ("id", "session", "source", "kind", "cancel", "stale", "done",
                 "created_at", "partial")

    def __init__(self, sid: int, session: str, source: str, kind: str) -> None:
        self.id = sid
        self.session = session
        self.source = source
        self.kind = kind
        self.cancel = threading.Event()
        self.stale = False
        self.done = False
        self.created_at = time.time()
        self.partial = ""

    def invalidate(self) -> None:
        self.stale = True
        self.cancel.set()

    def to_dict(self) -> dict:
        return {"turn": self.id, "session": self.session, "source": self.source,
                "kind": self.kind, "stale": self.stale}


class TurnManager:
    """按 session 管理 active turn。今天就一个 session（"default"）。"""

    def __init__(self, bus=None) -> None:
        self.bus = bus
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self.active: dict[str, Turn] = {}

    def new_id(self) -> int:
        return next(self._ids)

    def current(self, session: str = "default") -> Turn | None:
        with self._lock:
            return self.active.get(session)

    def begin(self, session: str = "default", source: str = "http",
              kind: str = "chat", barge_in: bool = True,
              reason: str = "new_input", publish_interrupt: bool = True) -> Turn:
        """开始新 turn。barge_in=True 时先打断同一 session 的旧 turn。"""
        with self._lock:
            old = self.active.get(session)
            if old is not None and barge_in:
                old.invalidate()
                self.active.pop(session, None)
                if publish_interrupt and self.bus is not None:
                    # turn=None：打断事件不能被旧 turn 的 stale 过滤掉
                    self.bus.publish({"type": "interrupt", "reason": reason,
                                      "session": session, "turn": old.id,
                                      "cancel_action": False})
            turn = Turn(self.new_id(), session, source, kind)
            self.active[session] = turn
            return turn

    def interrupt(self, session: str = "default", reason: str = "user",
                  cancel_action: bool = True) -> bool:
        """打断当前 turn；即使没有 active turn 也发 interrupt（清前端残留）。"""
        with self._lock:
            old = self.active.pop(session, None)
            if old is not None:
                old.invalidate()
            if self.bus is not None:
                self.bus.publish({"type": "interrupt", "reason": reason,
                                  "session": session,
                                  "turn": old.id if old is not None else None,
                                  "cancel_action": cancel_action})
            return old is not None

    def finish(self, turn: Turn) -> None:
        with self._lock:
            if self.active.get(turn.session) is turn:
                self.active.pop(turn.session, None)
            turn.done = True

    def status(self) -> dict:
        with self._lock:
            return {s: t.to_dict() for s, t in self.active.items()}
