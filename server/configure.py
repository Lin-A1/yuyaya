"""配置体系：把页面上的选择翻译成运行时真正用的东西。

**模式互斥**：全双工和半双工不会同时生效。

```
mode = "full" | "half"        ← 二选一
  full : 音频进、音频出（Qwen Realtime），只负责聊天、记忆和表情动作
  half : LLM 出字 → TTS 念出来 → ASR 收话；麦克风按 ASR 配

没有文件、电脑操作或后台任务能力。外部脚本想用她的脸和嘴，走 tools/mcp_server.py（MCP，不变）。
```

LLM 只认三类通用接口（`openai` / `anthropic` / `gemini`），预设只是帮页面填表：
配置里存的永远是 `type + base_url + model + api_key_env` 这四个字段。

``config.json`` 里存的就是这套结构；老配置（``brains`` / ``realtime`` / ``voice.tts`` /
``agents.backends``）会在加载时自动迁移过来，动作配置会被丢弃。
"""
from __future__ import annotations

import copy
import pathlib

from . import catalog, persona

SCHEMA_VERSION = 5

DEFAULT_ASR = {"provider": "qwen", "model": "qwen3-asr-flash"}


def defaults() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "mode": "half",                    # full | half，二者互斥
        "history_turns": 12,
        "voice": {
            "full": {
                "provider": "qwen",
                "model": "qwen-audio-3.1-realtime-flash",
                "api_key_env": "DASHSCOPE_API_KEY",
                "voice": "qiaoxiaojiao_v3.1",
                "vad_threshold": 0.5,
                "silence_duration_ms": 500,
                "tools": True,
            },
            "half": {
                "llm": {"type": "demo", "base_url": "", "model": "", "api_key_env": "",
                        "preset": "demo"},
                "tts": {"provider": "qwen-realtime", "voice": "qiaoxiaojiao_v3.1",
                        "base_url": "", "api_key_env": "", "rate": 0, "volume": 100},
                "asr": dict(DEFAULT_ASR),
            },
        },
        "security": {"allow_remote_actions": False},
        "io": {
            "inputs": [{"type": "http"}, {"type": "browser_speech",
                                          "barge_in": "speech_start"}],
            "outputs": [{"type": "sse"}],
        },
    }


# ---------------------------------------------------------------- 迁移

def _match_preset(brain: dict) -> str:
    base = (brain.get("base_url") or "").rstrip("/")
    label = (brain.get("label") or "").lower()
    for p in catalog.LLM_PRESETS:
        if (p.get("base_url") or "").rstrip("/") == base and base:
            return p["id"]
    for p in catalog.LLM_PRESETS:
        if p["id"] in label:
            return p["id"]
    return ""


def migrate(conf: dict) -> tuple[dict, bool]:
    """老结构 → 新结构。返回 (新配置, 有没有改动)。"""
    if not isinstance(conf, dict):
        return defaults(), True
    if conf.get("version") == SCHEMA_VERSION:
        return conf, False
    if conf.get("version") in (3, 4):
        # 旧动作配置整套移除，只保留聊天 / 语音 / 人设。
        out = copy.deepcopy(conf)
        out.pop("agents", None)
        out.pop("initiative", None)
        out["version"] = SCHEMA_VERSION
        return out, True

    out = defaults()
    out["history_turns"] = conf.get("history_turns", 12)
    out["security"] = {**out["security"], **(conf.get("security") or {})}
    out["io"] = conf.get("io") or out["io"]

    # --- 模式：有全双工就 full，否则 half ---
    rt = conf.get("realtime") or {}
    full_on = bool(rt.get("enabled")) if "enabled" in rt else bool(
        (conf.get("voice") or {}).get("full", {}).get("enabled"))
    out["mode"] = "full" if full_on else "half"

    # --- LLM：统一成 type + base_url + model + api_key_env ---
    if conf.get("version") == 2:
        half = (conf.get("voice") or {}).get("half") or {}
        old_llm = half.get("llm") or {}
        preset = old_llm.get("provider") or "demo"

        src = next((p for p in catalog.LLM_PRESETS if p["id"] == preset), {})
        out["voice"]["half"]["llm"] = {
            "type": src.get("type", "openai") if preset != "demo" else "demo",
            "base_url": old_llm.get("base_url") or src.get("base_url", ""),
            "model": old_llm.get("model") or src.get("model", ""),
            "api_key_env": old_llm.get("api_key_env") or src.get("api_key_env", ""),
            "preset": preset,
        }
        old_tts = half.get("tts") or {}
        out["voice"]["half"]["tts"] = {
            "provider": old_tts.get("provider") or "qwen-realtime",
            "voice": old_tts.get("voice") or "qiaoxiaojiao_v3.1",
            "base_url": "", "api_key_env": "",
            "rate": int(old_tts.get("rate", 0)), "volume": int(old_tts.get("volume", 100)),
        }
        out["voice"]["half"]["asr"] = dict(half.get("asr") or DEFAULT_ASR)
        full = (conf.get("voice") or {}).get("full") or {}
        if full:
            out["voice"]["full"] = {**out["voice"]["full"],
                                    **{k: v for k, v in full.items() if k != "enabled"}}
        return out, True

    if conf.get("version") == 1 or "voice" in conf:
        _migrate_v1(conf, out)
        return out, True

    # 更老：只有 brains / actions
    _migrate_v1(conf, out)
    return out, True


