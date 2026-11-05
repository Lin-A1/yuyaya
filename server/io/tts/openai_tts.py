"""OpenAI 兼容的 /audio/speech（TTS）。

OpenAI 官方、Groq、硅基流动、one-api / new-api 之类的网关，只要是这个接口形态都能用：
POST {base_url}/audio/speech  {"model", "input", "voice", "response_format":"wav"}
"""
from __future__ import annotations

import os

import requests

from ..base import TTSProvider

DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAITTSProvider(TTSProvider):
    type = "openai"
    mime = "audio/wav"

    def available(self) -> bool:
        return bool(self._key)

    @property
    def _key(self) -> str:
        return os.environ.get(self.conf.get("api_key_env") or "OPENAI_API_KEY", "")

    def synth_wav(self, text: str, voice: str = "", rate: int = 0, volume: int = 100) -> bytes:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if not self._key:
            raise RuntimeError(f"缺少环境变量 {self.conf.get('api_key_env') or 'OPENAI_API_KEY'}")
        base = (self.conf.get("base_url") or DEFAULT_BASE).rstrip("/")
        body = {"model": self.conf.get("model") or "tts-1",
                "input": text,
                "voice": voice or self.conf.get("voice") or "nova",
                "response_format": self.conf.get("format") or "wav"}
        if rate:
            body["speed"] = max(0.25, min(4.0, 1 + rate / 20))
        r = requests.post(f"{base}/audio/speech", headers={"Authorization": f"Bearer {self._key}"},
                          json=body, timeout=(10, float(self.conf.get("timeout", 90))))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        if len(r.content) < 100:
            raise RuntimeError("TTS 没有返回音频")
        return r.content

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available(),
                "voice": self.conf.get("voice") or "nova",
                "rate": int(self.conf.get("rate", 0)),
                "volume": int(self.conf.get("volume", 100))}
