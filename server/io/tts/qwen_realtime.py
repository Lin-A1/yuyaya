"""用 Qwen-Audio-Realtime 的预置音色朗读文字：打字回复也能用和语音对话同一个音色。

文字进、PCM16(24k) 出，包成 WAV 返回。默认音色 ``qiaoxiaojiao_v3.1``；
音色列表和实时语音共用（见 README 的“音色”一节）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import wave

import aiohttp

from ..base import TTSProvider

DEFAULT_URL = ("wss://cn-beijing.maas.qianwenaiapi.com/api-ws/v1/realtime"
               "?model={model}")
DEFAULT_MODEL = "qwen-audio-3.1-realtime-flash"
DEFAULT_VOICE = "qiaoxiaojiao_v3.1"
# 只让它朗读，不要回答内容、不要加开场白
SYSTEM = ("你是一个语音朗读器。用户发来的每条消息都只是要你朗读的文本："
          "请一字不差地读出来，不要回答内容、不要加“好的”“嗯”之类的话，"
          "不要解释，不要使用任何表情符号。")


class QwenRealtimeTTSProvider(TTSProvider):
    type = "qwen-realtime"
    mime = "audio/wav"

    # ---- 配置 ----
    @property
    def _key_env(self) -> str:
        return self.conf.get("api_key_env") or "DASHSCOPE_API_KEY"

    @property
    def _key(self) -> str:
        return self.conf.get("api_key") or os.environ.get(self._key_env, "")

    @property
    def _model(self) -> str:
        return self.conf.get("model") or DEFAULT_MODEL

    @property
    def _voice(self) -> str:
        return self.conf.get("voice") or DEFAULT_VOICE

    @property
    def _rate(self) -> int:
        return int(self.conf.get("sample_rate", 24000))

    def available(self) -> bool:
        return bool(self._key)

    def synth_wav(self, text: str, voice: str = "", rate: int = 0,
                  volume: int = 100) -> bytes:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if not self._key:
            raise RuntimeError(f"缺少环境变量 {self._key_env}")
        voice = voice or self._voice
        pcm = asyncio.run(self._synth(text, voice))
        if len(pcm) < 200:
            raise RuntimeError("TTS 没有返回音频")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self._rate)
            w.writeframes(pcm)
        return buf.getvalue()

    async def _synth(self, text: str, voice: str) -> bytes:
        url = (self.conf.get("url") or DEFAULT_URL).format(model=self._model)
        timeout = float(self.conf.get("timeout", 40))
        pcm = bytearray()
        timeout_cfg = aiohttp.ClientTimeout(total=None, sock_connect=15)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as sess:
            async with sess.ws_connect(
                    url, headers={"Authorization": f"Bearer {self._key}"},
                    heartbeat=20, max_msg_size=0) as ws:
                await ws.send_json({"type": "session.update", "session": {
                    "modalities": ["text", "audio"],
                    "voice": voice,
                    "instructions": SYSTEM,
                    "turn_detection": None,      # 只是朗读，不需要 VAD
                }})
                await ws.send_json({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": text}]}})
                await ws.send_json({"type": "response.create"})
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                while True:
                    left = deadline - loop.time()
                    if left <= 0:
                        break
                    msg = await asyncio.wait_for(ws.receive(), timeout=left)
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    ev = json.loads(msg.data)
                    etype = ev.get("type")
                    if etype == "response.audio.delta":
                        pcm.extend(base64.b64decode(ev.get("delta") or ""))
                    elif etype == "error":
                        raise RuntimeError(str(ev.get("error") or ev)[:300])
                    elif etype in ("response.done", "response.audio.done"):
                        break
            # 让 aiohttp 把连接收干净，避免 Windows 上 loop 关闭时的噪音
            await asyncio.sleep(0.05)
        return bytes(pcm)

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available(),
                "voice": self._voice,
                "rate": int(self.conf.get("rate", 0)),
                "volume": int(self.conf.get("volume", 100))}