def _migrate_v1(conf: dict, out: dict) -> None:
    brains = conf.get("brains") or {}
    default = conf.get("default") or ""

    def rank(item):
        name, b = item
        if name == default and b.get("type") in ("openai", "anthropic", "demo"):
            return 0
        if b.get("type") in ("openai", "anthropic", "gemini"):
            return 1
        if b.get("type") == "demo":
            return 2
        return 9

    chat = [(n, b) for n, b in brains.items() if b.get("type") != "cli"]
    pick = sorted(chat, key=rank)[0] if chat else None
    if pick:
        name, b = pick
        kind = "demo" if b.get("type") == "demo" else (b.get("type") or "openai")
        out["voice"]["half"]["llm"] = {
            "type": kind,
            "base_url": b.get("base_url") or "",
            "model": b.get("model") or "",
            "api_key_env": b.get("api_key_env") or "",
            "preset": _match_preset(b) or kind,
        }
    old_tts = (conf.get("voice") or {}).get("tts") or {}
    if old_tts:
        provider = old_tts.get("provider") or "qwen-realtime"
        if provider not in {p["id"] for p in catalog.TTS_PROVIDERS}:
            provider = "qwen-realtime"
        voice = old_tts.get("voice") or ""
        if voice and voice not in catalog.voice_ids(provider):
            vs = catalog.voices_for(provider)
            voice = vs[0]["id"] if vs else ""
        out["voice"]["half"]["tts"].update({
            "provider": provider, "voice": voice or "qiaoxiaojiao_v3.1",
            "rate": int(old_tts.get("rate", 0)), "volume": int(old_tts.get("volume", 100)),
        })
    rt = conf.get("realtime") or {}
    if rt:
        out["voice"]["full"] = {**out["voice"]["full"],
                                **{k: rt[k] for k in ("provider", "model", "api_key_env",
                                                      "voice", "vad_threshold",
                                                      "silence_duration_ms", "tools")
                                   if k in rt}}



# ---------------------------------------------------------------- 解析

