"""按配置构建适配器。

配置缺省时等价于改造前的行为：

    io.inputs  = [http, browser_speech]
    io.outputs = [sse, action_log]
    voice.tts.provider = sapi
"""
from __future__ import annotations

import logging

from .inputs import INPUTS
from .outputs import OUTPUTS
from .tts import build as build_tts_provider

log = logging.getLogger("yuyaya.io")

DEFAULT_INPUTS = [{"type": "http"}, {"type": "browser_speech"}]
DEFAULT_OUTPUTS = [{"type": "sse"}, {"type": "action_log"}]


def _io_conf(conf: dict) -> dict:
    return (conf or {}).get("io") or {}


def build_inputs(conf: dict, dispatcher) -> dict:
    io = _io_conf(conf)
    specs = io.get("inputs") if isinstance(io.get("inputs"), list) else DEFAULT_INPUTS
    barge = (io.get("barge_in") or {}).get("on") or "speech_start"
    built: dict = {}
    for spec in specs:
        spec = dict(spec or {})
        if spec.get("enabled", True) is False:
            continue
        kind = (spec.pop("type", "") or "").lower()
        cls = INPUTS.get(kind)
        if cls is None:
            raise ValueError(f"未知的输入适配器 {kind!r}（可用：{', '.join(INPUTS)}）")
        if kind == "browser_speech":
            spec.setdefault("barge_in", barge)
        name = spec.pop("name", None) or kind
        adapter = cls(spec, dispatcher)
        adapter.start()
        built[name] = adapter
        log.info("输入适配器：%s", name)
    return built


def build_outputs(conf: dict, bus) -> dict:
    io = _io_conf(conf)
    specs = io.get("outputs") if isinstance(io.get("outputs"), list) else DEFAULT_OUTPUTS
    built: dict = {}
    for spec in specs:
        spec = dict(spec or {})
        if spec.get("enabled", True) is False:
            continue
        kind = (spec.pop("type", "") or "").lower()
        cls = OUTPUTS.get(kind)
        if cls is None:
            raise ValueError(f"未知的输出适配器 {kind!r}（可用：{', '.join(OUTPUTS)}）")
        name = spec.pop("name", None) or kind
        adapter = cls(spec, bus)
        adapter.start()
        built[name] = adapter
        log.info("输出适配器：%s", name)
    return built


def build_tts(conf: dict):
    # 老的 voice.tts.provider 优先，io.tts.provider 兜底
    voice = (conf or {}).get("voice") or {}
    tts_conf = dict(voice.get("tts") or {})
    io_tts = _io_conf(conf).get("tts") or {}
    if "provider" not in tts_conf and io_tts.get("provider"):
        tts_conf["provider"] = io_tts["provider"]
    return build_tts_provider(tts_conf)
