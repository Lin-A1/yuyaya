"""规范输入的分发器：把一条输入事件变成动作。

- text / speech_final -> 聊天（或动作代理）
- say                 -> 直接说话，不经过大脑
- speech_start        -> 一开口就打断（只停说话，不杀桌面动作）
- speech_partial      -> 记在 turn 上（暂不广播）
- interrupt           -> 显式打断（Esc / 旧客户端），连同桌面动作一起停
"""
from __future__ import annotations

import threading

from ..protocol import normalize_emotion
from .events import make_input


class InputDispatcher:
    def __init__(self, avatar, turns) -> None:
        self.avatar = avatar
        self.turns = turns

    def barge_in(self, session: str = "default", source: str = "voice") -> bool:
        return self.turns.interrupt(session, reason="speech_start", cancel_action=False)

    def route(self, payload: dict) -> dict:
        ev = make_input(payload)
        etype = ev["type"]
        session = ev["session"]

        if etype == "speech_start":
            interrupted = self.barge_in(session, ev["source"])
            return {"ok": True, "interrupted": interrupted, "reason": "speech_start",
                    "session": session}

        if etype == "speech_partial":
            turn = self.turns.current(session)
            if turn is not None:
                turn.partial = ev.get("text", "")
            return {"ok": True, "session": session,
                    "turn": turn.id if turn is not None else None}

        if etype == "interrupt":
            self.avatar.interrupt(reason=ev.get("reason") or "user", cancel_action=True)
            return {"ok": True}

        if etype == "say":
            return self.avatar.say(
                ev["text"], normalize_emotion(payload.get("emotion")),
                bool(payload.get("interrupt")), session=session, source=ev["source"])

        # text / speech_final
        if ev.get("mode") == "say":
            return self.avatar.say(
                ev["text"], normalize_emotion(payload.get("emotion")),
                bool(payload.get("interrupt")), session=session, source=ev["source"])

        brain = payload.get("brain")
        force_action = bool(payload.get("action") or payload.get("force_action"))
        wait = payload.get("wait")
        if wait is None:
            wait = etype == "text"       # 语音走的默认是异步

        def run() -> dict:
            return self.avatar.chat_or_agent(ev["text"], brain, force_action,
                                             session=session, source=ev["source"])

        if wait:
            return run()
        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}
