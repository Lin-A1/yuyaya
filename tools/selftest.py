"""Self-check for the daily chat branch."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

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
        "io": {
            "inputs": [{"type": "http"}, {"type": "browser_speech"}],
            "outputs": [{"type": "sse"}, {"type": "recorder", "name": "rec"}],
        },
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

            # 统一输入入口
            r = client.post("/api/input", json={"type": "text", "text": "今天天气怎么样", "wait": True})
            check("统一输入走聊天模型", r.status_code == 200 and r.json().get("brain") == "demo",
                  r.text[:120])
            r = client.post("/api/input", json={"type": "text", "text": ""})
            check("空输入被拒", r.status_code == 400, r.text[:80])

            # 全双工：一开口就打断正在进行的回答
            rec = client.app.state.outputs["rec"]
            rec.clear()
            client.post("/api/chat", json={"text": "给我讲一个很长很长的故事", "wait": False})
            time.sleep(0.15)
            r = client.post("/api/input", json={"type": "speech_start"})
            check("speech_start 返回 200", r.status_code == 200, r.text[:80])
            time.sleep(2.2)
            ev = rec.snapshot()
            ints = [e for e in ev if e.get("type") == "interrupt"]
            check("一开口触发打断", any(e.get("reason") == "speech_start" for e in ints),
                  str([(e.get("type"), e.get("reason")) for e in ev][-10:]))
            old_ids = {e.get("id") for e in ev if e.get("type") == "speech"
                       and e.get("id") and e.get("id") != 0}
            r = client.post("/api/input", json={"type": "speech_final", "text": "你好呀", "wait": True})
            check("说完后能正常回答", r.status_code == 200 and bool(r.json().get("reply")),
                  r.text[:120])

            # TTS provider 注册表
            cfg2 = client.get("/api/voice/config").json()
            check("TTS provider 从注册表来", cfg2["tts"].get("provider") == "sapi",
                  str(cfg2.get("tts")))
            check("voice config 带 realtime 能力", "realtime" in cfg2, str(cfg2)[:120])

            # Codex 工具：同步执行并拿回结果（实时语音用它和 Codex 交互）
            time.sleep(0.3)
            res = client.app.state.avatar.actions.run("测试任务", timeout=20)
            check("Codex 工具返回输出", res.get("ok") is True and "action ok" in res.get("output", ""),
                  str(res)[:160])

            # 实时语音运行时：没配 key 时不可用，但工具表里要有动手的工具 do_task
            rt = client.app.state.realtime
            check("realtime 未配 key 时不可用", rt.available is False, str(rt.info()))
            check("realtime 工具包含 do_task",
                  any(s.get("name") == "do_task" for s in __import__("server.realtime.tools", fromlist=["TOOL_SCHEMAS"]).TOOL_SCHEMAS),
                  str(rt.info()))

            # 后台 agent 编排 + 前台自主触发（假 CLI 后端，不花 token）
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            from selftest_agents import agents_suite
            agents_suite(client, tmp, check)

            try:
                with client.websocket_connect("/ws/voice") as ws:
                    msg = ws.receive_json()
                check("realtime 关闭时 WS 返回错误", msg.get("type") == "error", str(msg)[:120])
            except Exception as e:
                check("realtime 关闭时 WS 返回错误", False, f"{type(e).__name__}: {e}")
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
