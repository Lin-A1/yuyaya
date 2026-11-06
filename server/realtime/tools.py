"""实时语音模型可以调用的工具（OpenAI Realtime 的 function calling 格式）。"""
from __future__ import annotations

import json

from ..protocol import normalize_emotion, normalize_motion

def _fn(name, desc, props, required=()):
    return {"type": "function", "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "remember",
        "description": "长期记住一件事（用户的喜好、约定、称呼）。同一件事不要重复记。",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要记住的事"}},
            "required": ["text"],
        },
    },
    {
        "type": "function",
        "name": "set_emotion",
        "description": "切换数字人当前的表情。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "enum": ["neutral", "happy", "sad", "surprised", "thinking", "shy", "angry"]},
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "set_motion",
        "description": "让数字人做一个动作。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "enum": ["nod", "shake", "hop", "jump", "droop", "tremble",
                                  "tilt", "wiggle", "sniff", "sigh"]},
            },
            "required": ["name"],
        },
    },
]

def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def execute(runtime, name: str, args: dict) -> str:
    """执行一个工具调用，返回给模型的字符串结果。"""
    avatar = runtime.avatar
    hub = runtime.hub
    args = args or {}
    if name == "remember":
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"ok": False, "error": "text is required"}, ensure_ascii=False)
        ok = avatar.session.add_fact(text)
        avatar.session.save()
        if ok:
            hub.publish({"type": "memory", "text": text})
        return json.dumps({"ok": ok, "facts": len(avatar.session.facts)}, ensure_ascii=False)
    if name == "set_emotion":
        emo = normalize_emotion(args.get("name"))
        if not emo:
            return json.dumps({"ok": False, "error": "unknown emotion"}, ensure_ascii=False)
        hub.publish({"type": "emotion", "name": emo})
        return json.dumps({"ok": True, "emotion": emo}, ensure_ascii=False)
    if name == "set_motion":
        motion = normalize_motion(args.get("name"))
        if not motion:
            return json.dumps({"ok": False, "error": "unknown motion"}, ensure_ascii=False)
        hub.publish({"type": "motion", "name": motion})
        return json.dumps({"ok": True, "motion": motion}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": f"unknown tool {name}"}, ensure_ascii=False)
