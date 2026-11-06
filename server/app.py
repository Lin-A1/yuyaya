"""鱼丫丫数字人服务。

    python -m server            # 默认 http://127.0.0.1:8848

前端通过 SSE (/api/events) 接收事件；任何 LLM / 脚本都可以通过 HTTP 控制她：

    POST /api/chat       {"text": "...", "brain": "deepseek"?, "wait": true}   让大脑回复并驱动数字人
    POST /api/say        {"text": "[happy]你好", "emotion"?, "interrupt"?}      直接说一段话（默认排队，不经过大脑）
    POST /api/emotion    {"name": "sad", "hold"?: 秒, "instant"?: false}        默认按情绪惯性逐步过渡
    POST /api/motion     {"name": "nod|shake|hop|jump|droop|tremble|tilt|wiggle|sniff|sigh"}  做一个动作
    POST /api/state      {"state": "idle|listening|thinking|speaking"}
    POST /api/mouth      {"level": 0.0~1.0}  或 {"levels": [...], "fps": 60}      口型（预留给 TTS 驱动）
    POST /api/memory    {"text": "..."}                                       让她记住一件事
    DELETE /api/memory  {"text"?: "..."}                                      忘掉一件事（不给 text 就全忘）
    POST /api/interrupt                                                       打断当前回复（预留给全双工插话）
    POST /api/reset                                                           清空对话历史
    GET  /api/brains     POST /api/brain {"name": "..."}                      查看 / 切换大脑
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import threading
import time

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import catalog, configure
from .brains import Brain, build_brain
from .io import (EventBus, InputDispatcher, InputError, Turn, TurnManager,
                 build_inputs, build_outputs, build_tts, make_input)
from .memory import Session
from .asr import ASRError as asr_error
from .asr import transcribe as asr_transcribe
from .protocol import (EMOTION_CN, EMOTIONS, MOTIONS, SpeechStream, motion_for_emotion,
                       normalize_emotion, normalize_motion)
from .realtime import RealtimeRuntime
from .settings import Settings
from .voice import VoicePipeline
from .voice import audio as vaudio

ROOT = pathlib.Path(__file__).resolve().parent.parent
log = logging.getLogger("yuyaya")


def config_path() -> pathlib.Path:
    path = pathlib.Path(os.environ.get("YUYAYA_CONFIG", ROOT / "config.json"))
    return path if path.exists() else ROOT / "config.example.json"


def load_config() -> dict:
    """读 config.json，并把老结构自动迁移成新结构（迁移结果会在保存设置时写回）。"""
    path = config_path()
    log.info("config: %s", path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    conf, migrated = configure.migrate(raw)
    if migrated:
        log.info("配置已从旧结构迁移到 v%d（下次保存设置时写回文件）", configure.SCHEMA_VERSION)
    return conf


def channel_keys(settings) -> dict:
    """{通道: key}，从 state/secrets.json 读（老的环境变量兜底）。"""
    return {ch: settings.key_for(ch) for ch in ("llm", "tts", "asr", "full")}


def runtime_conf(conf: dict, settings=None) -> dict:
    """把三层配置摊平成各模块直接可用的字典。"""
    return configure.resolve(conf, channel_keys(settings) if settings else None)


# 兼容旧代码：server.app.Hub 就是 EventBus
Hub = EventBus


class Avatar:
    """会话 + 当前回复任务。新输入会打断上一条（全双工插话的基础）。"""

    def __init__(self, bus: EventBus, conf: dict, turns: TurnManager | None = None) -> None:
        self.hub = bus
        self.conf = conf
        self.turns = turns or TurnManager(bus)
        self.brains: dict[str, Brain] = {n: build_brain(n, c) for n, c in conf["brains"].items()}
        self.active = conf.get("default") or next(iter(self.brains))
        self.max_turns = conf.get("history_turns", 12)
        # 会话记忆：短期上下文 + 长期记忆点，落盘保存（state/memory.json）
        state = pathlib.Path(os.environ.get("YUYAYA_STATE", ROOT / "state" / "memory.json"))
        self.session = Session(state if conf.get("persist_memory", True) else None, self.max_turns)
        log.info("记忆: %s（%d 条事实）", state, len(self.session.facts))

    def brain_info(self) -> dict:
        return {"active": self.active, "brains": [b.info() for b in self.brains.values()]}

    def chat_brain(self):
        """聊天永远用"聊天型"大脑（demo / API），不用命令行智能体。"""
        cur = self.brains.get(self.active)
        if cur is not None and cur.info()["role"] != "agent" and cur.availability()[0]:
            return cur
        fallback = None
        for b in self.brains.values():
            if b.info()["role"] == "agent" or not b.availability()[0]:
                continue
            if b.conf.get("type") == "demo":
                fallback = fallback or b
                continue
            return b
        return fallback or cur or next(iter(self.brains.values()))

    # ---- 任务控制 ----
    def interrupt(self, reason: str = "user", cancel_action: bool = True) -> None:
        """显式打断当前聊天回复。"""
        self.turns.interrupt("default", reason=reason, cancel_action=cancel_action)

    def _emit(self, turn: Turn, events: list[dict], ch: str = "main") -> None:
        if turn.stale:
            return
        # 模型经常只给情绪、不给动作。情绪变化时补一个配套的小动作，
        # 但同一句里模型自己写了动作就不补（尊重它的选择）。
        has_motion = any(e["op"] == "motion" for e in events)
        for i, e in enumerate(events):
            if e["op"] == "emotion":
                self.hub.set_emotion(e["name"])
                if not has_motion:
                    motion = motion_for_emotion(e["name"])
                    if motion:
                        self.hub.publish({"type": "speech", "id": turn.id, "turn": turn.id,
                                          "session": turn.session, "ch": ch,
                                          "op": "motion", "name": motion}, turn=turn)
            elif e["op"] == "memory":
                if self.session.add_fact(e["text"]):
                    self.session.save()
                    self.hub.publish({"type": "memory", "text": e["text"]})
                    log.info("记住了：%s", e["text"])
            elif e["op"] in ("task", "cancel", "answer"):
                continue                       # 已移除动作能力：标签只丢弃，不执行
            self.hub.publish({"type": "speech", "id": turn.id, "turn": turn.id,
                              "session": turn.session, "ch": ch, **e}, turn=turn)

    # ---- 对外能力 ----
    def say(self, text: str, emotion: str | None = None, interrupt: bool = False,
            session: str = "default", source: str = "say") -> dict:
        """默认排队：前一句说完再说这句；interrupt=True 时立即打断。"""
        if interrupt:
            turn = self.turns.begin(session, source, kind="say", barge_in=True, reason="say")
        else:
            turn = Turn(self.turns.new_id(), session, source, "say")
        sid = turn.id
        self.hub.publish({"type": "speech", "id": sid, "turn": sid, "session": session,
                          "ch": "main", "op": "begin", "source": "say",
                          "queue": not interrupt}, turn=turn)
        if emotion:
            self._emit(turn, [{"op": "emotion", "name": emotion}])
        sp = SpeechStream()
        self._emit(turn, sp.feed(text) + sp.flush())
        self.hub.publish({"type": "speech", "id": sid, "turn": sid, "session": session,
                          "ch": "main", "op": "end"}, turn=turn)
        self.turns.finish(turn)
        return {"id": sid, "text": sp.text}

    def chat(self, text: str, brain_name: str | None = None,
             session: str = "default", source: str = "http",
             turn: Turn | None = None) -> dict:
        brain = self.brains.get(brain_name) if brain_name else self.chat_brain()
        if brain is None:
            raise HTTPException(404, f"unknown brain {brain_name!r}")
        if brain.info()["role"] == "agent":
            raise HTTPException(400, f"{brain.label} 是命令行智能体，不用于聊天")
        brain_label = brain.name
        if turn is None:
            turn = self.turns.begin(session, source, kind="chat",
                                    barge_in=True, reason="new_input")
        sid, cancel = turn.id, turn.cancel
        pub = self.hub.publish
        # 聊天 runtime：共享短期对话、长期记忆和当前情绪；不向模型暴露工具。
        context = self.hub.mood_note() + self.session.context()
        prompt = text
        pub({"type": "user", "id": sid, "turn": sid, "session": session,
             "source": source, "text": text}, turn=turn)
        pub({"type": "speech", "id": sid, "turn": sid, "session": session,
             "ch": "main", "op": "begin", "source": brain_label}, turn=turn)
        pub({"type": "state", "state": "thinking"}, turn=turn)
        sp = SpeechStream()
        sp_events: list[dict] = []
        t0 = time.time()
        first = None
        error = None
        try:
            chunks = brain.stream(prompt, self.session.messages(), cancel, context)
            for chunk in chunks:
                if cancel.is_set():
                    break
                if first is None:
                    first = time.time() - t0
                    pub({"type": "stats", "first_token": round(first, 2),
                         "brain": brain_label}, turn=turn)
                evs = sp.feed(chunk)
                sp_events.extend(evs)
                self._emit(turn, evs)
            if not cancel.is_set():
                evs = sp.flush()
                sp_events.extend(evs)
                self._emit(turn, evs)
        except Exception as e:  # 大脑挂了也要让数字人有反应，而不是卡在"思考中"
            log.exception("brain %s failed", brain_label)
            error = str(e)
            self._emit(turn, [{"op": "emotion", "name": "sad"},
                              {"op": "text", "text": "呜……我连不上大脑了。"}])
            pub({"type": "error", "id": sid, "brain": brain_label,
                 "message": error[:500]}, turn=turn)
        finally:
            self.turns.finish(turn)
        interrupted = cancel.is_set()
        if not turn.stale:
            pub({"type": "speech", "id": sid, "turn": sid, "session": session,
                 "ch": "main", "op": "end", "interrupted": interrupted}, turn=turn)
        # 给新连接的快照可以回到 idle；已连接页面仍由 SpeechPlayer 播完字幕后自行退出说话状态
        if self.hub.snapshot["state"] == "thinking":
            self.hub.set_state("idle")
        pub({"type": "stats", "total": round(time.time() - t0, 2),
             "brain": brain_label}, turn=None)
        reply = sp.text
        if reply and error is None:
            # 记下这一轮；助手那侧带上当时的情绪，让大脑知道自己刚才什么状态
            emo = None
            for e in sp_events:
                if e.get("op") == "emotion":
                    emo = e["name"]
            self.session.add_turn("user", text)
            self.session.add_turn("assistant", reply, emo)
            self.session.save()
        return {"id": sid, "brain": brain_label, "reply": reply,
                "interrupted": interrupted, "error": error}

    def chat_or_agent(self, text: str, brain_name: str | None = None,
                      force_action: bool = False, session: str = "default",
                      source: str = "http") -> dict:
        """兼容旧调用；现在只有聊天 runtime，不再执行动作。"""
        return self.chat(text, brain_name, session=session, source=source)


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conf = load_config()
    # 页面设置：先把本地保存的 key 装进环境变量，再建大脑 / 实时语音，它们才看得到
    state_path = pathlib.Path(os.environ.get("YUYAYA_STATE", ROOT / "state" / "memory.json"))
    settings = Settings(conf, config_path(), state_path.parent)
    # conf 是三层结构（页面编辑的就是它）；rt 是摊平后各模块直接用的形状
    rt = configure.compat(configure.resolve(conf, channel_keys(settings)))
    bus = EventBus()
    hub = bus                      # 兼容旧的端点写法
    turns = TurnManager(bus)
    avatar = Avatar(bus, rt, turns)
    dispatcher = InputDispatcher(avatar, turns)

    def _remember(text: str) -> bool:
        ok = avatar.session.add_fact(text)
        if ok:
            avatar.session.save()
            bus.publish({"type": "memory", "text": text})
        return ok

    outputs = build_outputs(rt, bus)
    sse = outputs.get("sse")
    inputs = build_inputs(rt, dispatcher)
    tts = {"provider": build_tts(rt)}          # 可变：改设置后可以重建
    realtime = RealtimeRuntime(bus, avatar, rt.get("realtime") or {})
    voice_conf = rt.get("voice") or {}
    tts_conf = voice_conf.get("tts") or {}
    security_conf = rt.get("security") or {}
    app = FastAPI(title="鱼丫丫数字人")

    def tts_provider():
        return tts["provider"]

    def rebuild_tts() -> None:
        """改过 TTS 引擎/音色后重建 provider（就地更新，其它引用仍然有效）。"""
        tts["provider"] = build_tts(rt)

    def runtime_state() -> dict:
        """当前真正生效的运行状态（给设置页对照"选了"和"实际能用"）。"""
        return {"full": realtime.info()}

    def apply_config(changed_keys: dict) -> list[str]:
        """把刚保存的配置推到运行中的对象上，能不改就不重启。"""
        applied: list[str] = []
        new_rt = configure.compat(configure.resolve(conf, channel_keys(settings)))
        settings.conf = conf
        rt["mode"] = new_rt.get("mode")
        rt["llm"] = new_rt.get("_capabilities", {}).get("llm") or {}
        rt["asr"] = new_rt.get("_capabilities", {}).get("asr") or {}

        # 半双工 LLM：换大脑（重建 Avatar 太重，只换 active + 补齐新大脑）
        llm_name = new_rt.get("default")
        # 每次都按最新配置重建：改了同一接口的 Base URL / 模型 / Key 也要立刻生效
        # （大脑只是配置 + 请求逻辑，不持有会话状态，重建很轻）
        for name, spec in (new_rt.get("brains") or {}).items():
            try:
                avatar.brains[name] = build_brain(name, spec)
            except Exception as e:
                log.warning("重建大脑 %s 失败：%s", name, e)
        if llm_name in avatar.brains:
            avatar.active = llm_name
            applied.append(f"brain={llm_name}")

        # 朗读链
        rt["voice"] = new_rt["voice"]
        rt["realtime"] = new_rt["realtime"]
        # tts_conf 是创建时抓的引用，/api/tts 和语音状态都读它：就地更新，别让音色停在旧值
        tts_conf.clear()
        tts_conf.update((new_rt["voice"] or {}).get("tts") or {})
        rebuild_tts()
        app.state.tts_provider = tts["provider"]
        applied.append(f"tts={rt['voice']['tts']['provider']}/{rt['voice']['tts']['voice']}")

        # 全双工
        realtime.conf = new_rt["realtime"]
        realtime.enabled = bool(new_rt["realtime"].get("enabled"))
        realtime.model = new_rt["realtime"].get("model") or realtime.model
        realtime.voice = new_rt["realtime"].get("voice") or realtime.voice
        realtime.api_key_env = new_rt["realtime"].get("api_key_env") or realtime.api_key_env
        # 人设改了要重新拼 instructions（下一次连接就生效）
        realtime.base_instructions = realtime.build_instructions()
        applied.append(f"mode={'full' if realtime.enabled else 'half'}")

        # 改了 key：Claude 客户端缓存了旧 key，实时语音也要重读
        for b in avatar.brains.values():
            if hasattr(b, "_client"):
                b._client = None
        hub.publish({"type": "brains", **avatar.brain_info()})
        hub.publish({"type": "voice", **voice_status()})
        return applied

    # ---- 伪全双工语音流水线：VAD 切句 → ASR → 统一输入 → 说话器流式回传 ----
    def _asr_ready() -> tuple[dict, bool]:
        asr = configure.resolve(conf, channel_keys(settings))["asr"]
        ok = asr["enabled"] and bool(asr.get("api_key") or not asr["key_env"]
                                     or os.environ.get(asr["key_env"]))
        return asr, ok

    def pipeline_enabled() -> tuple[bool, str]:
        if rt.get("mode") == "full":
            return False, "当前是全双工模式，伪全双工流水线不工作（在设置里切到半双工）"
        raw = configure.resolve(conf, channel_keys(settings))
        if raw["asr"]["provider"] == "none":
            return False, "没有选语音识别：去「设置 → 对话与语音」选一个 ASR"
        asr = raw["asr"]
        if not (asr.get("api_key") or not asr["key_env"] or os.environ.get(asr["key_env"])):
            return False, "语音识别还没填 API Key"
        if not tts_provider().available():
            return False, "朗读不可用：去「设置 → 对话与语音」检查 TTS"
        return True, ""

    def pipeline_transcribe(pcm: bytes) -> str:
        asr = configure.resolve(conf, channel_keys(settings))["asr"]
        # 全双工开着时 resolve 会把 ASR 压成 enabled=False；流水线只在半双工下跑，这里不看它
        wav = vaudio.pcm_to_wav(pcm, 16000)
        return asr_transcribe(wav, asr["provider"], asr["model"], asr["key_env"],
                              asr.get("base_url", ""), 60, asr.get("api_key", ""))

    def pipeline_synth(text: str) -> bytes:
        p = tts_provider()
        cur = (rt.get("voice") or {}).get("tts") or {}
        wav = p.synth_wav(text, cur.get("voice") or "", int(cur.get("rate", 0)),
                          int(cur.get("volume", 100)))
        return vaudio.to_pcm(wav, getattr(p, "mime", "audio/wav"), 24000)

    pipeline = VoicePipeline(
        bus=bus, turns=turns, route=dispatcher.route, transcribe=pipeline_transcribe,
        synth=pipeline_synth, enabled=pipeline_enabled,
        conf=(rt.get("voice") or {}).get("pipeline") or conf.get("pipeline") or {},
        allow_remote=lambda: bool(security_conf.get("allow_remote_actions")))

    def require_local_control(request: Request) -> None:
        if security_conf.get("allow_remote_actions"):
            return
        host = request.client.host if request.client else ""
        if host and host not in ("127.0.0.1", "::1", "localhost", "testclient", "testserver"):
            raise HTTPException(403, "电脑操作接口默认只允许本机调用")

    @app.middleware("http")
    async def _no_cache_static(request, call_next):
        """改前端后普通刷新就能生效：不缓存页面和 js/css（仍然走 ETag 协商）。"""
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/js/") or p.startswith("/css/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.state.avatar = avatar
    app.state.bus = bus
    app.state.turns = turns
    app.state.dispatcher = dispatcher
    app.state.outputs = outputs
    app.state.inputs = inputs
    app.state.pipeline = pipeline
    app.state.tts = tts              # {"provider": ...}，改设置后就地替换
    app.state.settings = settings
    app.state.tts_provider = tts["provider"]
    app.state.realtime = realtime

    @app.on_event("startup")
    async def _startup() -> None:
        loop = asyncio.get_running_loop()
        bus.loop = loop
        if sse is not None:
            sse.attach_loop(loop)

    def hello() -> dict:
        bus.refresh()          # 强情绪超时后按平静恢复，别让新连接继承很久以前的害羞/惊讶
        return {"type": "hello", **bus.snapshot, **avatar.brain_info(),
                **avatar.session.stats(), "emotions": EMOTIONS, "motions": MOTIONS}

    @app.websocket("/ws/voice")
    async def voice_ws(ws: WebSocket):
        """语音：浏览器 PCM16(16k) <-> 服务端 <-> 音频回传(24k)。同一套协议，两种引擎：

        - 全双工模式：转给 Qwen-Audio-Realtime（真·音频级全双工）
        - 半双工模式：伪全双工流水线（VAD → ASR → 大脑 → TTS，可随时插话）
        """
        if rt.get("mode") == "full":
            await realtime.handle(ws)
        else:
            await pipeline.handle(ws)

    @app.get("/api/events")
    async def events():
        if sse is None:
            raise HTTPException(503, "SSE 输出适配器没有启用（config.io.outputs）")
        return sse.stream(hello)

    @app.post("/api/input")
    async def input_event(request: Request, body: dict = Body(...)):
        """统一输入入口：text / say / speech_start / speech_partial / speech_final / interrupt。"""
        try:
            ev = make_input(body)
        except InputError as e:
            raise HTTPException(400, str(e))
        adapter = next((a for a in inputs.values() if a.can_handle(ev)), None)
        handler = adapter.handle if adapter is not None else dispatcher.route
        try:
            return await asyncio.to_thread(handler, ev)
        except InputError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/chat")
    async def chat(request: Request, body: dict = Body(...)):
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        payload = {"type": "text", "mode": "chat", "text": text,
                   "brain": body.get("brain"),
                   "wait": body.get("wait", True), "source": "http"}
        return await asyncio.to_thread(dispatcher.route, payload)

    @app.post("/api/say")
    async def say(body: dict = Body(...)):
        return await asyncio.to_thread(dispatcher.route, {
            "type": "say", "text": body.get("text", ""),
            "emotion": normalize_emotion(body.get("emotion")),
            "interrupt": bool(body.get("interrupt", False)),
            "source": "http",
        })

    @app.post("/api/emotion")
    async def emotion(body: dict = Body(...)):
        name = normalize_emotion(body.get("name"))
        if not name:
            raise HTTPException(400, f"emotion must be one of {EMOTIONS}")
        hub.publish({"type": "emotion", "name": name, "hold": body.get("hold"), "instant": bool(body.get("instant"))})
        return {"emotion": name}

    @app.post("/api/motion")
    async def motion(body: dict = Body(...)):
        name = normalize_motion(body.get("name"))
        if not name:
            raise HTTPException(400, f"motion must be one of {MOTIONS}")
        hub.publish({"type": "motion", "name": name})
        return {"motion": name}

    @app.post("/api/state")
    async def state(body: dict = Body(...)):
        s = body.get("state")
        if s not in ("idle", "listening", "thinking", "speaking"):
            raise HTTPException(400, "state must be idle|listening|thinking|speaking")
        hub.publish({"type": "state", "state": s})
        return {"state": s}

    @app.post("/api/mouth")
    async def mouth(body: dict = Body(...)):
        hub.publish({"type": "mouth", **{k: body[k] for k in ("level", "levels", "fps") if k in body}})
        return {"ok": True}

    @app.post("/api/interrupt")
    async def interrupt(body: dict = Body(default={})):
        return await asyncio.to_thread(dispatcher.route, {
            "type": "interrupt", "reason": body.get("reason") or "user",
        })

    def voice_status() -> dict:
        """前端判断"现在用哪条链路"的依据。"""
        p = tts_provider()
        tts_info = p.info()
        asr = configure.resolve(conf, channel_keys(settings))["asr"]
        asr_ok = asr["enabled"] and bool(asr.get("api_key") or not asr["key_env"]
                                         or os.environ.get(asr["key_env"]))
        return {
            "tts": {
                "enabled": bool(tts_conf.get("enabled", True)) and p.available(),
                "provider": tts_info.get("provider", ""),
                "voice": tts_info.get("voice") or tts_conf.get("voice") or "",
                "rate": int(tts_conf.get("rate", 0)),
                "volume": int(tts_conf.get("volume", 100)),
            },
            "asr": {"provider": asr["provider"], "label": asr["label"], "model": asr["model"],
                    "enabled": asr_ok,
                    "reason": "" if asr_ok else
                    ("没有选语音识别" if not asr["enabled"]
                     else f"需要设置环境变量 {asr['key_env']}")},
            "realtime": realtime.info(),
            "pipeline": {"enabled": pipeline_enabled()[0], "reason": pipeline_enabled()[1],
                         **pipeline.client_conf()},
            "mode": configure.resolve(conf, channel_keys(settings))["mode"],
            "half": {"llm": (rt.get("llm") or {}).get("label") or "",
                     "model": (rt.get("llm") or {}).get("model") or ""},
        }

    @app.get("/api/voice/config")
    async def voice_config():
        return voice_status()

    @app.get("/api/settings")
    async def get_settings(refresh: int = 0):
        """设置页的全量数据：全双工、半双工三件套、内嵌 agent 状态、key 状态。

        ``?refresh=1`` 会清掉能力目录缓存，后续重新拉取模型列表。
        """
        if refresh:
            catalog.clear_cache()
        return settings.snapshot(runtime_state())

    @app.post("/api/settings")
    async def post_settings(request: Request, body: dict = Body(...)):
        require_local_control(request)      # 能写 key，只允许本机
        try:
            changed = settings.update(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        applied = apply_config(dict(body.get("keys") or {}))
        return {"ok": True, "changed": changed, "applied": applied,
                **settings.snapshot(runtime_state())}

    @app.post("/api/settings/models")
    async def settings_models(request: Request, body: dict = Body(...)):
        """拉取模型列表：用表单里还没保存的 Base URL / Key 直接问服务。"""
        require_local_control(request)      # 会带着 key 去请求外部地址，只允许本机
        try:
            return await asyncio.to_thread(settings.fetch_models, body.get("channel") or "llm", body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/tts")
    async def say_tts(body: dict = Body(...)):
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        if len(text) > 600:
            text = text[:600]
        # 设置面板的“试听”可以带 provider：不保存也能试听另一个引擎的音色
        requested = (body.get("provider") or "").strip()
        p = tts_provider()
        if requested and requested != p.type:
            sel = dict(((conf.get("voice") or {}).get("half") or {}).get("tts") or {})
            sel.pop("vendor", None)
            sel["type"] = requested
            sel["provider"] = requested
            try:
                p = build_tts({"voice": {"tts": sel}})
            except ValueError as e:
                raise HTTPException(400, str(e))
        try:
            wav = await asyncio.to_thread(
                p.synth_wav, text,
                body.get("voice") or tts_conf.get("voice") or "",
                int(body.get("rate", tts_conf.get("rate", 0))),
                int(body.get("volume", tts_conf.get("volume", 100))),
            )
        except Exception as e:
            raise HTTPException(503, f"TTS 不可用：{e}")
        return Response(content=wav, media_type=getattr(p, "mime", "audio/wav"),
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/memory")
    async def memory():
        """看她记得什么。"""
        return {"facts": [f["text"] for f in avatar.session.facts],
                "turns": len(avatar.session.turns)}

    @app.post("/api/memory")
    async def add_memory(body: dict = Body(...)):
        """替她记住一件事（大脑也可以用 [记住:xxx] 标签自己记）。"""
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        ok = avatar.session.add_fact(text)
        avatar.session.save()
        if ok:
            avatar.hub.publish({"type": "memory", "text": text})
        return {"ok": ok, "facts": len(avatar.session.facts)}

    @app.delete("/api/memory")
    async def forget_memory(body: dict = Body(default={})):
        """忘掉某件事（给 text 就只忘掉那件），不给就全忘。"""
        n = avatar.session.forget(body.get("text"))
        avatar.session.save()
        return {"forgotten": n, "facts": len(avatar.session.facts)}

    @app.post("/api/reset")
    async def reset():
        avatar.session.clear_turns()
        avatar.session.save()
        return {"ok": True}

    @app.get("/api/brains")
    async def brains():
        return avatar.brain_info()

    @app.post("/api/brain")
    async def set_brain(body: dict = Body(...)):
        name = body.get("name")
        if name not in avatar.brains:
            raise HTTPException(404, f"unknown brain {name!r}")
        ok, why = avatar.brains[name].availability()
        if not ok:
            raise HTTPException(400, f"大脑 {name} 现在不可用：{why}")
        if avatar.brains[name].info()["role"] == "agent":
            raise HTTPException(400, f"{name} 是命令行智能体，不参与聊天")
        avatar.active = name
        hub.publish({"type": "brain", **avatar.brain_info()})
        return avatar.brain_info()

    @app.post("/api/asr")
    async def speech_to_text(request: Request):
        """半双工的语音输入：前端录一段音频，这里转成文字。

        前端把录音整段传过来（webm/ogg/wav/mp3 都认），识别结果当成一次
        ``speech_final`` 交给同一个统一入口，和浏览器自带识别的路径完全一致。
        """
        require_local_control(request)
        asr = configure.resolve(conf, channel_keys(settings))["asr"]
        if not asr["enabled"]:
            raise HTTPException(400, "没有启用语音识别")
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "没收到音频")
        if len(audio) > 8 * 1024 * 1024:
            raise HTTPException(413, "录音太长了")
        try:
            text = await asyncio.to_thread(
                asr_transcribe, audio, asr["provider"], asr["model"], asr["key_env"],
                asr.get("base_url", ""), 60, asr.get("api_key", ""))
        except asr_error as e:
            raise HTTPException(503, str(e))
        text = (text or "").strip()
        if text:
            dispatcher.route({"type": "speech_final", "text": text, "source": "asr"})
        return {"text": text}

    web = ROOT / "web"

    @app.get("/")
    async def index():
        return FileResponse(web / "index.html")

    # 本地音色试听（state/ 不入库；没有这个目录就不挂载）
    samples = ROOT / "state" / "voice-samples"
    if samples.is_dir():
        app.mount("/voice-samples", StaticFiles(directory=samples), name="voice-samples")

    app.mount("/", StaticFiles(directory=web), name="web")
    return app