def resolve(conf: dict, keys: dict | None = None) -> dict:
    """摊平成各模块直接能用的字典。**模式互斥在这里落地**。

    ``keys`` 是 ``settings.key_for`` 的映射 ``{通道: key}``；给了就写进各段，
    适配器优先用 ``api_key``，没有才回去读环境变量（兼容老配置）。
    """
    conf, _ = migrate(conf)
    keys = keys or {}
    voice = conf.get("voice") or {}
    full = voice.get("full") or {}
    half = voice.get("half") or {}
    mode = conf.get("mode") if conf.get("mode") in ("full", "half") else "half"

    llm = _llm_config(half.get("llm") or {}, keys.get("llm", ""))
    tts = _tts_config(half.get("tts") or {}, keys.get("tts", ""))
    asr = _asr_config(half.get("asr") or {}, keys.get("asr", ""))
    agents = _agents_config(conf.get("agents") or {})

    # 全双工开着时，半双工的 LLM / ASR 不参与（TTS 也不说话，全双工自己出声）
    active_full = mode == "full"
    system = persona.effective(conf.get("persona"))
    return {
        "conf": conf,
        "mode": mode,
        "system": system,
        "persona": persona.snapshot(conf.get("persona")),
        "llm": llm,
        "tts": tts,
        "asr": asr if not active_full else {**asr, "enabled": False, "suppressed": True},
        "full": {
            "enabled": active_full,
            "provider": full.get("vendor") or full.get("provider") or "qwen",
            "model": full.get("model") or "qwen-audio-3.1-realtime-flash",
            "api_key": keys.get("full", ""),
            "api_key_env": full.get("api_key_env") or "DASHSCOPE_API_KEY",
            "voice": full.get("voice") or "qiaoxiaojiao_v3.1",
            "vad_threshold": float(full.get("vad_threshold", 0.5)),
            "silence_duration_ms": int(full.get("silence_duration_ms", 500)),
            "tools": bool(full.get("tools", True)),
        },
        "agents": agents,
        "initiative": conf.get("initiative") or {},
        "security": conf.get("security") or {},
        "io": conf.get("io") or {},
        "history_turns": int(conf.get("history_turns", 12)),
    }


def _vendor_row(sel: dict, presets: list[dict]) -> dict:
    vid = sel.get("vendor") or sel.get("provider") or ""
    return next((p for p in presets if p["id"] == vid), {})


def _iface_id(sel: dict, default: str) -> str:
    """TTS / ASR 的接口形态 id：新配置存 type，老配置是 vendor / provider。"""
    return sel.get("type") or sel.get("vendor") or sel.get("provider") or default


def _llm_config(sel: dict, key: str = "") -> dict:
    """LLM 是通用接口：type + base_url + model + key，不看厂商。

    旧配置里的 ``vendor`` 只用来兜底环境变量名（没有页面 key 时才用）。
    """
    kind = sel.get("type") or "demo"
    kind = _TYPE_ALIASES.get(kind, kind)       # 老配置里的 openai/anthropic/gemini
    legacy = _vendor_row(sel, catalog.LLM_PRESETS)
    type_row = catalog.provider("llm_type", kind) or catalog.provider("llm_type", "completions")
    base = (sel.get("base_url") or type_row.get("default_base", "")).strip()
    model = (sel.get("model") or "").strip()
    models = [m["id"] if isinstance(m, dict) else m for m in (sel.get("models") or [])]
    if not model and models:
        model = models[0]
    key_env = legacy.get("api_key_env") or ("" if kind == "demo"
                                            else _DEFAULT_KEY_ENV.get(kind, "OPENAI_API_KEY"))
    return {"type": kind, "vendor": kind,
            "label": "离线演示" if kind == "demo" else type_row.get("label", kind),
            "base_url": base, "model": model,
            "api_key": key, "api_key_env": key_env,
            "models": models or ([model] if model else []),
            "extra": sel.get("extra") if isinstance(sel.get("extra"), dict) else {}}


_TYPE_ALIASES = {"openai": "completions", "anthropic": "messages", "gemini": "generateContent"}
_DEFAULT_KEY_ENV = {"messages": "ANTHROPIC_API_KEY", "generateContent": "GEMINI_API_KEY"}


def _tts_config(sel: dict, key: str = "") -> dict:
    pid = _iface_id(sel, "qwen-realtime")
    row = next((p for p in catalog.TTS_PROVIDERS if p["id"] == pid), {})
    decl = [m["id"] if isinstance(m, dict) else m for m in (row.get("models") or [])]
    model = sel.get("model") or (decl[0] if decl else "")
    # 音色跟着模型走：同一个厂商的不同模型音色表不一样
    by_model = row.get("voices_by_model") or {}
    table = by_model.get(model) or by_model.get("*") or row.get("voices") or []
    voices = [i for i, _ in table]
    voice = sel.get("voice") or (voices[0] if voices else "")
    return {"enabled": sel.get("enabled", True) is not False, "provider": pid,
            "label": row.get("label", pid), "model": model, "voice": voice,
            "api_key": key, "api_key_env": row.get("key_env", ""),
            "key_env": row.get("key_env", ""),
            "base_url": sel.get("base_url") or "",
            "rate": int(sel.get("rate", 0)), "volume": int(sel.get("volume", 100))}


