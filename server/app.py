"""大肥鱼数字人服务。

    python -m server            # 默认 http://127.0.0.1:8848

前端通过 SSE (/api/events) 接收事件；任何 LLM / agent / 脚本都可以通过 HTTP 控制她：

    POST /api/chat       {"text": "...", "brain": "deepseek"?, "wait": true}   让大脑回复并驱动数字人
    POST /api/say        {"text": "[happy]你好", "emotion"?, "interrupt"?}      直接说一段话（默认排队，不经过大脑）
    POST /api/emotion    {"name": "sad", "hold"?: 秒, "instant"?: false}        默认按情绪惯性逐步过渡
    POST /api/motion     {"name": "nod|shake|hop|jump|droop|tremble|tilt|wiggle|sniff|sigh"}  做一个动作
    POST /api/state      {"state": "idle|listening|thinking|speaking"}
    POST /api/mouth      {"level": 0.0~1.0}  或 {"levels": [...], "fps": 60}      口型（预留给 TTS 驱动）
    POST /api/interrupt                                                       打断当前回复（预留给全双工插话）
    POST /api/reset                                                           清空对话历史
    GET  /api/brains     POST /api/brain {"name": "..."}                      查看 / 切换大脑
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import pathlib
import threading
import time

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .brains import Brain, build_brain
from .protocol import EMOTION_CN, EMOTIONS, MOTIONS, SpeechStream, normalize_emotion, normalize_motion

ROOT = pathlib.Path(__file__).resolve().parent.parent
log = logging.getLogger("yuyaya")


def load_config() -> dict:
    path = pathlib.Path(os.environ.get("YUYAYA_CONFIG", ROOT / "config.json"))
    if not path.exists():
        path = ROOT / "config.example.json"
    log.info("config: %s", path)
    return json.loads(path.read_text(encoding="utf-8"))


class Hub:
    """事件总线：一个事件广播给所有已连接的前端（浏览器、OBS 浏览器源……）。"""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: set[asyncio.Queue] = set()
        self.snapshot = {"emotion": "neutral", "state": "idle"}
        self.emotion_since = time.time()

    def set_emotion(self, name: str) -> None:
        if name != self.snapshot["emotion"]:
            self.emotion_since = time.time()
        self.snapshot["emotion"] = name

    def mood_note(self) -> str:
        """告诉大脑她此刻的情绪，让回复从这个情绪自然过渡（前端的表情过渡也有惯性，两边对得上）。"""
        emo = self.snapshot["emotion"]
        if emo == "neutral":
            return ""
        secs = int(time.time() - self.emotion_since)
        ago = "刚刚才开始" if secs < 20 else f"已经持续了约 {secs // 60} 分钟" if secs >= 120 else f"已经持续了约 {secs} 秒"
        return f"（你此刻的情绪：{EMOTION_CN.get(emo, emo)}，{ago}。）\n"

    def publish(self, event: dict) -> None:
        """线程安全，可以在大脑的工作线程里直接调用。"""
        if event.get("type") == "emotion":
            # 临时表情只影响已连接的前端，快照始终保留它背后的持续情绪
            if not event.get("hold"):
                self.set_emotion(event["name"])
        elif event.get("type") == "state":
            self.snapshot["state"] = event["state"]
        elif event.get("type") == "interrupt":
            self.snapshot["state"] = "idle"
        if self.loop is None:
            return
        for q in list(self.subs):
            self.loop.call_soon_threadsafe(q.put_nowait, event)


class Avatar:
    """会话 + 当前回复任务。新的 chat/say 会打断上一条（全双工插话的基础）。"""

    def __init__(self, hub: Hub, conf: dict) -> None:
        self.hub = hub
        self.conf = conf
        self.brains: dict[str, Brain] = {n: build_brain(n, c) for n, c in conf["brains"].items()}
        self.active = conf.get("default") or next(iter(self.brains))
        self.history: list[dict] = []
        self.max_turns = conf.get("history_turns", 12)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.cancel: threading.Event | None = None

    def brain_info(self) -> dict:
        return {"active": self.active, "brains": [b.info() for b in self.brains.values()]}

    # ---- 任务控制 ----
    def interrupt(self) -> None:
        with self.lock:
            if self.cancel is not None:
                self.cancel.set()
                self.cancel = None
        self.hub.publish({"type": "interrupt"})

    def _begin(self, interrupt: bool = True) -> tuple[int, threading.Event]:
        if interrupt:
            self.interrupt()
        cancel = threading.Event()
        with self.lock:
            self.cancel = cancel
        sid = next(self.ids)
        return sid, cancel

    def _emit(self, sid: int, events: list[dict]) -> None:
        for e in events:
            if e["op"] == "emotion":
                self.hub.set_emotion(e["name"])
            self.hub.publish({"type": "speech", "id": sid, **e})

    # ---- 对外能力 ----
    def say(self, text: str, emotion: str | None = None, interrupt: bool = False) -> dict:
        """默认排队：前一句说完再说这句；interrupt=True 时立即打断。"""
        if interrupt:
            self.interrupt()
        sid = next(self.ids)
        self.hub.publish({"type": "speech", "id": sid, "op": "begin", "source": "say", "queue": not interrupt})
        if emotion:
            self._emit(sid, [{"op": "emotion", "name": emotion}])
        sp = SpeechStream()
        self._emit(sid, sp.feed(text) + sp.flush())
        self.hub.publish({"type": "speech", "id": sid, "op": "end"})
        return {"id": sid, "text": sp.text}

    def chat(self, text: str, brain_name: str | None = None) -> dict:
        brain = self.brains.get(brain_name or self.active)
        if brain is None:
            raise HTTPException(404, f"unknown brain {brain_name!r}")
        sid, cancel = self._begin()
        pub = self.hub.publish
        prompt = self.hub.mood_note() + text   # 只给大脑看，历史里仍存用户原话
        pub({"type": "user", "id": sid, "text": text})
        pub({"type": "speech", "id": sid, "op": "begin", "source": brain.name})
        pub({"type": "state", "state": "thinking"})
        sp = SpeechStream()
        t0 = time.time()
        first = None
        error = None
        try:
            for chunk in brain.stream(prompt, self.history[-2 * self.max_turns:], cancel):
                if cancel.is_set():
                    break
                if first is None:
                    first = time.time() - t0
                    pub({"type": "stats", "first_token": round(first, 2), "brain": brain.name})
                self._emit(sid, sp.feed(chunk))
            if not cancel.is_set():
                self._emit(sid, sp.flush())
        except Exception as e:  # 大脑挂了也要让数字人有反应，而不是卡在"思考中"
            log.exception("brain %s failed", brain.name)
            error = str(e)
            self._emit(sid, [{"op": "emotion", "name": "sad"},
                             {"op": "text", "text": "呜……我连不上大脑了。"}])
            pub({"type": "error", "id": sid, "brain": brain.name, "message": error[:500]})
        interrupted = cancel.is_set()
        pub({"type": "speech", "id": sid, "op": "end", "interrupted": interrupted})
        # 给新连接的快照可以回到 idle；已连接页面仍由 SpeechPlayer 播完字幕后自行退出说话状态
        if self.hub.snapshot["state"] == "thinking":
            self.hub.snapshot["state"] = "idle"
        pub({"type": "stats", "total": round(time.time() - t0, 2), "brain": brain.name})
        with self.lock:
            if self.cancel is cancel:
                self.cancel = None
        reply = sp.text
        if reply and error is None:
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            del self.history[:-2 * self.max_turns]
        return {"id": sid, "brain": brain.name, "reply": reply, "interrupted": interrupted, "error": error}


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conf = load_config()
    hub = Hub()
    avatar = Avatar(hub, conf)
    app = FastAPI(title="大肥鱼数字人")
    app.state.avatar = avatar

    @app.on_event("startup")
    async def _startup() -> None:
        hub.loop = asyncio.get_running_loop()

    @app.get("/api/events")
    async def events():
        q: asyncio.Queue = asyncio.Queue()
        hub.subs.add(q)

        async def gen():
            try:
                hello = {"type": "hello", **hub.snapshot, **avatar.brain_info(), "emotions": EMOTIONS, "motions": MOTIONS}
                yield f"data: {json.dumps(hello, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                hub.subs.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat")
    async def chat(body: dict = Body(...)):
        text = (body.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        if body.get("wait", True):
            return await asyncio.to_thread(avatar.chat, text, body.get("brain"))
        threading.Thread(target=avatar.chat, args=(text, body.get("brain")), daemon=True).start()
        return {"ok": True}

    @app.post("/api/say")
    async def say(body: dict = Body(...)):
        return avatar.say(body.get("text", ""), normalize_emotion(body.get("emotion")),
                          bool(body.get("interrupt", False)))

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
    async def interrupt():
        avatar.interrupt()
        return {"ok": True}

    @app.post("/api/reset")
    async def reset():
        avatar.history.clear()
        return {"ok": True}

    @app.get("/api/brains")
    async def brains():
        return avatar.brain_info()

    @app.post("/api/brain")
    async def set_brain(body: dict = Body(...)):
        name = body.get("name")
        if name not in avatar.brains:
            raise HTTPException(404, f"unknown brain {name!r}")
        avatar.active = name
        hub.publish({"type": "brain", **avatar.brain_info()})
        return avatar.brain_info()

    web = ROOT / "web"

    @app.get("/")
    async def index():
        return FileResponse(web / "index.html")

    app.mount("/", StaticFiles(directory=web), name="web")
    return app
