"""Self-check for the daily chat branch."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from server.intent import is_action_request  # noqa: E402

fails = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"[{'  ok  ' if cond else ' FAIL '}] {name}{(' - ' + extra) if extra else ''}")


def test_intent() -> None:
    actions = ["打开记事本", "帮我打开浏览器", "点一下开始", "按一下空格"]
    chats = ["怎么打开任务管理器", "今天天气怎么样", "我刚刚打开了游戏"]
    check("动作意图", all(is_action_request(t) for t in actions))
    check("普通问答不误判", not any(is_action_request(t) for t in chats))


def test_tts() -> None:
    from server import tts
    if not tts.available():
        print("[ SKIP ] SAPI unavailable")
        return
    wav = tts.synth_wav("你好", volume=20)
    check("本机 TTS", wav[:4] == b"RIFF" and len(wav) > 1000, f"{len(wav)} bytes")


def test_api() -> None:
    try:
        from fastapi.testclient import TestClient
    except Exception as e:
        check("fastapi.testclient", False, str(e))
        return
    from server.app import create_app

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="yuyaya-main-test-"))
    cfg = {
        "default": "demo",
        "history_turns": 4,
        "brains": {
            "demo": {"type": "demo", "label": "demo"},
            "codex": {"type": "cli", "label": "Codex", "cmd": ["codex"]},
        },
        "actions": {
            "enabled": True, "timeout": 20, "ack": "好",
            "cmd": [sys.executable, "-c", "print('action ok')"],
        },
        "voice": {"tts": {"enabled": True, "volume": 20}},
    }
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    old_cfg, old_state = os.environ.get("YUYAYA_CONFIG"), os.environ.get("YUYAYA_STATE")
    os.environ["YUYAYA_CONFIG"] = str(cfg_path)
    os.environ["YUYAYA_STATE"] = str(tmp / "memory.json")
    try:
        with TestClient(create_app()) as client:
            r = client.post("/api/chat", json={"text": "今天天气怎么样", "wait": True})
            check("普通问答走聊天模型", r.status_code == 200 and r.json().get("brain") == "demo",
                  r.text[:120])
            r = client.post("/api/chat", json={"text": "打开记事本", "wait": True})
            check("动作请求走执行器",
                  r.status_code == 200 and r.json().get("action") is True, r.text[:160])
            r = client.post("/api/chat", json={"text": "hi", "brain": "codex", "wait": True})
            check("聊天不能直接选 CLI 智能体", r.status_code == 400, r.text[:120])
            r = client.get("/api/voice/config")
            check("语音配置接口", r.status_code == 200 and "tts" in r.json(), r.text[:120])
            r = client.post("/api/tts", json={"text": "你好", "volume": 20})
            check("TTS 接口", r.status_code == 200 and r.content[:4] == b"RIFF",
                  f"{r.status_code} {len(r.content)} bytes")
    finally:
        if old_cfg is None:
            os.environ.pop("YUYAYA_CONFIG", None)
        else:
            os.environ["YUYAYA_CONFIG"] = old_cfg
        if old_state is None:
            os.environ.pop("YUYAYA_STATE", None)
        else:
            os.environ["YUYAYA_STATE"] = old_state


def main() -> None:
    test_intent()
    test_tts()
    test_api()
    print("\n全部通过" if not fails else f"\n{fails} 项失败")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
