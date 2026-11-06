"""说话器：把总线上的 speech 事件变成音频流发给浏览器。

订阅的是**总线**，不是某个具体入口，所以所有"她要说的话"走同一条路：
聊天回复、干活时的应答（"好，我去弄一下"）、任务做完后的主动发言，都会被念出来。

流水线：

    speech 事件 ─► 切句 ─► text_q ─► [合成线程] ─► audio_q ─► [发送] ─► 浏览器

- 一句话刚出来就合成，不等整段回复；合成和发送是两个协程，下一句在合成时上一句已经在放。
- 每个队列项都带 ``gen``（代数）。打断 = ``gen += 1``，旧代的东西不管在哪个阶段，
  到了下一个检查点就被丢掉，所以不会有"打断了还冒出一句旧台词"。
- ``caption`` 消息先于该句音频发出，浏览器按音频真正开始播放的时间点显示，字幕和声音对得上。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque

from .audio import chunk_pcm

log = logging.getLogger("yuyaya.voice")

_END = "。！？!?…\n"
_SOFT = "，、；;：:,"
_CLOSERS = "”’\"')）】》」』"
_VISIBLE = re.compile(r"[\w一-鿿]")


def split_sentences(text: str, soft_max: int = 42, min_len: int = 3) -> list[str]:
    """切成适合逐句合成的小段。句末标点切；太长的在逗号处再切；碎片并入下一段。"""
    out: list[str] = []
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        i += 1
        cut = False
        if ch in _END:
            while i < len(text) and (text[i] in _END or text[i] in _CLOSERS):
                buf += text[i]
                i += 1
            cut = True
        elif ch in _SOFT and len(buf) >= soft_max:
            cut = True
        if cut:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    merged: list[str] = []
    carry = ""
    for piece in out:
        piece = carry + piece
        carry = ""
        if len(_VISIBLE.findall(piece)) < min_len:
            carry = piece                     # 太碎（"嗯。"）：并进下一句
            continue
        merged.append(piece.strip())
    if carry.strip():
        if merged:
            merged[-1] += carry.strip()
        elif _VISIBLE.search(carry):
            merged.append(carry.strip())
    return [m for m in merged if _VISIBLE.search(m)]


def _norm(text: str) -> str:
    return "".join(_VISIBLE.findall(text or "")).lower()


class Speaker:
    def __init__(self, bus, loop, synth, send_json, send_bytes, *, rate: int = 24000,
                 soft_max: int = 42, on_audio_start=None) -> None:
        self.bus = bus
        self.loop = loop
        self.synth = synth                  # (text) -> PCM16 @ rate，阻塞
        self.send_json = send_json          # async
        self.send_bytes = send_bytes        # async
        self.rate = rate
        self.soft_max = soft_max
        self.on_audio_start = on_audio_start
        self.gen = 0
        self.pending = 0                    # 当前代里还没处理完的项
        self.playing_until = 0.0            # 估计浏览器放完的时刻（monotonic）
        self.accepted: set = set()
        self.recent: deque = deque(maxlen=12)   # (时刻, 文本)：认回声用
        self.started_audio = False
        self.text_q: asyncio.Queue = asyncio.Queue()
        self.audio_q: asyncio.Queue = asyncio.Queue(maxsize=3)
        self._tasks: list = []
        self._unsub = None

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        self._unsub = self.bus.subscribe(self._on_event)
        self._tasks = [asyncio.ensure_future(self._tts_loop()),
                       asyncio.ensure_future(self._send_loop())]

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self.gen += 1
        for t in self._tasks:
            t.cancel()
        self._tasks = []

    @property
    def busy(self) -> bool:
        """她正在说，或者马上要说（合成中 / 浏览器还在放）。"""
        return self.pending > 0 or time.monotonic() < self.playing_until + 0.4

    def reset(self) -> None:
        """打断：旧代作废。"""
        self.gen += 1
        self.pending = 0
        self.playing_until = 0.0
        self.accepted.clear()
        self.started_audio = False

    # ---------------------------------------------------------------- 回声判断
    def spoken_recently(self, within: float = 25.0) -> str:
        now = time.monotonic()
        return "".join(t for at, t in self.recent if now - at <= within)

    def is_echo(self, heard: str) -> bool:
        """识别结果是不是她刚说过的话（扬声器漏进麦克风）。"""
        h = _norm(heard)
        s = _norm(self.spoken_recently())
        if len(h) < 3 or not s:
            return False
        if h in s:
            return True
        hit = sum(1 for c in h if c in s)
        return hit / len(h) > 0.85 and len(h) >= 6

    # ---------------------------------------------------------------- 事件入口
    def _on_event(self, event: dict) -> None:
        """总线回调：可能在任何线程，转回事件循环处理。"""
        t = event.get("type")
        if t not in ("speech", "interrupt"):
            return
        try:
            self.loop.call_soon_threadsafe(self._handle, event)
        except RuntimeError:                # 循环已关闭
            pass

    def _handle(self, ev: dict) -> None:
        if ev.get("type") == "interrupt":
            self.reset()
            asyncio.ensure_future(self.send_json({"type": "flush"}))
            return
        if ev.get("ch", "main") != "main":
            return
        op, sid = ev.get("op"), ev.get("id")
        if op == "begin":
            if not ev.get("queue") or not self.busy:
                self.reset()                # 新回答顶掉旧的；排队的 say 接在后面
            self.accepted.add(sid)
            self._put("clear", sid)
        elif sid not in self.accepted:
            return                          # 已被打断的旧回复的残余事件
        elif op == "text":
            for piece in split_sentences(ev.get("text", ""), self.soft_max):
                self._put("text", sid, piece)
        elif op == "end":
            self._put("end", sid)

    def _put(self, kind: str, sid, text: str = "") -> None:
        self.pending += 1
        self.text_q.put_nowait((self.gen, kind, sid, text))

    # ---------------------------------------------------------------- 合成 / 发送
    async def _tts_loop(self) -> None:
        while True:
            gen, kind, sid, text = await self.text_q.get()
            if gen != self.gen:
                continue
            pcm = b""
            if kind == "text":
                try:
                    pcm = await asyncio.to_thread(self.synth, text)
                except Exception as e:
                    log.warning("TTS 失败：%s", e)
                    await self.send_json({"type": "error", "error": f"朗读失败：{e}"[:200]})
                if gen != self.gen:
                    continue
            await self.audio_q.put((gen, kind, sid, text, pcm))

    async def _send_loop(self) -> None:
        while True:
            gen, kind, sid, text, pcm = await self.audio_q.get()
            if gen != self.gen:
                continue
            try:
                if kind == "clear":
                    await self.send_json({"type": "caption_clear"})
                elif kind == "text":
                    if text:
                        await self.send_json({"type": "caption", "text": text})
                    if pcm:
                        if not self.started_audio:
                            self.started_audio = True
                            if self.on_audio_start:
                                self.on_audio_start()
                        for chunk in chunk_pcm(pcm, 200, self.rate):
                            if gen != self.gen:
                                break
                            await self.send_bytes(chunk)
                        if gen == self.gen:
                            now = time.monotonic()
                            self.playing_until = max(self.playing_until, now) \
                                + len(pcm) / 2 / self.rate
                    if gen == self.gen:
                        self.recent.append((time.monotonic(), text))
                elif kind == "end":
                    await self.send_json({"type": "caption_end"})
            except Exception:
                log.debug("发送失败（连接可能已断开）", exc_info=True)
            finally:
                if gen == self.gen:
                    self.pending = max(0, self.pending - 1)
