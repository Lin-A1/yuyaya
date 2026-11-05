"""Microsoft Edge 在线语音合成（免费、免 key、音色自然）。

用 ``pip install edge-tts`` 装好，``config.voice.tts.provider`` 写 ``edge``。
音色名是 Edge 的 ShortName，例如 ``zh-CN-XiaoyiNeural``（活泼少女，适合 Q 版角色）、
``zh-CN-XiaoxiaoNeural``（温和）、``zh-CN-XiaoshuangNeural``（偏童声）。
完整列表：``python -m edge_tts --list-voices``。
"""
from __future__ import annotations

import asyncio

from ..base import TTSProvider

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"


class EdgeTTSProvider(TTSProvider):
    type = "edge"
    mime = "audio/mpeg"

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
        except Exception:
            return False
        return True

    def synth_wav(self, text: str, voice: str = "", rate: int = 0,
                  volume: int = 100) -> bytes:
        """返回 MP3 字节（方法名沿用 TTSProvider 契约；前端用 decodeAudioData 解码，不挑容器）。"""
        try:
            import edge_tts
        except ImportError as e:  # pragma: no cover - 依赖缺失
            raise RuntimeError("没装 edge-tts（pip install edge-tts）") from e
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        voice = voice or self.conf.get("voice") or DEFAULT_VOICE
        # SAPI 的 rate 是 -10~10（每个 +1 约快一档），Edge 用百分比；volume 100 对应 +0%
        rate_pct = f"{max(-50, min(50, int(rate) * 10)):+d}%"
        vol_pct = f"{max(-100, min(100, int(volume) - 100)):+d}%"

        async def run() -> bytes:
            comm = edge_tts.Communicate(text, voice, rate=rate_pct, volume=vol_pct)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        data = asyncio.run(run())
        if len(data) < 100:
            raise RuntimeError("TTS 没有返回音频")
        return data

    def info(self) -> dict:
        return {"provider": self.type, "enabled": self.available(),
                "voice": self.conf.get("voice") or DEFAULT_VOICE,
                "rate": int(self.conf.get("rate", 0)),
                "volume": int(self.conf.get("volume", 100))}
