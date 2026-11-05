"""实时语音模型可以调用的工具（OpenAI Realtime 的 function calling 格式）。"""
from __future__ import annotations

import json

from ..protocol import normalize_emotion, normalize_motion

def _fn(name, desc, props, required=()):
    return {"type": "function", "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}


# 对模型来说，"动手"就是她自己的能力：工具名、描述、返回值里都不出现后端（Codex/Claude…）。
# 由谁执行是 Orchestrator 按 kind 路由决定的内部细节。
TOOL_SCHEMAS = [
    _fn("do_task",
        "你自己动手去做一件事：操作电脑和软件、读写文件、写改代码、查资料。"
        "调用后立刻返回，事情在你手上继续进行，你不用干等：先用一句话告诉用户“我来弄”，然后接着聊。"
        "做完、卡住或出错时你会收到 [任务进展] 通知。普通聊天不要调用。",
        {"task": {"type": "string", "description": "要做成什么，说清楚，一两句话"},
         "kind": {"type": "string", "enum": ["desktop", "code", "search", "general"],
                  "description": "desktop=操作桌面/软件，code=写改代码或文件，search=查资料，general=其它"},
         "after": {"type": "array", "items": {"type": "number"},
                   "description": "要等哪几件（任务号）先做成再开始"},
         "confirmed": {"type": "boolean",
                       "description": "涉及删除/覆盖/发送/付款时，必须先口头问用户并得到明确同意，再填 true"}},
        ["task"]),
    _fn("task_status", "看你手上的事做到哪了。不填 job_id 就列出全部。用户问“弄得怎么样了”时用。",
        {"job_id": {"type": "number"}}),
    _fn("task_cancel", "停下手上的某件事（用户说不用做了）。", {"job_id": {"type": "number"}}, ["job_id"]),
    _fn("task_answer",
        "做事时卡在一个需要拿主意的问题上：能根据记忆或常识自己定的，就用它定下来继续做，不要打扰用户；"
        "定不了或有风险，再问用户，拿到答案后用它继续。",
        {"job_id": {"type": "number"}, "text": {"type": "string"}}, ["job_id", "text"]),
    _fn("schedule",
        "给未来的自己留一条提醒：过一段时间后你会收到通知，再自然地提醒用户。",
        {"seconds": {"type": "number", "description": "多少秒之后"},
         "reason": {"type": "string", "description": "到时候要提醒什么"}}, ["seconds", "reason"]),
    _fn("quiet", "用户说“别吵我/安静一会”时调用：这段时间里除非很重要，你不会主动开口。",
        {"minutes": {"type": "number", "description": "默认 30"}}),
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


# 旧名字照样能用（老配置、老提示词），但不再出现在 TOOL_SCHEMAS 里
ALIASES = {"agent_start": "do_task", "codex": "do_task", "computer_action": "do_task",
           "agent_status": "task_status", "agent_cancel": "task_cancel", "agent_reply": "task_answer"}

_JOB_FIELDS = ("id", "task", "status", "elapsed", "question", "result", "error", "progress")


def _for_model(job: dict) -> dict:
    """给模型看的任务视图：只有事情本身，不带"谁做的"。"""
    out = {k: job[k] for k in _JOB_FIELDS if job.get(k) not in (None, "", [])}
    if "id" in out:
        out["job_id"] = out.pop("id")
    # progress 是后端输出流的最近几行，可能很长很吵；只给最后一条，够她"说一句正在做什么"
    prog = out.get("progress")
    if isinstance(prog, list):
        out["progress"] = (prog[-1] if prog else None) or None
        if out["progress"] is None:
            out.pop("progress")
    if out["status"] in ("running", "queued") and not out.get("elapsed_note"):
        out["note"] = ("这件事还在做。用户问进度就用一句话说说在做什么，"
                       "不要说得像已经完成了。")
    return out


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def execute(runtime, name: str, args: dict) -> str:
    """执行一个工具调用，返回给模型的字符串结果。"""
    avatar = runtime.avatar
    hub = runtime.hub
    args = args or {}
    orch = getattr(avatar, "orch", None)
    init = getattr(avatar, "initiative", None)
    legacy = name
    name = ALIASES.get(name, name)
    if name == "do_task":
        goal = (args.get("task") or args.get("goal") or "").strip()
        if not goal:
            return _dump({"ok": False, "error": "task is required"})
        kind = args.get("kind") or ("general" if legacy in ("do_task", "agent_start") else "desktop")
        # backend 不对模型开放；旧调用里带了也照样传（兼容），但结果里不回显
        r = orch.start(goal, args.get("backend"), kind, "", args.get("after") or args.get("depends_on"),
                       bool(args.get("confirmed")), "realtime")
        out = {"ok": bool(r.get("ok")), "accepted": bool(r.get("accepted"))}
        if r.get("job_id"):
            out.update(job_id=r["job_id"], status=r.get("status"),
                       progress="刚开始做，还没做完。现在只能说“在弄了/马上弄”，不能说“弄好了”；"
                                "做完会收到 [任务进展]，到时再告诉用户结果。")
        if r.get("needs_confirm"):
            out.update(needs_confirm=True, task=goal,
                       hint="这件事不可逆。先用一句话向用户复述并问是否确认；明确同意后带 confirmed=true 再调用。")
        elif not r.get("accepted"):
            out["error"] = "现在动不了手" if "agent" in (r.get("reason") or "") else (r.get("reason") or "")
        return _dump(out)
    if name == "task_status":
        jid = args.get("job_id")
        st = orch.status(int(jid) if jid is not None else None)
        if jid is not None:
            return _dump(st if st.get("not_found") else _for_model(st))
        return _dump({"tasks": [_for_model(j) for j in st.get("jobs", [])], "active": st.get("active", [])})
    if name == "task_cancel":
        return _dump(orch.cancel(int(args.get("job_id", 0))))
    if name == "task_answer":
        return _dump(orch.answer(int(args.get("job_id", 0)), args.get("text") or ""))
    if name == "schedule":
        return json.dumps(init.add_schedule(args.get("seconds") or 60, args.get("reason") or ""),
                          ensure_ascii=False)
    if name == "quiet":
        return json.dumps(init.quiet(args.get("minutes")), ensure_ascii=False)
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
