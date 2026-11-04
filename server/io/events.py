"""规范输入事件。

所有输入适配器最后都产出一个这样的 dict，交给 InputDispatcher：

    {"type": "text",         "text": "你好", "source": "http"}
    {"type": "say",          "text": "[happy]我回来啦", "source": "cli"}
    {"type": "speech_start", "source": "browser"}
    {"type": "speech_partial","text": "今天", "source": "browser"}
    {"type": "speech_final", "text": "今天天气怎么样", "source": "browser"}
    {"type": "interrupt",    "reason": "user|escape|barge_in"}

text 输入可以用 mode="chat"（默认）或 mode="say"。老端点 /api/chat、/api/say
就是这两种的薄包装。
"""
from __future__ import annotations

from .base import InputError

INPUT_TYPES = ("text", "say", "speech_start", "speech_partial", "speech_final",
               "interrupt")


def make_input(body: dict | None) -> dict:
    """校验并补默认值。不合法抛 InputError（HTTP 400）。"""
    if not isinstance(body, dict):
        raise InputError("请求体必须是 JSON 对象")
    etype = (body.get("type") or "").strip()
    if not etype:
        # 兼容：只有 text 的请求当作一问一答
        if (body.get("text") or "").strip():
            etype = "text"
        else:
            raise InputError("type is required")
    if etype not in INPUT_TYPES:
        raise InputError(f"unknown input type {etype!r}")

    ev = dict(body)
    ev["type"] = etype
    ev["session"] = (body.get("session") or "default").strip() or "default"
    ev["source"] = (body.get("source") or ("browser" if etype.startswith("speech_") else "http")).strip()

    if etype in ("text", "say", "speech_final"):
        text = (body.get("text") or "").strip()
        if not text:
            raise InputError("text is required")
        ev["text"] = text
    if etype == "speech_partial":
        ev["text"] = (body.get("text") or "").strip()
    if etype == "text":
        mode = (body.get("mode") or "chat").strip()
        if mode not in ("chat", "say"):
            raise InputError("mode must be chat or say")
        ev["mode"] = mode
    return ev
