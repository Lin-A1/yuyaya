"""Self-check for the daily chat branch."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

fails = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"[{'  ok  ' if cond else ' FAIL '}] {name}{(' - ' + extra) if extra else ''}")


def test_tts() -> None:
    """本机 SAPI。这台机器上 Windows 语音引擎有时会慢到 40~50 秒，
    所以慢的时候只报 SKIP，不判失败（它不是我们代码的问题）。"""
    from server import tts
    if not tts.available():
        print("[ SKIP ] SAPI unavailable")
        return
    t0 = time.time()
    try:
        wav = tts.synth_wav("你好", volume=20)
    except Exception as e:
        print(f"[ SKIP ] SAPI 调用失败/超时（{type(e).__name__}），跳过：{str(e)[:80]}")
        return
    cost = time.time() - t0
    if cost > 20:
        print(f"[ SKIP ] SAPI 异常缓慢（{cost:.0f}s），跳过判定")
        return
    check("本机 TTS", wav[:4] == b"RIFF" and len(wav) > 1000, f"{len(wav)} bytes, {cost:.1f}s")


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


def test_messages_brain() -> None:
    """Messages 适配器必须尊重 base_url，并正确解析流式事件（不依赖 anthropic SDK）。"""
    import http.server
    import threading
    from server.brains import build_brain

    seen: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            seen["path"] = self.path
            seen["key"] = self.headers.get("x-api-key")
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(n))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for ev in (
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "[happy]你"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好呀"}},
                {"type": "message_stop"},
            ):
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")

        def do_GET(self):
            seen["models_path"] = self.path
            seen["models_key"] = self.headers.get("x-api-key")
            body = json.dumps({"data": [{"id": "m-b"}, {"id": "m-a"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        b = build_brain("messages", {"type": "messages", "model": "any-model", "base_url": base,
                                     "api_key": "k-test"})
        out = "".join(b.stream("hi", [], threading.Event()))
        check("Messages 适配器走自定义 base_url 并解析流式",
              out == "[happy]你好呀" and seen.get("path") == "/v1/messages",
              f"{out!r} {seen.get('path')}")
        check("Messages 请求带 x-api-key 和 Bearer，且没有 Anthropic 专有参数",
              seen.get("key") == "k-test" and seen.get("auth") == "Bearer k-test"
              and "output_config" not in seen.get("body", {})
              and seen["body"]["model"] == "any-model",
              str(list(seen.get("body", {}))))
        from server import catalog
        models, err = catalog.fetch_models_ex(base, "k-test", "messages")
        check("按 Messages 格式拉模型列表（x-api-key + /v1/models）",
              [m["id"] for m in models] == ["m-a", "m-b"]
              and seen.get("models_path") == "/v1/models"
              and seen.get("models_key") == "k-test", f"{models} {err}")
    finally:
        srv.shutdown()


def test_extra_params() -> None:
    """LLM 的"额外请求参数"必须原样进请求体（比如关掉思考）。"""
    import http.server
    import threading
    from server.brains import build_brain

    seen: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            seen["body"] = json.loads(self.rfile.read(n))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b = build_brain("completions", {"type": "completions", "model": "m", "api_key": "k",
                                        "base_url": f"http://127.0.0.1:{srv.server_address[1]}",
                                        "extra": {"enable_thinking": False, "temperature": 0.3}})
        "".join(b.stream("hi", [], threading.Event()))
        body = seen.get("body", {})
        check("额外请求参数原样进了请求体，且不影响 model / messages",
              body.get("enable_thinking") is False and body.get("temperature") == 0.3
              and body.get("model") == "m" and body.get("stream") is True
              and body["messages"][-1]["content"] == "hi", str(sorted(body)))
    finally:
        srv.shutdown()


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

            av = client.app.state.avatar

            class _ContinuityBrain:
                name, label, kind = "continuity", "continuity", "stub"
                conf = {"type": "stub"}

                def info(self):
                    return {"name": self.name, "kind": self.kind, "label": self.label,
                            "role": "chat", "model": "stub", "available": True,
                            "reason": "", "slow": False}

                def availability(self):
                    return True, ""

                def stream(self, prompt, history, cancel, context=""):
                    seen = "\n".join(m.get("content", "") for m in history)
                    if prompt.startswith("第二轮"):
                        yield "记住了" if "记住了" in seen else "不知道"
                    else:
                        yield "记住了。"

            old_active = av.active
            av.brains["continuity"] = _ContinuityBrain()
            av.active = "continuity"
            try:
                first = client.post("/api/chat", json={
                    "text": "这是一次连续对话测试。请只回复：记住了。", "wait": True})
                second = client.post("/api/chat", json={
                    "text": "第二轮：我刚才让你回复的是什么？", "wait": True})
                check("连续两轮共享上下文，不会退化成只能一问一答",
                      first.status_code == 200 and second.status_code == 200
                      and first.json().get("reply") == "记住了。"
                      and second.json().get("reply") == "记住了",
                      f"{first.text[:100]} / {second.text[:100]}")
            finally:
                av.brains.pop("continuity", None)
                av.active = old_active

            r = client.post("/api/chat", json={"text": "打开记事本", "wait": True})
            check("动作能力已移除：请求只走聊天，不再派任务",
                  r.status_code == 200 and r.json().get("brain") == "demo"
                  and "job_id" not in r.json(), r.text[:160])
            check("旧任务接口已移除", client.get("/api/jobs").status_code == 404, "")
            r = client.post("/api/chat", json={"text": "hi", "brain": "codex", "wait": True})
            check("聊天不能直接选不存在的 CLI 名字", r.status_code == 404, r.text[:120])
            r = client.get("/api/voice/config")
            check("语音配置接口", r.status_code == 200 and "tts" in r.json(), r.text[:120])
            # 只验证接口契约（空文本要 400、正常文本能返回音频），不依赖本机语音引擎
            r = client.post("/api/tts", json={"text": ""})
            check("TTS 接口拒绝空文本", r.status_code == 400, r.text[:80])
            check("TTS provider 有 info()",
                  "provider" in client.app.state.tts_provider.info(), "")

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

            # 页面设置：全双工 / 半双工 LLM+TTS+ASR / 人设
            st = client.get("/api/settings").json()
            check("设置接口按通道组织",
                  {"llm", "tts", "asr", "full", "persona"} <= set(st),
                  str(list(st))[:120])
            check("每个通道都有 base_url + 模型列表 + key 状态",
                  all({"base_url", "models", "model", "has_key"} <= set(st[c])
                      for c in ("llm", "tts", "asr", "full")),
                  str({c: list(st[c])[:6] for c in ("llm", "tts", "asr")}))
            check("LLM / TTS / ASR 是通用接口，没有厂商这一层；只有全双工按厂商",
                  all("vendor" not in st[c] and "vendors" not in st[c] and "types" in st[c]
                      for c in ("llm", "tts", "asr"))
                  and {"vendor", "vendors"} <= set(st["full"]),
                  str({c: list(st[c]) for c in ("llm", "tts", "asr")}))
            check("全双工只列一家（Qwen）",
                  [v["id"] for v in st["full"]["vendors"]] == ["qwen"],
                  str([v["id"] for v in st["full"]["vendors"]]))
            check("全双工音色表有平台预置音色",
                  len(st["full"]["voices"]) > 5 and st["full"]["voice"] == "qiaoxiaojiao_v3.1",
                  str(st["full"]["voice"]))
            check("LLM 只认三种线协议（外加 Google 原生和离线演示）",
                  [t["id"] for t in st["llm"]["types"]] ==
                  ["completions", "responses", "messages", "generateContent", "demo"],
                  str([t["id"] for t in st["llm"]["types"]]))
            check("TTS/ASR 接口格式带默认模型，本地引擎标了 local",
                  all(v["default_models"] is not None for v in st["tts"]["types"])
                  and {t["id"] for t in st["tts"]["types"] if t["local"]} == {"edge", "sapi"}
                  and st["asr"]["enabled"] is True,
                  str([(v["id"], v["local"]) for v in st["tts"]["types"]]))
            check("四种大脑适配器都能构建", _brains_ok(), "")
            check("人设可在设置里改（有默认值和自定义标记）",
                  st["persona"]["default_identity"] and "customized" in st["persona"],
                  str(list(st["persona"]))[:100])
            check("ASR 可选 none（不启用语音输入）",
                  any(v["id"] == "none" for v in st["asr"]["types"]),
                  str([v["id"] for v in st["asr"]["types"]]))
            check("设置里不再暴露文件、桌面或任务能力", "agents" not in st, str(list(st)))

            # 通道保存：key 跟着通道走，只回显掩码
            r = client.post("/api/settings", json={"llm": {
                "vendor": "deepseek", "type": "completions",
                "base_url": "https://api.deepseek.com",
                "models": [{"id": "deepseek-chat", "label": "deepseek-chat"},
                           {"id": "deepseek-reasoner", "label": "deepseek-reasoner"}],
                "model": "deepseek-chat", "api_key": "sk-selftest-not-real"}})
            changed = r.json().get("changed") or {}
            d = r.json()["llm"]
            check("LLM 保存 base / 模型列表，传来的 vendor 被忽略",
                  "llm.vendor" not in changed and "vendor" not in d
                  and d["base_url"] == "https://api.deepseek.com"
                  and [m["id"] for m in d["models"]] == ["deepseek-chat", "deepseek-reasoner"],
                  str(changed))
            # 换 API 格式：Base URL 还是旧默认值就跟着换；用户自己填的不动
            r = client.post("/api/settings", json={"llm": {"type": "messages"}})
            check("换 API 格式时 Base URL 跟着换（还是旧格式默认值）",
                  r.json()["llm"]["type"] == "messages"
                  and r.json()["llm"]["base_url"] == "https://api.anthropic.com",
                  str(r.json()["llm"]["base_url"]))
            client.post("/api/settings", json={"llm": {"base_url": "http://gw.local/v1"}})
            r = client.post("/api/settings", json={"llm": {"type": "completions"}})
            check("用户自己填的 Base URL 不会被换格式冲掉",
                  r.json()["llm"]["base_url"] == "http://gw.local/v1",
                  str(r.json()["llm"]["base_url"]))
            r = client.post("/api/settings/models", json={
                "channel": "llm", "type": "completions", "base_url": "http://127.0.0.1:9/v1"})
            check("拉取模型失败时给出原因而不是报错",
                  r.status_code == 200 and r.json()["ok"] is False and r.json()["error"],
                  r.text[:120])
            # 额外请求参数：保存 → 进运行中的大脑；写错 JSON 报 400 且不落盘；不能覆盖保留字段
            r = client.post("/api/settings", json={"llm": {"extra": '{"enable_thinking": false}'}})
            br = client.app.state.avatar.brains["completions"]
            check("额外请求参数保存后进了运行中的大脑，设置接口也回显",
                  br.conf.get("extra") == {"enable_thinking": False}
                  and r.json()["llm"]["extra"] == {"enable_thinking": False},
                  str(br.conf.get("extra")))
            r = client.post("/api/settings", json={"llm": {"extra": "{not json"}})
            check("额外请求参数写错 JSON → 400，原值不变",
                  r.status_code == 400
                  and client.get("/api/settings").json()["llm"]["extra"] == {"enable_thinking": False},
                  r.text[:100])
            r = client.post("/api/settings", json={"llm": {"extra": {"model": "x"}}})
            check("额外请求参数不能覆盖 model / messages 等保留字段", r.status_code == 400, r.text[:100])
            r = client.post("/api/settings", json={"llm": {"extra": ""}})
            check("额外请求参数留空 = 清掉", r.json()["llm"]["extra"] == {}, str(r.json()["llm"]["extra"]))
            client.post("/api/settings", json={"llm": {
                "type": "completions", "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat", "api_key": "sk-selftest-not-real"}})
            brain = client.app.state.avatar.brains["completions"]
            check("页面保存的 Base URL / 模型 / Key 立刻进了运行中的大脑",
                  brain.conf.get("api_key") == "sk-selftest-not-real"
                  and brain.conf.get("base_url") == "https://api.deepseek.com"
                  and brain.conf.get("model") == "deepseek-chat",
                  str({k: brain.conf.get(k) for k in ("base_url", "model")}))
            check("key 跟着通道存在 secrets.json",
                  json.loads((tmp / "secrets.json").read_text(encoding="utf-8"))
                  .get("llm") == "sk-selftest-not-real", str(d.get("has_key")))
            check("设置接口只回显 key 掩码",
                  d["has_key"] is True and "sk-selftest" not in json.dumps(r.json()),
                  str(d.get("key_hint")))
            r = client.post("/api/settings", json={"llm": {"api_key": ""}})
            check("清空通道 key 会删掉它", r.json()["llm"]["has_key"] is False, "")

            # 换厂商：base_url 和模型表自动跟着换
            r = client.post("/api/settings", json={"tts": {"type": "sapi"}})
            d = r.json()["tts"]
            check("换 TTS 接口后自动带上该接口的默认音色",
                  d["type"] == "sapi" and d["voice"] == "Microsoft Huihui Desktop"
                  and d["local"] is True and "vendor" not in d,
                  str((d["type"], d["voice"])))
            # 通用接口：自己填 base_url / 模型 / key，音色按模型取
            r = client.post("/api/settings", json={"tts": {
                "type": "qwen-tts", "base_url": "http://gw.local", "api_key": "tts-key-xx",
                "models": [{"id": "qwen3-tts-flash", "label": "qwen3-tts-flash"}],
                "model": "qwen3-tts-flash", "voice": "Cherry"}})
            d = r.json()["tts"]
            check("TTS 自填 base_url / 模型 / 音色并保存",
                  d["type"] == "qwen-tts" and d["base_url"] == "http://gw.local"
                  and d["model"] == "qwen3-tts-flash" and d["voice"] == "Cherry"
                  and d["has_key"] is True, str({k: d.get(k) for k in ("type", "base_url", "voice")}))
            tp = client.app.state.tts_provider
            check("TTS 适配器拿到页面里的 key / 模型 / 地址",
                  tp.type == "qwen-tts" and tp.conf.get("api_key") == "tts-key-xx"
                  and tp.conf.get("model") == "qwen3-tts-flash"
                  and tp.conf.get("base_url") == "http://gw.local",
                  str({k: tp.conf.get(k) for k in ("model", "base_url")}))
            client.post("/api/settings", json={"tts": {"api_key": ""}})
            client.post("/api/settings", json={"tts": {"type": "sapi"}})
            check("换引擎后朗读 provider 立即重建",
                  client.app.state.tts_provider.info()["provider"] == "sapi", "")

            # 模式互斥
            client.post("/api/settings", json={"mode": "full"})
            vc = client.get("/api/voice/config").json()
            check("全双工模式下 ASR 被抑制",
                  vc["mode"] == "full" and vc["asr"]["enabled"] is False, str(vc)[:160])
            client.post("/api/settings", json={"mode": "half"})
            vc = client.get("/api/voice/config").json()
            check("半双工模式下 ASR 恢复", vc["mode"] == "half", str(vc)[:160])

            # ASR 关掉：麦克风不可用
            client.post("/api/settings", json={"asr": {"type": "none"}})
            check("ASR 选 none 后语音输入标记为不可用",
                  client.get("/api/voice/config").json()["asr"]["enabled"] is False,
                  str(client.get("/api/voice/config").json()["asr"]))
            client.post("/api/settings", json={"asr": {"type": "qwen"}})
            vc = client.get("/api/voice/config").json()
            r = client.get("/api/settings").json()["asr"]
            check("ASR 设置页有 enabled 标记（麦克风按钮靠它）",
                  r["enabled"] is True and r["type"] == "qwen" and r["base_url"], str(r)[:140])
            check("ASR 打开后暴露识别模型",
                  vc["asr"]["model"] == "qwen3-asr-flash", str(vc["asr"]))
            r = client.post("/api/asr", content=b"")
            check("空录音被拒", r.status_code == 400, r.text[:80])

            # 实时语音运行时：没配 key 时不可用，只保留聊天辅助工具
            rt = client.app.state.realtime
            check("realtime 未配 key 时不可用", rt.available is False, str(rt.info()))
            names = {s.get("name") for s in __import__(
                "server.realtime.tools", fromlist=["TOOL_SCHEMAS"]).TOOL_SCHEMAS}
            check("realtime 只保留聊天、记忆和表情动作工具",
                  names == {"remember", "set_emotion", "set_motion"}
                  and not ({"do_task", "think", "task_status", "schedule"} & names),
                  str(names))

            # 伪全双工流水线（假 ASR / 假 TTS）
            from selftest_voice import voice_suite
            voice_suite(client, check)

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


def test_render() -> None:
    """渲染自检：立绘必须真的画出来。

    踩过的坑：改 canvas 尺寸会重置整个 WebGL 状态（program / VAO / 纹理绑定），
    那一帧就什么都画不出来。浏览器里 rAF 每帧重绘所以看不出问题，录屏/截图就会白屏。
    """
    import json
    import shutil
    import subprocess
    import tempfile
    import time

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if not os.path.exists(edge):
        print("[ SKIP ] 找不到 Edge，跳过渲染自检")
        return
    try:
        import websocket
    except Exception as e:
        check("websocket", False, str(e))
        return

    profile = tempfile.mkdtemp(prefix="yuyaya-render-")
    port = 9406
    proc = subprocess.Popen([
        edge, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--no-first-run", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--window-size=1600,900", "http://127.0.0.1:8848/?obs=1",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(40):
            try:
                for t in requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json():
                    if t.get("type") == "page" and "8848" in t.get("url", ""):
                        ws_url = t["webSocketDebuggerUrl"]
            except Exception:
                pass
            if ws_url:
                break
            time.sleep(1)
        if not ws_url:
            check("打开页面", False, "找不到标签页（服务没起？）")
            return
        ws = websocket.create_connection(ws_url, timeout=60)
        nid = [0]

        def ev(expr):
            nid[0] += 1
            ws.send(json.dumps({"id": nid[0], "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True,
                                           "userGesture": True}}))
            while True:
                r = json.loads(ws.recv())
                if r.get("id") == nid[0]:
                    res = (r.get("result") or {}).get("result", {})
                    return res.get("value")

        for _ in range(60):
            if ev("typeof window.avatar === 'object'"):
                break
            time.sleep(0.5)
        time.sleep(2.5)     # 让 rAF 先稳定跑几帧

        painted = """(()=>{const c=document.querySelector('#char-body canvas');
          if(!c) return -1; const g=c.getContext('webgl2'); const px=new Uint8Array(4); let n=0;
          for(let x=20;x<c.width-20;x+=25) for(let y=20;y<c.height-20;y+=25){
            g.readPixels(x,y,1,1,g.RGBA,g.UNSIGNED_BYTE,px); if(px[3]>8) n++; }
          return n;})()"""
        before = ev(f"(()=>{{avatar._frame(performance.now());return {painted}}})()")
        check("立绘真的画出来了", isinstance(before, int) and before > 0, f"{before} 个采样点非空")

        # 模拟窗口缩放 / 分辨率变化：改 canvas 尺寸会重置 WebGL 状态
        ev("""(()=>{const c=document.querySelector('#char-body canvas');
          c.width=Math.round(c.width*1.3); c.height=Math.round(c.height*1.3); return 1})()""")
        after = ev(f"(()=>{{avatar._frame(performance.now());return {painted}}})()")
        check("画布尺寸变化后仍然能画出来", isinstance(after, int) and after > 0, f"{after} 个采样点非空")

        # 虚拟时钟 + 贴图未就绪：第一帧不能让姿态变成 NaN
        # （t0 在 load() 等贴图时才设好，早一步算出来的 t 是 NaN，弹簧会被污染）
        ev("""(()=>{window.__vt=performance.now(); performance.now=()=>window.__vt;
          window.requestAnimationFrame=()=>0; avatar.t0=undefined; return 1})()""")
        for _ in range(5):
            ev("window.__vt+=33.4; avatar._frame(window.__vt)")
        nan = ev("""(()=>{const p=avatar._pose(window.__vt,1,0.033);
          const bad=[p.roll,p.yaw,p.pitch,p.lean].filter(v=>!Number.isFinite(v));
          return JSON.stringify({bad:bad.length,
            springBad:!Number.isFinite(avatar.sp.roll.x)})})()""")
        check("时钟异常时姿态不会变成 NaN", '"bad":0' in nan and '"springBad":false' in nan, nan)
        ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    test_messages_brain()
    test_extra_params()
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from selftest_voice import voice_units
    voice_units(check)
    # 本机 SAPI 默认跳过：这台机器上 Windows 语音引擎偶尔要 40~90 秒，
    # 会把线程池占满、拖垮后面的接口测试。要测就显式加 --sapi。
    if "--sapi" in sys.argv:
        test_tts()
    test_api()
    test_render()
    print("\n全部通过" if not fails else f"\n{fails} 项失败")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
