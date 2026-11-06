"""页面设置：通道化配置的读写。

四个通道形状一致：**厂商 + API 格式 + Base URL + API Key + 模型列表（选一个）**

    voice.half.llm / voice.half.tts / voice.half.asr / voice.full

key **不写进 config.json**，按通道存进 ``state/secrets.json``：

```json
{"llm": "sk-...", "tts": "...", "asr": "...", "full": "..."}
```

保存时包一层兼容：老版本按环境变量名存的 key（``DASHSCOPE_API_KEY`` 那种）仍然能读到，
新存的 key 优先。接口只回显掩码，不回传明文。
"""
from __future__ import annotations

import json
import os
import pathlib

from . import catalog, persona

SECRETS_NAME = "secrets.json"
CHANNELS = ("llm", "tts", "asr", "full")


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-4:]}"


def _persona_rules() -> str:
    """只读的协议规则：从内置人设里摘出"输出规则"那一段给页面展示。"""
    full = persona.DEFAULT_IDENTITY
    i = full.find("【输出规则")
    return full[i:] if i >= 0 else full[-600:]


def _model_list(raw) -> list[dict]:
    """模型列表统一成 [{"id","label"}]。"""
    out = []
    for m in raw or []:
        if isinstance(m, str):
            if m.strip():
                out.append({"id": m.strip(), "label": m.strip()})
        elif isinstance(m, dict) and m.get("id"):
            out.append({"id": str(m["id"]), "label": str(m.get("label") or m["id"])})
    return out


