"""TTS 提供者注册表。

加一种语音合成 = 在 tts/ 下加一个文件实现 TTSProvider，然后在这里登记；
``config.voice.half.tts.provider`` 写它的 id。
"""
from __future__ import annotations

import logging

from .edge import EdgeTTSProvider
from .openai_tts import OpenAITTSProvider
from .qwen_realtime import QwenRealtimeTTSProvider
from .qwen_tts import QwenTTSProvider
from .sapi import SapiTTSProvider

log = logging.getLogger("yuyaya.io")

PROVIDERS = {
    "sapi": SapiTTSProvider,
    "edge": EdgeTTSProvider,
    "qwen-realtime": QwenRealtimeTTSProvider,
    "qwen-tts": QwenTTSProvider,
    "openai": OpenAITTSProvider,
}

# 这些 provider 不依赖任何 key，永远可用（缺 key 时回退到它们不会白回退）
KEYLESS = ("sapi", "edge")


def build(conf: dict | None):
    conf = dict(conf or {})
    kind = (conf.get("provider") or "qwen-realtime").strip().lower()
    cls = PROVIDERS.get(kind)
    if cls is None:
        raise ValueError(f"未知的 TTS provider {kind!r}（可用：{', '.join(PROVIDERS)}）")
    provider = cls(conf)
    if kind not in KEYLESS and not provider.available():
        # 缺 key / 没装依赖时退回本机 SAPI，别让整个语音链路挂掉
        log.warning("TTS provider %s 不可用，退回 sapi", kind)
        provider = PROVIDERS["sapi"]({**conf, "provider": "sapi", "voice": ""})
    return provider


__all__ = ["PROVIDERS", "build", "SapiTTSProvider", "EdgeTTSProvider",
           "QwenRealtimeTTSProvider", "QwenTTSProvider", "OpenAITTSProvider"]
