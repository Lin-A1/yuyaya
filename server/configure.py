"""配置体系：把页面上的选择翻译成运行时真正用的东西。

**模式互斥**：全双工和半双工不会同时生效，三个 CLI 也不会同时被调用。

```
mode = "full" | "half"        ← 二选一
  full : 音频进、音频出（Qwen Realtime），工具调用走 agent
  half : LLM 出字 → TTS 念出来 → ASR 收话；麦克风按 ASR 配

agents.mode = "auto" | "manual"
  auto   : 按任务类型（code / search / desktop）自动挑一个后端
  manual : 只用 agents.active 指定的那一个
```

LLM 只认三类通用接口（`openai` / `anthropic` / `gemini`），预设只是帮页面填表：
配置里存的永远是 `type + base_url + model + api_key_env` 这四个字段。

``config.json`` 里存的就是这套结构；老配置（``brains`` / ``realtime`` / ``voice.tts`` /
``agents.backends``）会在加载时自动迁移过来。
"""
from __future__ import annotations

import copy

from . import catalog, persona

SCHEMA_VERSION = 3

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
        "agents": {
            "enabled": True,
            "mode": "auto",                # auto | manual
            "active": "",                  # manual 模式下用哪个
            "max_concurrent": 2,
            "max_refine": 1,
            "cli": {
                "claude": {"enabled": True, "model": ""},
                "codex": {"enabled": True, "model": ""},
                "opencode": {"enabled": True, "model": ""},
            },
            "routing": {"desktop": "codex-desktop", "code": "claude", "search": "claude"},
            "desktop": {"enabled": True},
        },
        "initiative": {"enabled": True, "cooldown": 90, "hourly_cap": 6,
                       "quiet_minutes": 30, "idle_chat_seconds": 0},
        "security": {"allow_remote_actions": False},
        "io": {
            "inputs": [{"type": "http"}, {"type": "browser_speech",
                                          "barge_in": "speech_start"}],
            "outputs": [{"type": "sse"}, {"type": "action_log"}],
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

    out = defaults()
    out["history_turns"] = conf.get("history_turns", 12)
    out["security"] = {**out["security"], **(conf.get("security") or {})}
    out["initiative"] = {**out["initiative"], **(conf.get("initiative") or {})}
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
        _migrate_agents(conf, out)
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
    _migrate_agents(conf, out)


def _migrate_agents(conf: dict, out: dict) -> None:
    agents = conf.get("agents") or {}
    if not agents:
        act = conf.get("actions") or {}
        if act.get("cmd"):
            out["agents"]["custom"] = {"cmd": list(act["cmd"]),
                                      "timeout": float(act.get("timeout", 180)),
                                      "ack": act.get("ack") or "好，我去弄一下！"}
            out["agents"]["cli"] = {k: {**v, "enabled": False}
                                    for k, v in out["agents"]["cli"].items()}
        return
    out["agents"]["enabled"] = bool(agents.get("enabled", True))
    out["agents"]["max_concurrent"] = int(agents.get("max_concurrent", 2))
    out["agents"]["max_refine"] = int(agents.get("max_refine", 1))
    out["agents"]["routing"] = agents.get("routing") or out["agents"]["routing"]
    # v2 已经有 cli 选择，直接搬
    if isinstance(agents.get("cli"), dict) and "backend" not in agents:
        for aid, sel in agents["cli"].items():
            if aid in out["agents"]["cli"] and isinstance(sel, dict):
                out["agents"]["cli"][aid].update(
                    {k: v for k, v in sel.items() if k in ("enabled", "model")})
        out["agents"]["desktop"]["enabled"] = bool(
            (agents.get("desktop") or {}).get("enabled", True))
    else:
        backends = agents.get("backends") or {}
        for agent_id in ("claude", "codex", "opencode"):
            spec = _find_backend(backends, agent_id)
            if spec is None:
                continue
            out["agents"]["cli"][agent_id]["enabled"] = spec.get("enabled", True) is not False
            model = _model_from_cmd(spec.get("cmd") or [], agent_id)
            if model:
                out["agents"]["cli"][agent_id]["model"] = model
        out["agents"]["desktop"]["enabled"] = any("desktop" in k for k in backends)


def _find_backend(backends: dict, agent_id: str) -> dict | None:
    for name, spec in backends.items():
        if name == agent_id or name.startswith(f"{agent_id}-"):
            return spec
    return None


def _model_from_cmd(cmd: list[str], agent_id: str) -> str:
    spec = catalog.cli_spec(agent_id)
    for i, token in enumerate(cmd):
        for f in spec.get("model_flag") or []:
            if token == f and i + 1 < len(cmd):
                return cmd[i + 1]
            if token.startswith(f"{f}="):
                return token.split("=", 1)[1]
    return ""


# ---------------------------------------------------------------- 解析

def resolve(conf: dict) -> dict:
    """摊平成各模块直接能用的字典。**模式互斥在这里落地**。"""
    conf, _ = migrate(conf)
    voice = conf.get("voice") or {}
    full = voice.get("full") or {}
    half = voice.get("half") or {}
    mode = conf.get("mode") if conf.get("mode") in ("full", "half") else "half"

    llm = _llm_config(half.get("llm") or {})
    tts = _tts_config(half.get("tts") or {})
    asr = _asr_config(half.get("asr") or {})
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
            "provider": full.get("provider") or "qwen",
            "model": full.get("model") or "qwen-audio-3.1-realtime-flash",
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


def _llm_config(sel: dict) -> dict:
    kind = sel.get("type") or "demo"
    # 老配置里的 openai/anthropic/gemini 映射到新的接口名
    kind = _TYPE_ALIASES.get(kind, kind)
    preset = next((p for p in catalog.LLM_PRESETS if p["id"] == sel.get("preset")), {})
    type_row = catalog.provider("llm_type", kind) or catalog.provider("llm_type", "completions")
    base = sel.get("base_url") or preset.get("base_url") or type_row.get("default_base", "")
    model = sel.get("model") or preset.get("model", "")
    key_env = sel.get("api_key_env") or ("" if kind == "demo" else
                                         preset.get("api_key_env")
                                         or _DEFAULT_KEY_ENV.get(kind, "OPENAI_API_KEY"))
    return {"type": kind, "label": preset.get("label") or type_row.get("label", kind),
            "base_url": base, "model": model, "api_key_env": key_env,
            "preset": sel.get("preset") or kind,
            "models": preset.get("models") or ([model] if model else [])}


_TYPE_ALIASES = {"openai": "completions", "anthropic": "messages", "gemini": "generateContent"}
_DEFAULT_KEY_ENV = {"messages": "ANTHROPIC_API_KEY", "generateContent": "GEMINI_API_KEY"}


def _tts_config(sel: dict) -> dict:
    pid = sel.get("provider") or "qwen-realtime"
    p = catalog.provider("tts", pid) or catalog.provider("tts", "qwen-realtime")
    voices = [i for i, _ in (p.get("voices") or [])]
    voice = sel.get("voice") or (voices[0] if voices else "")
    return {"enabled": sel.get("enabled", True) is not False, "provider": p["id"],
            "label": p.get("label", p["id"]), "voice": voice,
            "key_env": p.get("key_env", ""),
            "base_url": sel.get("base_url") or "",
            "rate": int(sel.get("rate", 0)), "volume": int(sel.get("volume", 100))}


def _asr_config(sel: dict) -> dict:
    pid = sel.get("provider") or "none"
    p = catalog.provider("asr", pid) or catalog.provider("asr", "none")
    models = p.get("models") or []
    return {"provider": p["id"], "label": p.get("label", p["id"]),
            "model": sel.get("model") or (models[0] if models else ""),
            "key_env": p.get("key_env", ""), "models": models,
            "base_url": sel.get("base_url") or "",
            "enabled": p["id"] != "none"}


def _agents_config(agents: dict) -> dict:
    """把 CLI 选择 + 探测结果变成 orchestrator 要的 backends 字典。"""
    enabled = bool(agents.get("enabled", True))
    cli_sel = agents.get("cli") or {}
    routing = dict(agents.get("routing") or {})
    backends: dict[str, dict] = {}
    ack = (agents.get("custom") or {}).get("ack") or "好，我去弄一下！"

    custom = agents.get("custom") or {}
    if custom.get("cmd"):
        backends["custom"] = {"type": "cli", "cmd": list(custom["cmd"]),
                              "timeout": float(custom.get("timeout", 180))}

    for spec in catalog.CLI_AGENTS:
        aid = spec["id"]
        sel = cli_sel.get(aid) or {}
        if sel.get("enabled", True) is False:
            continue
        status = catalog.cli_status(spec)
        if not status["installed"]:
            continue
        # "model" 键存在（哪怕是空串）= 用户明确选过；不存在 = 用 CLI 自己的默认。
        model = sel["model"] if "model" in sel else (status.get("default_model") or "")
        cmd = catalog.apply_model(_default_cmd(aid), spec.get("model_flag") or [], model)
        backends[aid] = {"type": "cli", "cmd": cmd, "timeout": 300, "model": model}
        if aid == "codex" and agents.get("desktop", {}).get("enabled", True):
            backends["codex-desktop"] = {
                "type": "cli", "timeout": 300, "model": model, "desktop": True,
                "cmd": catalog.apply_model(
                    ["codex", "exec", "--skip-git-repo-check", "-s", "danger-full-access",
                     "--output-last-message", "{outfile}", "-"], spec.get("model_flag") or [], model),
            }

    available = list(backends)
    for kind, target in list(routing.items()):
        if target not in backends:
            routing[kind] = available[0] if available else ""
    default = routing.get("code") or (available[0] if available else "")

    # 单后端模式：只有 agents.active 会被调用，路由表形同虚设
    amode = agents.get("mode") or "auto"
    active = agents.get("active") or ""
    if amode == "manual":
        chosen = active if active in backends else (available[0] if available else "")
        if chosen:
            routing = {k: chosen for k in ("code", "search", "desktop")}
            default = chosen
        available = [chosen] if chosen else []
    return {"enabled": enabled and bool(backends), "backends": backends,
            "routing": routing, "default": default, "ack": ack,
            "mode": amode, "active": active if amode == "manual" else "",
            "max_concurrent": int(agents.get("max_concurrent", 2)),
            "max_refine": int(agents.get("max_refine", 1))}


def _default_cmd(agent_id: str) -> list[str]:
    return {
        # --allowedTools 是可变参数，后面跟位置参数会被它吞掉，提示词必须走 stdin
        "claude": ["claude", "-p", "--permission-mode", "acceptEdits", "--allowedTools",
                   "Write", "Edit", "Read", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"],
        "codex": ["codex", "exec", "--skip-git-repo-check", "--output-last-message",
                  "{outfile}", "-"],
        "opencode": ["opencode", "run", "--auto", "{prompt}"],
    }[agent_id]


def legacy_brain_config(resolved: dict) -> dict:
    """给 Avatar 用的 ``brains`` 字典（保持老构造函数不变）。"""
    llm = resolved["llm"]
    system = resolved.get("system") or ""
    common = {"system": system} if system else {}
    kind = llm.get("type") or "demo"
    if kind == "demo":
        return {"demo": {"type": "demo", "label": "演示（离线）", **common}}
    # 大脑适配器认识的是接口类型，不是预设名
    key = kind
    extra = {"label": llm.get("label"), "model": llm.get("model"),
             "api_key_env": llm.get("api_key_env"), **common}
    if kind == "generateContent":
        return {key: {"type": kind, **extra}}
    if kind == "messages":
        return {key: {"type": kind, **extra}}
    return {key: {"type": kind, "base_url": llm.get("base_url", ""), **extra}}


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
                          "voice": tts["voice"], "rate": tts["rate"],
                          "volume": tts["volume"], "base_url": tts["base_url"],
                          "api_key_env": tts["key_env"]}},
        "realtime": {
            "enabled": full["enabled"], "provider": full["provider"],
            "model": full["model"], "api_key_env": full["api_key_env"],
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
