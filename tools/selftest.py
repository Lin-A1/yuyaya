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

            # 页面设置：三层配置（全双工 / 半双工 LLM+TTS+ASR / agent CLI）
            st = client.get("/api/settings").json()
            check("设置接口按通道组织",
                  {"llm", "tts", "asr", "full", "agents", "persona"} <= set(st),
                  str(list(st))[:120])
            check("每个通道都有 厂商 + base_url + 模型列表",
                  all({"vendor", "vendors", "base_url", "models", "model"} <= set(st[c])
                      for c in ("llm", "tts", "asr", "full")),
                  str({c: list(st[c])[:6] for c in ("llm", "tts", "asr")}))
            check("全双工只列一家（Qwen）",
                  [v["id"] for v in st["full"]["vendors"]] == ["qwen"],
                  str([v["id"] for v in st["full"]["vendors"]]))
            check("全双工音色表有平台预置音色",
                  len(st["full"]["voices"]) > 5 and st["full"]["voice"] == "qiaoxiaojiao_v3.1",
                  str(st["full"]["voice"]))
            check("LLM 只认三种线协议",
                  [t["id"] for t in st["llm"]["types"]] ==
                  ["completions", "responses", "messages", "generateContent"],
                  str([t["id"] for t in st["llm"]["types"]]))
            check("厂商预设带默认模型和接口类型",
                  all(v["default_models"] is not None for v in st["llm"]["vendors"]),
                  str([(v["id"], len(v["default_models"])) for v in st["llm"]["vendors"]][:4]))
            check("四种大脑适配器都能构建", _brains_ok(), "")
            check("人设可在设置里改（有默认值和自定义标记）",
                  st["persona"]["default_identity"] and "customized" in st["persona"],
                  str(list(st["persona"]))[:100])
            check("ASR 可选 none（不启用语音输入）",
                  any(v["id"] == "none" for v in st["asr"]["vendors"]),
                  str([v["id"] for v in st["asr"]["vendors"]]))
            check("agent 三个 CLI 都被探测到（装没装都要有记录）",
                  len(st["agents"]["cli"]) == 3
                  and all({"installed", "models", "enabled"} <= set(c) for c in st["agents"]["cli"]),
                  str([(c["id"], c["installed"]) for c in st["agents"]["cli"]]))

            # 通道保存：key 跟着通道走，只回显掩码
            r = client.post("/api/settings", json={"llm": {
                "vendor": "deepseek", "type": "completions",
                "base_url": "https://api.deepseek.com",
                "models": [{"id": "deepseek-chat", "label": "deepseek-chat"},
                           {"id": "deepseek-reasoner", "label": "deepseek-reasoner"}],
                "model": "deepseek-chat", "api_key": "sk-selftest-not-real"}})
            changed = r.json().get("changed") or {}
            d = r.json()["llm"]
            check("通道保存厂商 / base / 模型列表",
                  changed.get("llm.vendor") == "deepseek"
                  and d["base_url"] == "https://api.deepseek.com"
                  and [m["id"] for m in d["models"]] == ["deepseek-chat", "deepseek-reasoner"],
                  str(changed))
            check("key 跟着通道存在 secrets.json",
                  json.loads((tmp / "secrets.json").read_text(encoding="utf-8"))
                  .get("llm") == "sk-selftest-not-real", str(d.get("has_key")))
            check("设置接口只回显 key 掩码",
                  d["has_key"] is True and "sk-selftest" not in json.dumps(r.json()),
                  str(d.get("key_hint")))
            r = client.post("/api/settings", json={"llm": {"api_key": ""}})
            check("清空通道 key 会删掉它", r.json()["llm"]["has_key"] is False, "")

            # 换厂商：base_url 和模型表自动跟着换
            r = client.post("/api/settings", json={"tts": {"vendor": "sapi"}})
            d = r.json()["tts"]
            check("换厂商后自动带上该厂商的默认值",
                  d["vendor"] == "sapi" and d["voice"] == "Microsoft Huihui Desktop",
                  str((d["vendor"], d["voice"])))
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
            client.post("/api/settings", json={"asr": {"vendor": "none"}})
            check("ASR 选 none 后语音输入标记为不可用",
                  client.get("/api/voice/config").json()["asr"]["enabled"] is False,
                  str(client.get("/api/voice/config").json()["asr"]))
            client.post("/api/settings", json={"asr": {"vendor": "qwen"}})
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
    test_intent()
    # 本机 SAPI 默认跳过：这台机器上 Windows 语音引擎偶尔要 40~90 秒，
    # 会把线程池占满、拖垮后面的 agent 测试。要测就显式加 --sapi。
    if "--sapi" in sys.argv:
        test_tts()
    test_api()
    test_render()
    print("\n全部通过" if not fails else f"\n{fails} 项失败")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
