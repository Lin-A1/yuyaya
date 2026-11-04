"""TTS 提供者注册表。

加一种语音合成 = 在 tts/ 下加一个文件实现 TTSProvider，然后
``config.voice.tts.provider`` 写它的 type。
"""
from __future__ import annotations

from .sapi import SapiTTSProvider

PROVIDERS = {
    "sapi": SapiTTSProvider,
}


def build(conf: dict | None) -> SapiTTSProvider:
    conf = conf or {}
    kind = (conf.get("provider") or "sapi").strip().lower()
    cls = PROVIDERS.get(kind)
    if cls is None:
        raise ValueError(f"未知的 TTS provider {kind!r}（可用：{', '.join(PROVIDERS)}）")
    return cls(conf)


__all__ = ["PROVIDERS", "build", "SapiTTSProvider"]