class Settings:
    def __init__(self, conf: dict, config_path: pathlib.Path, state_dir: pathlib.Path) -> None:
        self.conf = conf
        self.config_path = pathlib.Path(config_path)
        self.secrets_path = pathlib.Path(state_dir) / SECRETS_NAME
        self.secrets: dict[str, str] = self._read_secrets()

    # ---------------------------------------------------------------- key
    def _read_secrets(self) -> dict[str, str]:
        try:
            data = json.loads(self.secrets_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def write_secrets(self) -> None:
        try:
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            self.secrets_path.write_text(
                json.dumps(self.secrets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def key_for(self, channel: str) -> str:
        """取某个通道的 key：页面存的优先，老的环境变量名兜底。"""
        val = (self.secrets.get(channel) or "").strip()
        if val:
            return val
        env = self._legacy_env(channel)
        return (os.environ.get(env, "") if env else "") or ""

    def _legacy_env(self, channel: str) -> str:
        """老版本在 config 里记的是 api_key_env；迁移后用它兜底读环境变量。"""
        voice = self.conf.get("voice") or {}
        if channel == "full":
            return (voice.get("full") or {}).get("api_key_env") or "DASHSCOPE_API_KEY"
        sel = (voice.get("half") or {}).get(channel) or {}
        if sel.get("api_key_env"):
            return sel["api_key_env"]
        preset = next((p for p in catalog.LLM_PRESETS if p["id"] == sel.get("vendor")), {})
        return preset.get("api_key_env") or ""

    def set_key(self, channel: str, value: str) -> None:
        value = (value or "").strip()
        if value:
            self.secrets[channel] = value
        else:
            self.secrets.pop(channel, None)

    # ---------------------------------------------------------------- 读
    def snapshot(self, runtime: dict | None = None) -> dict:
        runtime = runtime or {}
        backends = runtime.get("agents", {}).get("backends") or []
        voice = self.conf.get("voice") or {}
        half = voice.get("half") or {}
        agents = self.conf.get("agents") or {}
        cli_sel = agents.get("cli") or {}

        clis = []
        for spec in catalog.CLI_AGENTS:
            st = catalog.cli_status(spec)
            sel = cli_sel.get(spec["id"]) or {}
            st["model"] = sel["model"] if "model" in sel else ""
            st["auto_model"] = st.get("default_model") or ""
            st["enabled"] = sel.get("enabled", True) is not False
            st["active"] = bool(st["enabled"] and st["installed"]
                                and spec["id"] in backends)
            clis.append(st)

        return {
            "version": self.conf.get("version", 3),
            "mode": self.conf.get("mode") or "half",
            "llm": self._channel("llm", half.get("llm") or {}, catalog.LLM_PRESETS,
                                 catalog.LLM_TYPES),
            "tts": self._channel("tts", half.get("tts") or {}, catalog.TTS_PROVIDERS, None),
            "asr": self._channel("asr", half.get("asr") or {}, catalog.ASR_PROVIDERS, None),
            "full": self._channel("full", voice.get("full") or {}, catalog.FULL_PROVIDERS, None),
            "agents": {
                "enabled": bool(agents.get("enabled", True)),
                "mode": agents.get("mode") or "auto",
                "active": agents.get("active") or "",
                "max_concurrent": int(agents.get("max_concurrent", 2)),
                "desktop": bool((agents.get("desktop") or {}).get("enabled", True)),
                "cli": clis,
                "backends": backends,
            },
            "persona": persona.snapshot(self.conf.get("persona")),
            "persona_rules": _persona_rules(),
            "secrets_path": str(self.secrets_path),
        }

    def _channel(self, name: str, sel: dict, presets: list[dict],
                 types: list[dict] | None = None) -> dict:
        """一个通道的展示数据。"""
        vendor = (sel.get("vendor") or sel.get("provider") or "").strip()
        row = next((p for p in presets if p["id"] == vendor), {})
        default_models = _model_list(row.get("models") or sel.get("models") or [])
        models = _model_list(sel.get("models")) or default_models
        model = (sel.get("model") or "").strip()
        if model and model not in {m["id"] for m in models}:
            models.insert(0, {"id": model, "label": model})
        if not model and models:
            model = models[0]["id"]
        key = self.key_for(name)
        info = {
            "vendor": vendor or (presets[0]["id"] if presets else ""),
            "vendors": [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                         "default_base": p.get("base_url", ""),
                         "default_models": _model_list(p.get("models") or []),
                         "key_env": p.get("key_env", "")} for p in presets],
            "base_url": (sel.get("base_url") or row.get("base_url") or "").strip(),
            "models": models,
            "model": model,
            "has_key": bool(key),
            "key_hint": mask(key),
        }
        if name == "full":
            info["voices"] = [{"id": i, "label": l} for i, l in catalog.QWEN_VOICES]
            info["voice"] = sel.get("voice") or "qiaoxiaojiao_v3.1"
            info["ready"] = bool(self.conf.get("mode") == "full" and bool(key)
                                 and info["model"])
        elif name == "tts":
            info["voices"] = _voices_for(sel, row)
            info["voice"] = sel.get("voice") or (info["voices"][0]["id"]
                                                 if info["voices"] else "")
        if types is not None:
            info["types"] = [{"id": t["id"], "label": t["label"], "note": t.get("note", ""),
                              "default_base": t.get("default_base", "")} for t in types]
            info["type"] = sel.get("type") or row.get("type") or "completions"
        if name == "asr":
            info["enabled"] = (sel.get("vendor") or sel.get("provider") or "none") != "none"
            # 用户选的 none 也要在列表里；vendor 列表本身不含 none
            info["vendors"] = [{"id": "none", "label": "不启用语音输入",
                                "note": "只能在控制台打字", "default_base": "",
                                "default_models": [], "key_env": ""}] + [
                v for v in info["vendors"] if v["id"] != "none"]
            if info["vendor"] == "none":
                info["vendor"] = "none"
        if name == "llm" and not any(v["id"] == "demo" for v in info["vendors"]):
            # demo 不在预设列表里，补一项，否则下拉选不到当前值
            info["vendors"] = info["vendors"] + [
                {"id": "demo", "label": "离线演示（不联网）", "note": "台词写死，用来调形象。",
                 "default_base": "", "default_models": [], "key_env": ""}]
        return info

    # ---------------------------------------------------------------- 写
    def save(self) -> None:
        data = json.loads(json.dumps(self.conf, ensure_ascii=False))
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def update(self, body: dict) -> dict:
        changed: dict = {}
        voice = self.conf.setdefault("voice", {})
        full = voice.setdefault("full", {})
        half = voice.setdefault("half", {})

        if body.get("mode") in ("full", "half"):
            self.conf["mode"] = body["mode"]
            changed["mode"] = body["mode"]

        # 四个通道用同一套更新逻辑
        for name, key_path in (("llm", half), ("tts", half), ("asr", half), ("full", full)):
            patch = body.get(name)
            if not isinstance(patch, dict):
                continue
            sel = key_path.setdefault(name, {})
            self._apply_channel(name, sel, patch, changed)

        # agents
        ag = body.get("agents") if isinstance(body.get("agents"), dict) else {}
        aconf = self.conf.setdefault("agents", {})
        if "enabled" in ag:
            aconf["enabled"] = bool(ag["enabled"])
            changed["agents.enabled"] = bool(ag["enabled"])
        if ag.get("mode") in ("auto", "manual"):
            aconf["mode"] = ag["mode"]
            changed["agents.mode"] = ag["mode"]
        if "active" in ag:
            aconf["active"] = str(ag["active"] or "")
            changed["agents.active"] = aconf["active"]
        if "desktop" in ag:
            aconf.setdefault("desktop", {})["enabled"] = bool(ag["desktop"])
            changed["agents.desktop"] = bool(ag["desktop"])
        cli = ag.get("cli") if isinstance(ag.get("cli"), dict) else {}
        sel_cli = aconf.setdefault("cli", {})
        for aid, spec in cli.items():
            if aid not in {s["id"] for s in catalog.CLI_AGENTS} or not isinstance(spec, dict):
                continue
            row = sel_cli.setdefault(aid, {})
            if "enabled" in spec:
                row["enabled"] = bool(spec["enabled"])
                changed[f"agents.cli.{aid}.enabled"] = row["enabled"]
            if "model" in spec:
                row["model"] = str(spec["model"])
                changed[f"agents.cli.{aid}.model"] = row["model"]

        # 人设
        pn = body.get("persona") if isinstance(body.get("persona"), dict) else {}
        if pn:
            cur_p = self.conf.setdefault("persona", {})
            for key in ("name", "identity", "address", "extra"):
                if key in pn:
                    val = str(pn[key] or "").strip()
                    cur_p[key] = val
                    changed[f"persona.{key}"] = val[:20]

        self.conf["version"] = 3
        if any(not k.startswith("_") for k in changed):
            self.save()
        return changed

    def _apply_channel(self, name: str, sel: dict, patch: dict, changed: dict) -> None:
        """厂商 / 类型 / base_url / 模型列表 / 选中模型 / key。"""
        presets = {"llm": catalog.LLM_PRESETS, "tts": catalog.TTS_PROVIDERS,
                   "asr": catalog.ASR_PROVIDERS, "full": catalog.FULL_PROVIDERS}[name]
        if patch.get("vendor") and patch["vendor"] != sel.get("vendor"):
            vid = str(patch["vendor"])
            row = next((p for p in presets if p["id"] == vid), None)
            if row is None and vid != "none":
                raise ValueError(f"未知的{name}厂商 {vid!r}")
            sel["vendor"] = vid
            sel.pop("provider", None)
            changed[f"{name}.vendor"] = vid
            # 换厂商：base_url 和模型表跟着换成新的（除非用户同一请求里另给了值）
            if "base_url" not in patch and row:
                sel["base_url"] = row.get("base_url", "")
            if "models" not in patch and row:
                sel["models"] = _model_list(row.get("models") or [])
                sel["model"] = (row.get("model")
                                or (sel["models"][0]["id"] if sel["models"] else ""))
            if "type" not in patch and row and row.get("type"):
                sel["type"] = row["type"]
            # 换厂商：音色也要换成新厂商的第一个，否则会拿旧厂商的音色名去合成
            if "voice" not in patch and row and row.get("voices"):
                sel["voice"] = row["voices"][0][0]
        for key in ("base_url", "type", "model"):
            if key in patch:
                sel[key] = str(patch[key] or "").strip()
                changed[f"{name}.{key}"] = sel[key][:60]
        if "models" in patch and isinstance(patch["models"], list):
            sel["models"] = _model_list(patch["models"])
            changed[f"{name}.models"] = len(sel["models"])
            ids = {m["id"] for m in sel["models"]}
            if sel.get("model") not in ids:
                sel["model"] = (sel["models"][0]["id"] if sel["models"] else "")
                changed[f"{name}.model"] = sel["model"]
        if "voice" in patch:
            sel["voice"] = str(patch["voice"] or "")
            changed[f"{name}.voice"] = sel["voice"]
        if "api_key" in patch:
            self.set_key(name, str(patch["api_key"] or ""))
            self.write_secrets()
            changed[f"{name}.key"] = "已设置" if self.secrets.get(name) else "已清除"


def _voices_for(sel: dict, row: dict) -> list[dict]:
    """TTS 音色表：厂商自带就用它的，否则用当前 base_url 对应的通用表。"""
    if row.get("voices"):
        return [{"id": i, "label": l} for i, l in row["voices"]]
    return []
