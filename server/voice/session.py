"""伪全双工语音流水线（ASR + LLM + TTS 拼出全双工的手感）。

    浏览器麦克风 PCM16 16k ──► VAD 切句 ──► ASR ──► 统一输入(speech_final) ──► 大脑
         ▲                                                                       │
         └────────── PCM16 24k ◄── 说话器(切句→合成→发送) ◄── 总线 speech 事件 ◄──┘

和真全双工（Qwen-Audio-Realtime）用**同一套 WebSocket 协议**，前端只有一个客户端类：

    浏览器 → 服务端   二进制：PCM16 16k 单声道；文本：{"type":"cancel"} / {"type":"text","text":…}
    服务端 → 浏览器   二进制：PCM16 24k；文本：ready / speech_started / vad / transcript /
                      caption / caption_clear / caption_end / flush / error / closed

手感上的几件事：

- **随时插话**：麦克风一直开着。她在说话（或在想）时，持续发声够久就算插话：取消大脑请求、
  清空合成队列、让浏览器立刻停音频；她自己的声音要更久、更响才会触发（见 vad.Endpointer）。
- **补问合并**：你停顿了一下又接着说，而她还没出声，就把两段并成一句再回答，不会答两遍。
- **回声**：浏览器回声消除 + 前端"放音时麦克风必须明显更响才上传"的闸门 + 这里把识别结果
  和她刚说过的话比对，三道关。
- **统一入口**：识别出的文字走和打字一样的 ``speech_final``，所以"打开记事本"这类会交给
  后台 agent，应答和任务完成后的主动发言也由同一个说话器念出来。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .speaker import Speaker, _VISIBLE
from .vad import Endpointer

log = logging.getLogger("yuyaya.voice")

LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient", "testserver")
_END_PUNCT = "。！？!?…，、；;,.~"


class VoicePipeline:
    """每个应用一份；每条浏览器连接是一个 ``_Session``。"""

    def __init__(self, *, bus, turns, route, transcribe, synth, enabled, conf=None,
                 allow_remote=lambda: False) -> None:
        self.bus = bus
        self.turns = turns
        self.route = route                  # 统一输入：dispatcher.route
        self.transcribe = transcribe        # (pcm16k) -> str，阻塞
        self.synth = synth                  # (text) -> pcm16 24k，阻塞
        self.enabled = enabled              # () -> (bool, 原因)
        self.conf = conf or {}
        self.allow_remote = allow_remote
        self.current: _Session | None = None

    def client_conf(self) -> dict:
        c = self.conf
        return {"input_sample_rate": 16000, "output_sample_rate": 24000,
                # 前端回声闸门：放音时麦克风响度要超过 max(echo_gate, 放音响度 * echo_ratio) 才上传
                "echo_gate": float(c.get("echo_gate", 0.03)),
                "echo_ratio": float(c.get("echo_ratio", 0.6))}

    async def handle(self, ws) -> None:
        await ws.accept()
        ok, why = self.enabled()
        if not ok:
            await ws.send_json({"type": "error", "error": why})
            await ws.close()
            return
        host = ws.client.host if ws.client else ""
        if host and host not in LOCAL_HOSTS and not self.allow_remote():
            await ws.send_json({"type": "error", "error": "语音输入默认只允许本机使用"})
            await ws.close()
            return
        if self.current is not None:
            await self.current.close("replaced")     # 新连接顶掉旧的，避免两个页面同时开口
        sess = _Session(self, ws)
        self.current = sess
        try:
            await sess.run()
        finally:
            if self.current is sess:
                self.current = None


class _Session:
    def __init__(self, pipe: VoicePipeline, ws) -> None:
        self.p = pipe
        self.ws = ws
        c = pipe.conf
        self.ep = Endpointer(
            onset_ms=int(c.get("onset_ms", 120)),
            onset_strict_ms=int(c.get("barge_in_ms", 320)),
            end_ms=int(c.get("end_ms", 650)),
            min_speech_ms=int(c.get("min_speech_ms", 250)))
        self.merge_window = float(c.get("merge_window", 3.0))
        self.min_chars = int(c.get("min_chars", 2))
        self.utterances: asyncio.Queue | None = None     # 在事件循环里（run）才建
        self.speaker: Speaker | None = None
        self.closed = False
        self.last_text = ""
        self.last_at = 0.0
        self.answered = True
        self._worker = None

    # ---------------------------------------------------------------- 发送
    async def send_json(self, obj: dict) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_json(obj)
        except Exception:
            self.closed = True

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_bytes(data)
        except Exception:
            self.closed = True

    async def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        await self.send_json({"type": "closed", "reason": reason})
        self.closed = True
        try:
            await self.ws.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- 主循环
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self.utterances = asyncio.Queue()
        self.speaker = Speaker(self.p.bus, loop, self.p.synth, self.send_json, self.send_bytes,
                               on_audio_start=self._audio_started)
        self.speaker.start()
        self._worker = asyncio.ensure_future(self._utterance_loop())
        await self.send_json({"type": "ready", "mode": "pipeline",
                              "model": "asr+llm+tts", **self.p.client_conf()})
        try:
            while not self.closed:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    await self._on_audio(data)
                    continue
                text = msg.get("text")
                if text:
                    await self._on_command(text)
        except Exception:
            log.debug("语音连接中断", exc_info=True)
        finally:
            self.closed = True
            self.speaker.stop()
            if self._worker:
                self._worker.cancel()

    # ---------------------------------------------------------------- 麦克风
    async def _on_audio(self, data: bytes) -> None:
        for ev in self.ep.feed(data, strict=self.speaker.busy):
            if ev[0] == "start":
                await self._on_speech_start()
            elif ev[0] == "end":
                self.utterances.put_nowait(ev[1])
                await self.send_json({"type": "vad", "state": "idle"})
            elif ev[0] == "drop":
                await self.send_json({"type": "vad", "state": "idle"})

    async def _on_speech_start(self) -> None:
        await self.send_json({"type": "vad", "state": "speech"})
        if self.speaker.busy or self.p.turns.current("default") is not None:
            # 她在说或在想：这就是插话。只停说话，不杀正在跑的桌面动作。
            self.p.turns.interrupt("default", reason="barge_in", cancel_action=False)
            self.speaker.reset()
            await self.send_json({"type": "speech_started"})

    def _audio_started(self) -> None:
        self.answered = True

    async def _on_command(self, raw: str) -> None:
        try:
            cmd = json.loads(raw)
        except ValueError:
            return
        kind = cmd.get("type")
        if kind == "cancel":
            self.p.turns.interrupt("default", reason="cancel", cancel_action=False)
            self.speaker.reset()
            await self.send_json({"type": "flush"})
        elif kind == "text" and cmd.get("text"):
            await asyncio.to_thread(self.p.route, {
                "type": "text", "text": str(cmd["text"]), "source": "duplex", "wait": False})
        elif kind == "ping":
            await self.send_json({"type": "pong"})

    # ---------------------------------------------------------------- 一句话说完
    async def _utterance_loop(self) -> None:
        while True:
            pcm = await self.utterances.get()
            try:
                await self._handle_utterance(pcm)
            except Exception as e:
                log.exception("处理语音失败")
                await self.send_json({"type": "error", "error": f"语音处理失败：{e}"[:200]})

    async def _handle_utterance(self, pcm: bytes) -> None:
        try:
            text = (await asyncio.to_thread(self.p.transcribe, pcm) or "").strip()
        except Exception as e:
            await self.send_json({"type": "error", "error": f"识别失败：{e}"[:200]})
            return
        if len(_VISIBLE.findall(text)) < self.min_chars:
            return                                         # 噪声 / 单个语气词
        if self.speaker.is_echo(text):
            log.info("丢弃回声：%s", text[:30])
            return
        text = self._merge(text)
        await self.send_json({"type": "transcript", "role": "user", "text": text})
        self.last_text, self.last_at, self.answered = text, time.monotonic(), False
        await asyncio.to_thread(self.p.route, {
            "type": "speech_final", "text": text, "source": "duplex", "wait": False})

    def _merge(self, text: str) -> str:
        """补问合并：上一句她还没出声就被你接着说的话打断了，两段并成一句。"""
        if (self.last_text and not self.answered
                and time.monotonic() - self.last_at < self.merge_window):
            joiner = "" if self.last_text[-1] in _END_PUNCT else "，"
            return self.last_text + joiner + text
        return text
