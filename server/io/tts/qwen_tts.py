"""Qwen TTS（DashScope 原生 multimodal-generation 接口）。

和实时语音那套不同：这里用的是 ``qwen3-tts-flash`` 这类**语音合成模型**，
音色是它自己的（Cherry / Ethan / Serena …），不是实时语音的 28 个预置音色。

    POST /api/v1/services/aigc/multimodal-generation/generation
    {"model": "qwen3-tts-flash", "input": {"text": "...", "voice": "Cherry"}}
    → {"output": {"audio": {"url": "https://..."}}}

拿到的是 wav/mp3 的临时 URL，下载回来再返回给前端。
"""
from __future__ import annotations

import os
import struct

import requests

from ..base import TTSProvider

DEFAULT_BASE = "https://dashscope.aliyuncs.com"
DEFAULT_MODEL = "qwen3-tts-flash"
DEFAULT_VOICE = "Cherry"


class QwenTTSProvider(TTSProvider):
    type = "qwen-tts"
    mime = "audio/wav"

    @property
    def _key(self) -> str:
        return self.conf.get("api_key") or os.environ.get(
            self.conf.get("api_key_env") or "DASHSCOPE_API_KEY", "")

    def available(self) -> bool:
        return bool(self._key)

    def synth_wav(self, text: str, voice: str = "", rate: int = 0, volume: int = 100) -> bytes:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if not self._key:
            raise RuntimeError("缺少 DashScope API Key")
        model = self.conf.get("model") or DEFAULT_MODEL
        body = {
            "model": model,
            "input": {"text": text, "voice": voice or self.conf.get("voice") or DEFAULT_VOICE},
        }
        base = (self.conf.get("base_url") or DEFAULT_BASE).rstrip("/")
        url = f"{base}/api/v1/services/aigc/multimodal-generation/generation"
        r = requests.post(url, headers={"Authorization": f"Bearer {self._key}",
                                        "Content-Type": "application/json"},
                          json=body, timeout=(10, float(self.conf.get("timeout", 90))))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            audio = (r.json().get("output") or {}).get("audio") or {}
        except ValueError as e:
            raise RuntimeError(f"返回格式不对：{r.text[:200]}") from e
        data = audio.get("data")
        if data:                                  # 有的模型直接回 base64
            import base64
            return base64.b64decode(data)
        link = audio.get("url")
        if not link:
            raise RuntimeError(f"没拿到音频：{r.text[:200]}")
        got = requests.get(link, timeout=60)
        if got.status_code != 200:
            raise RuntimeError(f"下载音频失败 HTTP {got.status_code}")
        return _fix_wav_header(got.content)


def _fix_wav_header(data: bytes) -> bytes:
    """服务端返回的 WAV 是流式占位头（长度写成 0x7FFFFFBF），这里改成真实长度。

    不改也能播，但有的播放器/解码器会按声明长度去读，结果是静音或者干脆不播。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    out = bytearray(data)
    struct.pack_into("<I", out, 4, len(out) - 8)
    i = out.find(b"data", 12)
    if i >= 0:
        struct.pack_into("<I", out, i + 4, len(out) - i - 8)
    return bytes(out)

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available(),
                "voice": self.conf.get("voice") or DEFAULT_VOICE,
                "model": self.conf.get("model") or DEFAULT_MODEL,
                "rate": int(self.conf.get("rate", 0)),
                "volume": int(self.conf.get("volume", 100))}
