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


def _brains_ok() -> bool:
    """四种接口的大脑适配器都能构建出来（不真的发请求）。"""
    try:
        from server.brains import build_brain
    except Exception:
        return False
    for kind in ("completions", "responses", "messages", "generateContent", "demo"):
        try:
            b = build_brain(kind, {"type": kind, "model": "m", "api_key_env": "NOPE_KEY"})
            b.availability()
        except Exception:
            return False
    return True


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
            check("聊天不能直接选不存在的 CLI 名字", r.status_code == 404, r.text[:120])
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

            # 页面设置：三层配置（全双工 / 半双工 LLM+TTS+ASR / agent CLI）
            st = client.get("/api/settings").json()
            check("设置接口有全双工 / 半双工 / agent 三层",
                  {"full", "llm", "tts", "asr", "agents", "keys"} <= set(st),
                  str(list(st))[:120])
            check("半双工只列一家全双工服务商（Qwen）",
                  [p["id"] for p in st["full"]["providers"]] == ["qwen"],
                  str([p["id"] for p in st["full"]["providers"]]))
            check("全双工音色表有平台预置音色",
                  len(st["full"]["voices"]) > 5 and st["full"]["voice"] == "qiaoxiaojiao_v3.1",
                  str(st["full"]["voice"]))
            check("LLM 只认三种线协议 + 离线演示",
                  [t["id"] for t in st["llm"]["types"]] ==
                  ["completions", "responses", "messages", "generateContent", "demo"],
                  str([t["id"] for t in st["llm"]["types"]]))
            check("预设带各自的模型表和接口类型",
                  all("models" in p and "type" in p for p in st["llm"]["presets"])
                  and any(p["id"] == "deepseek" for p in st["llm"]["presets"]),
                  str([(p["id"], p["type"]) for p in st["llm"]["presets"]]))
            check("四种大脑适配器都能构建",
                  _brains_ok(), "")
            check("模式是二选一（full / half）",
                  [m["id"] for m in st["modes"]] == ["full", "half"],
                  str([m["id"] for m in st["modes"]]))
            check("人设可在设置里改（有默认值和自定义标记）",
                  st["persona"]["default_identity"] and "customized" in st["persona"],
                  str(list(st["persona"]))[:100])
            check("ASR 可选 none（不启用语音输入）",
                  any(p["id"] == "none" for p in st["asr"]["providers"]),
                  str([p["id"] for p in st["asr"]["providers"]]))
            check("agent 三个 CLI 都被探测到（装没装都要有记录）",
                  len(st["agents"]["cli"]) == 3
                  and all({"installed", "models", "enabled"} <= set(c) for c in st["agents"]["cli"]),
                  str([(c["id"], c["installed"]) for c in st["agents"]["cli"]]))
            check("设置接口不泄露 key 明文",
                  all("hint" in k and len(k.get("hint", "")) < 24 for k in st["keys"]),
                  str(st["keys"])[:120])

            # 换 TTS 引擎：音色必须跟着换到新引擎的表里
            r = client.post("/api/settings", json={
                "tts": {"provider": "sapi"},
                "keys": {"DEEPSEEK_API_KEY": "sk-selftest-not-real"}})
            changed = r.json().get("changed") or {}
            check("设置能改朗读引擎并立即重建 provider",
                  r.status_code == 200 and changed.get("tts.provider") == "sapi"
                  and client.app.state.tts_provider.info()["provider"] == "sapi", str(changed))
            check("换引擎后音色自动换成新引擎的",
                  changed.get("tts.voice") == "Microsoft Huihui Desktop", str(changed))
            check("key 写进 state/secrets.json 并立即生效",
                  os.environ.get("DEEPSEEK_API_KEY") == "sk-selftest-not-real"
                  and (tmp / "secrets.json").exists(), str(os.environ.get("DEEPSEEK_API_KEY")))
            check("key 只回显掩码",
                  r.json()["keys"][1]["hint"] == "sk-…real", str(r.json()["keys"][1]))
            r = client.post("/api/settings", json={"keys": {"DEEPSEEK_API_KEY": ""}})
            check("清空 key 会删掉它",
                  not os.environ.get("DEEPSEEK_API_KEY")
                  and not (r.json()["keys"][1]["set"]), str(r.json()["keys"][1]))

            # 切 LLM：通用三件套，活跃大脑跟着换
            r = client.post("/api/settings", json={"llm": {
                "preset": "qwen", "type": "completions", "model": "qwen-plus",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key_env": "DASHSCOPE_API_KEY"}})
            changed = r.json().get("changed") or {}
            check("切 LLM 会同时更新活跃大脑",
                  changed.get("llm.preset") == "qwen"
                  and client.app.state.avatar.active == "completions", str(changed))
            check("LLM 存的是通用四件套（不是服务商）",
                  r.json()["llm"]["type"] == "completions"
                  and r.json()["llm"]["model"] == "qwen-plus", str(r.json()["llm"])[:120])
            # 直接填 base_url 换一个没预设过的服务商也能用
            r = client.post("/api/settings", json={"llm": {
                "type": "completions", "base_url": "https://example.invalid/v1",
                "model": "some-model", "api_key_env": "SOME_KEY"}})
            d = r.json()["llm"]
            check("能配任意的 OpenAI 兼容服务商",
                  d["base_url"] == "https://example.invalid/v1" and d["model"] == "some-model",
                  str(d)[:140])

            # 模式互斥：全双工模式下半双工的 ASR 不参与
            # （realtime.enabled 还取决于有没有 key，这里只看模式是否抑制了 ASR）
            client.post("/api/settings", json={"mode": "full"})
            vc = client.get("/api/voice/config").json()
            check("全双工模式下 ASR 被抑制",
                  vc["mode"] == "full" and vc["asr"]["enabled"] is False, str(vc)[:160])
            client.post("/api/settings", json={"mode": "half"})
            vc = client.get("/api/voice/config").json()
            check("半双工模式下 ASR 恢复", vc["mode"] == "half" and vc["asr"]["provider"] != "",
                  str(vc)[:160])

            # ASR 关掉：麦克风不可用；打开：暴露 qwen3-asr-flash
            r = client.post("/api/settings", json={"asr": {"provider": "none"}})
            check("ASR 选 none 后语音输入标记为不可用",
                  client.get("/api/voice/config").json()["asr"]["enabled"] is False,
                  str(client.get("/api/voice/config").json()["asr"]))
            r = client.post("/api/settings", json={"asr": {"provider": "qwen"}})
            vc = client.get("/api/voice/config").json()
            check("ASR 打开后暴露识别模型",
                  vc["asr"]["model"] == "qwen3-asr-flash", str(vc["asr"]))
            r = client.post("/api/asr", content=b"")
            check("空录音被拒", r.status_code == 400, r.text[:80])

            # CLI 模型：选了就进命令行，没选就用 CLI 自己的默认
            from server.catalog import apply_model
            check("--model 插在子命令之后",
                  apply_model(["codex", "exec", "--skip-git-repo-check", "-"], ["-m"], "gpt-5")
                  == ["codex", "exec", "-m", "gpt-5", "--skip-git-repo-check", "-"], "")
            check("已有 --model 就替换而不是重复添加",
                  apply_model(["claude", "-p", "--model", "sonnet"], ["--model"], "opus")
                  == ["claude", "-p", "--model", "opus"], "")
            r = client.post("/api/settings", json={
                "agents": {"cli": {"claude": {"model": "opus"}}}})
            check("CLI 选模型写进配置", (r.json().get("changed") or {}).get(
                "agents.cli.claude.model") == "opus", str(r.json().get("changed")))

            # Codex 工具：同步执行并拿回结果（实时语音用它和 Codex 交互）
            # 用假后端，不依赖本机装的 CLI、也不花 token
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
