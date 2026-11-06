"""页面设置：通道化配置的读写。

四个通道形状一致：**接口类型 + Base URL + API Key + 模型列表（选一个）**

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

from . import catalog, configure, persona

SECRETS_NAME = "secrets.json"
CHANNELS = ("llm", "tts", "asr", "full")


_TYPE_ALIAS = {"openai": "completions", "anthropic": "messages", "gemini": "generateContent"}


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
        voice = self.conf.get("voice") or {}
        half = voice.get("half") or {}

        return {
            "version": self.conf.get("version", configure.SCHEMA_VERSION),
            "mode": self.conf.get("mode") or "half",
            "llm": self._channel("llm", half.get("llm") or {}, catalog.LLM_PRESETS,
                                 catalog.LLM_TYPES),
            "tts": self._channel("tts", half.get("tts") or {}, catalog.TTS_PROVIDERS, None),
            "asr": self._channel("asr", half.get("asr") or {}, catalog.ASR_PROVIDERS, None),
            "full": self._channel("full", voice.get("full") or {}, catalog.FULL_PROVIDERS, None),
            "persona": persona.snapshot(self.conf.get("persona")),
            "persona_rules": _persona_rules(),
            "secrets_path": str(self.secrets_path),
        }

    def _channel(self, name: str, sel: dict, presets: list[dict],
                 types: list[dict] | None = None) -> dict:
        """一个通道的展示数据。

        LLM 是通用接口：只有 API 格式 + Base URL + Key + 模型列表，没有"厂商"这一层。
        TTS / ASR / 全双工按各自适配器（厂商）分。
        """
        generic = name in ("llm", "tts", "asr")
        iface = ""
        if name in ("tts", "asr"):
            iface = (sel.get("type") or sel.get("vendor") or sel.get("provider")
                     or ("qwen-realtime" if name == "tts" else "none")).strip()
        vendor = "" if generic else (sel.get("vendor") or sel.get("provider") or "").strip()
        row = ({} if name == "llm" else
               next((p for p in presets if p["id"] == (iface or vendor)), {}))
        models = _model_list(sel.get("models")) or _model_list(row.get("models") or [])
        model = (sel.get("model") or "").strip()
        key = self.key_for(name)
        base_url = (sel.get("base_url") or row.get("base_url") or "").strip()
        kind = (sel.get("type") or row.get("type") or "completions").strip()
        kind = _TYPE_ALIAS.get(kind, kind)
        if name in ("tts", "asr"):
            kind = iface
            if not (sel.get("base_url") or "").strip():
                base_url = (row.get("default_base") or "").strip()
        # 列表是空的、但 Base URL + Key 都填了：自动拉一次作为起点（之后以用户保存的为准）
        if generic and not models and base_url and kind != "demo":
            models = catalog.fetch_models(base_url, key, kind)
        if model and model not in {m["id"] for m in models}:
            models.insert(0, {"id": model, "label": model})
        if not model and models:
            model = models[0]["id"]
        info = {
            "base_url": base_url,
            "models": models,
            "model": model,
            "has_key": bool(key),
            "key_hint": mask(key),
        }
        if name in ("tts", "asr"):
            info["type"] = iface
            info["local"] = bool(row.get("local"))
            if name == "asr":
                info["enabled"] = iface != "none"
            info["types"] = [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                              "default_base": p.get("default_base", ""),
                              "default_models": _model_list(p.get("models") or []),
                              "local": bool(p.get("local")),
                              "voices": [{"id": i, "label": l} for i, l in p.get("voices") or []],
                              "voices_by_model": {m: [{"id": i, "label": l} for i, l in tbl]
                                                  for m, tbl in (p.get("voices_by_model") or {}).items()},
                              } for p in presets]
        if name == "llm":
            ex = sel.get("extra")
            info["extra"] = ex if isinstance(ex, dict) else {}
        if not generic:
            info["vendor"] = vendor or (presets[0]["id"] if presets else "")
            info["vendors"] = [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                                "default_base": p.get("base_url", ""),
                                "default_models": _model_list(p.get("models") or []),
                                "key_env": p.get("key_env", "")} for p in presets]
        if name == "full":
            info["voices"] = [{"id": i, "label": l} for i, l in catalog.QWEN_VOICES]
            info["voice"] = sel.get("voice") or "qiaoxiaojiao_v3.1"
        elif name == "tts":
            # 音色跟着**模型**走
            info["voices"] = _voices_for(model, row)
            info["voice"] = sel.get("voice") or (info["voices"][0]["id"] if info["voices"] else "")
        if types is not None and name == "llm":
            info["types"] = [{"id": t["id"], "label": t["label"], "note": t.get("note", ""),
                              "default_base": t.get("default_base", "")}
                             for t in types if t["id"] != "demo"]
            if name == "llm":
                info["types"].append({"id": "demo", "label": "离线演示（不联网）",
                                      "note": "不需要任何配置，只会说几句固定的话。", "default_base": ""})
            info["type"] = kind
        return info

    def fetch_models(self, channel: str, body: dict) -> dict:
        """页面上的"拉取模型"：用表单里还没保存的 Base URL / Key 直接去问服务。"""
        if channel not in ("llm", "tts", "asr"):
            raise ValueError("这个通道不支持拉取模型列表")
        sel = ((self.conf.get("voice") or {}).get("half") or {}).get(channel) or {}
        kind = "completions"
        if channel == "llm":
            kind = _TYPE_ALIAS.get(body.get("type") or sel.get("type") or "completions",
                                   body.get("type") or sel.get("type") or "completions")
        base = (body.get("base_url") if body.get("base_url") is not None else sel.get("base_url")) or ""
        key = body.get("api_key") or self.key_for(channel)
        models, error = catalog.fetch_models_ex(base, key, kind)
        return {"ok": bool(models), "models": models, "error": error}

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

        self.conf.pop("agents", None)             # 动作能力已移除

        # 人设
        pn = body.get("persona") if isinstance(body.get("persona"), dict) else {}
        if pn:
            cur_p = self.conf.setdefault("persona", {})
            for key in ("name", "identity", "address", "extra"):
                if key in pn:
                    val = str(pn[key] or "").strip()
                    cur_p[key] = val
                    changed[f"persona.{key}"] = val[:20]

        self.conf["version"] = configure.SCHEMA_VERSION
        if any(not k.startswith("_") for k in changed):
            self.save()
        return changed

    def _apply_channel(self, name: str, sel: dict, patch: dict, changed: dict) -> None:
        """厂商 / 类型 / base_url / 模型列表 / 选中模型 / key。"""
        presets = {"llm": [], "tts": catalog.TTS_PROVIDERS,
                   "asr": catalog.ASR_PROVIDERS, "full": catalog.FULL_PROVIDERS}[name]
        if name == "llm":
            sel.pop("vendor", None)         # LLM 是通用接口，不记厂商
            sel.pop("provider", None)
            new_type = _TYPE_ALIAS.get(patch.get("type"), patch.get("type"))
            old_type = _TYPE_ALIAS.get(sel.get("type"), sel.get("type"))
            if new_type and new_type != old_type and "base_url" not in patch:
                defaults = {t["id"]: t.get("default_base", "") for t in catalog.LLM_TYPES}
                cur = (sel.get("base_url") or "").strip()
                if not cur or cur == defaults.get(old_type, None):
                    sel["base_url"] = defaults.get(new_type, "")
        elif name in ("tts", "asr"):
            old_type = (sel.get("type") or sel.get("vendor") or sel.get("provider") or "").strip()
            sel.pop("vendor", None)
            sel.pop("provider", None)
            if old_type:
                sel["type"] = old_type
            new_type = str(patch.get("type") or patch.get("vendor") or "").strip()
            if new_type and new_type != old_type:
                row = next((p for p in presets if p["id"] == new_type), None)
                if row is None:
                    raise ValueError(f"未知的{name}接口 {new_type!r}")
                old_row = next((p for p in presets if p["id"] == old_type), {})
                sel["type"] = new_type
                changed[f"{name}.type"] = new_type
                # Base URL 空着、或者还是旧接口的默认值 → 换成新接口的；用户自己填的不动
                if "base_url" not in patch:
                    cur = (sel.get("base_url") or "").strip()
                    if not cur or cur == (old_row.get("default_base") or "").strip():
                        sel["base_url"] = row.get("default_base", "")
                # 模型表、音色都是跟接口走的：换接口就换成新接口的默认
                if "models" not in patch:
                    sel["models"] = _model_list(row.get("models") or [])
                    sel["model"] = sel["models"][0]["id"] if sel["models"] else ""
                if "voice" not in patch and row.get("voices"):
                    sel["voice"] = row["voices"][0][0]
        elif patch.get("vendor") and patch["vendor"] != sel.get("vendor"):
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
            if key in patch and not (key == "type" and name in ("tts", "asr")):
                sel[key] = str(patch[key] or "").strip()
                changed[f"{name}.{key}"] = sel[key][:60]
        if "models" in patch and isinstance(patch["models"], list):
            sel["models"] = _model_list(patch["models"])
            changed[f"{name}.models"] = len(sel["models"])
            ids = {m["id"] for m in sel["models"]}
            if sel.get("model") not in ids:
                sel["model"] = (sel["models"][0]["id"] if sel["models"] else "")
                changed[f"{name}.model"] = sel["model"]
        if "extra" in patch and name == "llm":
            sel["extra"] = _parse_extra(patch["extra"])
            changed["llm.extra"] = json.dumps(sel["extra"], ensure_ascii=False)[:60] or "{}"
        if "voice" in patch:
            sel["voice"] = str(patch["voice"] or "")
            changed[f"{name}.voice"] = sel["voice"]
        if "api_key" in patch:
            self.set_key(name, str(patch["api_key"] or ""))
            self.write_secrets()
            changed[f"{name}.key"] = "已设置" if self.secrets.get(name) else "已清除"


def _parse_extra(raw) -> dict:
    """额外请求参数：接受对象或 JSON 字符串；空 = 清掉。写错了抛 ValueError，不落盘。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            raise ValueError(f"额外请求参数不是合法的 JSON：{e}") from e
    if not isinstance(raw, dict):
        raise ValueError("额外请求参数必须是一个 JSON 对象，比如 {\"enable_thinking\": false}")
    banned = {"model", "messages", "input", "stream", "system", "instructions"}
    bad = banned & set(raw)
    if bad:
        raise ValueError("额外请求参数不能覆盖这些字段：" + "、".join(sorted(bad)))
    return raw


def _voices_for(model: str, row: dict) -> list[dict]:
    """TTS 音色表：按**模型**取，取不到才退回该厂商的通用表。"""
    by_model = row.get("voices_by_model") or {}
    table = by_model.get(model) or by_model.get("*") or row.get("voices") or []
    return [{"id": i, "label": l} for i, l in table]
