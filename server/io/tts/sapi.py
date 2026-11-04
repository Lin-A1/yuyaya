"""Windows SAPI（System.Speech）语音合成。"""
from __future__ import annotations

from ... import tts
from ..base import TTSProvider


class SapiTTSProvider(TTSProvider):
    type = "sapi"

    def available(self) -> bool:
        return tts.available()

    def synth_wav(self, text: str, voice: str = "", rate: int = 0,
                  volume: int = 100) -> bytes:
        return tts.synth_wav(text, voice=voice, rate=rate, volume=volume)

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available(),
                "voice": self.conf.get("voice") or "",
                "rate": int(self.conf.get("rate", 0)),
                "volume": int(self.conf.get("volume", 100))}