def _asr_config(sel: dict, key: str = "") -> dict:
    pid = _iface_id(sel, "none")
    row = next((p for p in catalog.ASR_PROVIDERS if p["id"] == pid), {})
    models = [m["id"] if isinstance(m, dict) else m
              for m in (sel.get("models") or row.get("models") or [])]
    return {"provider": pid, "label": row.get("label", pid),
            "model": sel.get("model") or (models[0] if models else ""),
            "api_key": key, "key_env": row.get("key_env", ""), "models": models,
            "base_url": sel.get("base_url") or "",
            "enabled": pid != "none"}


_ROOT = pathlib.Path(__file__).resolve().parent.parent


def default_workspace() -> str:
    return str(_ROOT / "state" / "workspace")


def _agents_config(agents: dict) -> dict:
    """聊天专用 runtime：不创建任何能读写文件或操作桌面的后端。"""
    return {"enabled": False, "backends": {}, "routing": {}, "default": "",
            "ack": "好呀。", "mode": "auto", "active": "",
            "desktop": False, "workspace": "",
            "max_concurrent": 0, "max_refine": 0}


def legacy_brain_config(resolved: dict) -> dict:
    """给 Avatar 用的 ``brains`` 字典（保持老构造函数不变）。"""
    llm = resolved["llm"]
    system = resolved.get("system") or ""
    common = {"system": system} if system else {}
    kind = llm.get("type") or "demo"
    if kind == "demo":
        return {"demo": {"type": "demo", "label": "演示（离线）", **common}}
    # 大脑适配器认识的是接口类型，不是预设名
    extra = {"label": llm.get("label"), "model": llm.get("model"),
             "base_url": llm.get("base_url", ""), "api_key": llm.get("api_key", ""),
             "extra": llm.get("extra") or {},
             "api_key_env": llm.get("api_key_env"), **common}
    return {kind: {"type": kind, **extra}}


def compat(resolved: dict) -> dict:
    """把新结构摊回各模块一直在用的老形状（模块签名不变）。"""
    llm, tts, asr, full = resolved["llm"], resolved["tts"], resolved["asr"], resolved["full"]
    agents = resolved["agents"]
    return {
        "mode": resolved["mode"],
        "default": next(iter(legacy_brain_config(resolved))),
        "history_turns": resolved["history_turns"],
        "brains": legacy_brain_config(resolved),
        "voice": {"tts": {"enabled": tts["enabled"], "provider": tts["provider"],
                          "model": tts["model"], "voice": tts["voice"], "rate": tts["rate"],
                          "volume": tts["volume"], "base_url": tts["base_url"],
                          "api_key": tts["api_key"], "api_key_env": tts["key_env"]}},
        "realtime": {
            "enabled": full["enabled"], "provider": full["provider"],
            "model": full["model"], "api_key_env": full["api_key_env"],
            "api_key": full["api_key"],
            "voice": full["voice"], "vad_threshold": full["vad_threshold"],
            "silence_duration_ms": full["silence_duration_ms"], "tools": full["tools"],
            # 全双工也用人设；页面上改过人设时这里给完整文本，没改就用内置常量
            "persona_text": resolved.get("system") or "",
            "input_sample_rate": 16000, "output_sample_rate": 24000,
        },
        "agents": {
            "enabled": agents["enabled"], "default": agents["default"],
            "routing": agents["routing"], "backends": agents["backends"],
            "mode": agents.get("mode", "auto"), "active": agents.get("active", ""),
            "max_concurrent": agents["max_concurrent"], "max_refine": agents["max_refine"],
        },
        "actions": {"enabled": agents["enabled"], "ack": agents.get("ack") or "好，我去弄一下！",
                    "timeout": 300},
        "initiative": resolved["initiative"],
        "security": resolved["security"],
        "io": resolved["io"],
        "_capabilities": {"llm": llm, "tts": tts, "asr": asr, "full": full,
                          "mode": resolved["mode"],
                          "agents": {"enabled": agents["enabled"],
                                     "mode": agents["mode"],
                                     "active": agents["active"],
                                     "backends": list(agents["backends"]),
                                     "default": agents["default"]}},
    }
