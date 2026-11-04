"""事件总线：所有输出适配器都从这里拿事件。

和原来的 Hub 保持同样的对外语义（publish / snapshot / mood_note），
但多了两件事：

1. ``subscribe(cb)`` —— 输出适配器注册回调，SSE 只是其中一个。
2. ``publish(event, turn=turn)`` —— 如果 turn 已经被打断（stale），
   这条事件直接丢掉，保证"打断之后不会再冒出一句旧台词"。
"""
from __future__ import annotations

import logging
import threading
import time

from ..protocol import EMOTION_CN

log = logging.getLogger("yuyaya.bus")

# 强情绪没人继续推动时自然回落（秒）。前端 avatar.js 的 EMO_LINGER 更短，
# 这里只是服务端快照的兜底：重连恢复、给大脑的情绪提示都不该停在很久以前的害羞/惊讶。
EMOTION_TTL = {"happy": 25, "sad": 90, "surprised": 8, "shy": 10, "angry": 45}


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list = []
        self.loop = None                      # 兼容旧代码：SSE 适配器会用它
        self.snapshot = {"emotion": "neutral", "state": "idle"}
        self.emotion_since = time.time()
        self.seq = 0

    # ---------- 订阅 ----------

    def subscribe(self, callback):
        """注册一个输出适配器，返回取消订阅的函数。"""
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subs.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    # ---------- 情绪快照（沿用 Hub 的语义） ----------

    def set_emotion(self, name: str) -> None:
        with self._lock:
            if name != self.snapshot["emotion"]:
                self.emotion_since = time.time()
            self.snapshot["emotion"] = name

    def set_state(self, state: str) -> None:
        """只改快照，不广播（兼容旧代码里直接写 snapshot 的地方）。"""
        with self._lock:
            self.snapshot["state"] = state

    def mood_note(self) -> str:
        """告诉大脑她此刻的情绪，让回复从这个情绪自然过渡。"""
        self.refresh()
        with self._lock:
            emo = self.snapshot["emotion"]
            since = self.emotion_since
        if emo == "neutral":
            return ""
        secs = int(time.time() - since)
        ago = "刚刚才开始" if secs < 20 else (
            f"已经持续了约 {secs // 60} 分钟" if secs >= 120 else f"已经持续了约 {secs} 秒")
        return f"（你此刻的情绪：{EMOTION_CN.get(emo, emo)}，{ago}。）\n"

    def refresh(self) -> None:
        """强情绪在 TTL 后回落到平静，避免快照一直停在害羞/惊讶上。"""
        with self._lock:
            emo = self.snapshot["emotion"]
            ttl = EMOTION_TTL.get(emo)
            if ttl and time.time() - self.emotion_since > ttl:
                self.snapshot["emotion"] = "neutral"
                self.emotion_since = time.time()

    # ---------- 发布 ----------

    def publish(self, event: dict, turn=None) -> bool:
        """线程安全。返回 False 表示这条事件因为 turn 已过期被丢弃。"""
        with self._lock:
            if turn is not None and getattr(turn, "stale", False):
                return False
            self.seq += 1
            event.setdefault("seq", self.seq)
            etype = event.get("type")
            if etype == "emotion":
                # 临时表情只影响已连接的前端，快照保留它背后的持续情绪
                if not event.get("hold"):
                    self.set_emotion(event.get("name", "neutral"))
            elif etype == "state":
                self.snapshot["state"] = event.get("state", "idle")
            elif etype == "interrupt":
                self.snapshot["state"] = "idle"
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                log.exception("输出适配器处理事件失败：%r", event)
        return True
