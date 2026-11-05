"""页面设置：三层配置的读写。

- **普通选项**（模式、全双工、半双工 LLM/TTS/ASR、每个 CLI 用哪个模型）写回
  ``config.json``（新结构，``version: 3``）。
- **key** 只写 ``state/secrets.json``（``state/`` 已 gitignore，永不进仓库），
  写进去后立刻装进环境变量；``config.json`` 里始终只有 ``api_key_env`` 这种名字。

规则：环境里已有的 key 不动；页面上填过就以页面为准；空串表示"删掉这个 key"，
**键不存在表示"不改"**（避免点一次保存就把没碰过的 key 清空）。
"""
from __future__ import annotations

import json
import os
import pathlib

from . import catalog

SECRETS_NAME = "secrets.json"


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}…{value[-4:]}"


class Settings:
    def __init__(self, conf: dict, config_path: pathlib.Path, state_dir: pathlib.Path) -> None:
        self.conf = conf
        self.config_path = pathlib.Path(config_path)
        self.secrets_path = pathlib.Path(state_dir) / SECRETS_NAME
        self.secrets: dict[str, str] = self._read_secrets()
        self.apply_secrets()

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

    def apply_secrets(self) -> None:
        """启动时把本地保存的 key 装进环境变量；外部已经设了的优先。"""
        for name, value in self.secrets.items():
            if value and not os.environ.get(name):
                os.environ[name] = value

    def set_key(self, name: str, value: str) -> None:
        value = (value or "").strip()
        if value:
            os.environ[name] = value          # 立即生效
            self.secrets[name] = value
        else:
            os.environ.pop(name, None)
            self.secrets.pop(name, None)

    def key_status(self) -> list[dict]:
        out = []
        for name, label in catalog.KEY_FIELDS:
            val = os.environ.get(name, "")
            out.append({"env": name, "label": label, "set": bool(val),
                        "hint": mask(val), "saved": bool(self.secrets.get(name))})
        return out

    def _key_ok(self, env: str, keyless: bool = False) -> bool:
        if keyless:
            return True
        if not env:
            return True
        return bool(os.environ.get(env))

    # ---------------------------------------------------------------- 读
    def snapshot(self, runtime: dict | None = None) -> dict:
        """设置页要的全量数据。``runtime`` 是 configure.resolve 出来的实际运行状态。"""
        voice = self.conf.get("voice") or {}
        full = voice.get("full") or {}
        half = voice.get("half") or {}
        llm = half.get("llm") or {}
        tts = half.get("tts") or {}
        asr = half.get("asr") or {}
        agents = self.conf.get("agents") or {}
        cli_sel = agents.get("cli") or {}
        runtime = runtime or {}
        backends = runtime.get("agents", {}).get("backends") or []

        preset_id = llm.get("preset") or llm.get("type") or "openai"
        tts_id = tts.get("provider") or "qwen-realtime"
        asr_id = asr.get("provider") or "none"

        clis = []
        for spec in catalog.CLI_AGENTS:
            status = catalog.cli_status(spec)
            sel = cli_sel.get(spec["id"]) or {}
            status["model"] = sel["model"] if "model" in sel else ""
            status["auto_model"] = status.get("default_model") or ""
            status["enabled"] = sel.get("enabled", True) is not False
            status["active"] = bool(status["enabled"] and status["installed"]
                                    and spec["id"] in backends)
            clis.append(status)

        mode = self.conf.get("mode") or "half"
        agent_mode = agents.get("mode") or "auto"

        return {
            "version": self.conf.get("version", 3),
            "mode": mode,                       # full | half，互斥
            "modes": [
                {"id": "full", "label": "全双工", "desc": "音频进、音频出，随时插话"},
                {"id": "half", "label": "半双工", "desc": "打字或说话 → LLM → TTS 念出来"},
            ],
            "full": {
                "providers": [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                               "models": p["models"], "key_env": p["key_env"],
                               "available": self._key_ok(p["key_env"])}
                              for p in catalog.FULL_PROVIDERS],
                "provider": full.get("provider") or "qwen",
                "model": full.get("model") or "qwen-audio-3.1-realtime-flash",
                "voice": full.get("voice") or "qiaoxiaojiao_v3.1",
                "voices": [{"id": i, "label": l} for i, l in catalog.QWEN_VOICES],
                "key_env": full.get("api_key_env") or "DASHSCOPE_API_KEY",
                "ready": self._key_ok(full.get("api_key_env") or "DASHSCOPE_API_KEY"),
            },
            "llm": {
                "type": llm.get("type") or "demo",
                "types": [{"id": t["id"], "label": t["label"], "note": t.get("note", ""),
                           "default_base": t.get("default_base", "")} for t in catalog.LLM_TYPES],
                "preset": preset_id,
                "presets": [{"id": p["id"], "label": p["label"], "type": p["type"],
                             "base_url": p.get("base_url", ""), "model": p.get("model", ""),
                             "api_key_env": p.get("api_key_env", ""),
                             "models": p.get("models") or [], "note": p.get("note", "")}
                            for p in catalog.LLM_PRESETS],
                "base_url": llm.get("base_url") or "",
                "model": llm.get("model") or "",
                "api_key_env": llm.get("api_key_env") or "",
                "available": llm.get("type") in ("demo", None) or self._key_ok(
                    llm.get("api_key_env") or ""),
            },
            "tts": {
                "provider": tts_id,
                "voice": tts.get("voice") or "",
                "base_url": tts.get("base_url") or "",
                "rate": int(tts.get("rate", 0)),
                "volume": int(tts.get("volume", 100)),
                "providers": [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                               "key_env": p["key_env"], "base_url_field": bool(p.get("base_url_field")),
                               "voices": [{"id": i, "label": l} for i, l in p["voices"]],
                               "available": self._key_ok(p["key_env"], p["id"] in ("edge", "sapi"))}
                              for p in catalog.TTS_PROVIDERS],
                "voices": catalog.voices_for(tts_id),
            },
            "asr": {
                "provider": asr_id,
                "model": asr.get("model") or "",
                "base_url": asr.get("base_url") or "",
                "providers": [{"id": p["id"], "label": p["label"], "note": p.get("note", ""),
                               "key_env": p["key_env"], "models": p["models"],
                               "base_url_field": bool(p.get("base_url_field")),
                               "available": self._key_ok(p["key_env"], p["id"] == "none")}
                              for p in catalog.ASR_PROVIDERS],
                "enabled": asr_id != "none",
            },
            "agents": {
                "enabled": bool(agents.get("enabled", True)),
                "mode": agent_mode,             # auto | manual
                "active": agents.get("active") or "",
                "max_concurrent": int(agents.get("max_concurrent", 2)),
                "desktop": bool((agents.get("desktop") or {}).get("enabled", True)),
                "cli": clis,
                "backends": backends,
            },
            "keys": self.key_status(),
            "secrets_path": str(self.secrets_path),
        }

    # ---------------------------------------------------------------- 写
    def save(self) -> None:
        """把配置写回 config.json（永不含 key 明文）。"""
        data = json.loads(json.dumps(self.conf, ensure_ascii=False))
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def update(self, body: dict) -> dict:
        """按页面提交的内容就地更新，返回改动摘要（不含任何 key 明文）。"""
        changed: dict = {}
        voice = self.conf.setdefault("voice", {})
        full = voice.setdefault("full", {})
        half = voice.setdefault("half", {})

        # 模式：全双工 / 半双工二选一
        if body.get("mode") in ("full", "half"):
            self.conf["mode"] = body["mode"]
            changed["mode"] = body["mode"]

        # 全双工
        f = body.get("full") if isinstance(body.get("full"), dict) else {}
        if f.get("provider"):
            pid = str(f["provider"])
            row = catalog.provider("full", pid)
            if not row:
                raise ValueError(f"未知的全双工服务商 {pid!r}")
            full["provider"] = pid
            full["api_key_env"] = row.get("key_env", "")
            if f.get("model"):
                full["model"] = str(f["model"])
            elif row.get("models"):
                full["model"] = row["models"][0]
            changed["full.provider"] = pid
        elif f.get("model"):
            full["model"] = str(f["model"])
            changed["full.model"] = full["model"]
        if f.get("voice"):
            full["voice"] = str(f["voice"])
            changed["full.voice"] = full["voice"]

        # 半双工 LLM：只存通用四件套
        llm = body.get("llm") if isinstance(body.get("llm"), dict) else {}
        cur = half.setdefault("llm", {})
        if llm.get("preset") and llm.get("preset") != "custom":
            pid = str(llm["preset"])
            row = next((p for p in catalog.LLM_PRESETS if p["id"] == pid), None)
            if row is None:
                raise ValueError(f"未知的预设 {pid!r}")
            models = row.get("models") or []
            # 预设自带模型表就以它为准（没表的如 demo 清空），避免表单里的旧值混进来
            model = (str(llm.get("model") or "") if models else "") or (
                row.get("model", "") if models else "")
            cur.update({"type": row["type"], "base_url": row.get("base_url", ""),
                        "model": model, "api_key_env": row.get("api_key_env", ""),
                        "preset": pid})
            changed["llm.preset"] = pid
            changed["llm.type"] = row["type"]
            changed["llm.model"] = cur["model"]
        else:
            for key in ("type", "base_url", "model", "api_key_env"):
                if key in llm:
                    cur[key] = str(llm[key] or "")
                    changed[f"llm.{key}"] = cur[key]
            if llm.get("model") is not None:
                cur["preset"] = cur.get("preset") or "custom"

        # TTS
        tts = body.get("tts") if isinstance(body.get("tts"), dict) else {}
        cts = half.setdefault("tts", {})
        if tts.get("provider"):
            pid = str(tts["provider"])
            row = catalog.provider("tts", pid)
            if not row:
                raise ValueError(f"未知的朗读引擎 {pid!r}")
            cts["provider"] = pid
            voices = [i for i, _ in row.get("voices") or []]
            want = str(tts.get("voice") or "")
            cts["voice"] = want if want in voices else (voices[0] if voices else "")
            cts["api_key_env"] = row.get("key_env", "")
            changed["tts.provider"] = pid
            changed["tts.voice"] = cts["voice"]
        elif tts.get("voice"):
            cts["voice"] = str(tts["voice"])
            changed["tts.voice"] = cts["voice"]
        if "base_url" in tts:
            cts["base_url"] = str(tts["base_url"] or "")
            changed["tts.base_url"] = cts["base_url"]

        # ASR
        asr = body.get("asr") if isinstance(body.get("asr"), dict) else {}
        cas = half.setdefault("asr", {})
        if asr.get("provider"):
            pid = str(asr["provider"])
            row = catalog.provider("asr", pid)
            if not row:
                raise ValueError(f"未知的语音识别 {pid!r}")
            cas["provider"] = pid
            cas["model"] = asr.get("model") or (row.get("models") or [""])[0]
            cas["api_key_env"] = row.get("key_env", "")
            changed["asr.provider"] = pid
            changed["asr.model"] = cas["model"]
        elif asr.get("model"):
            cas["model"] = str(asr["model"])
            changed["asr.model"] = cas["model"]
        if "base_url" in asr:
            cas["base_url"] = str(asr["base_url"] or "")
            changed["asr.base_url"] = cas["base_url"]

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
        sel = aconf.setdefault("cli", {})
        for aid, spec in cli.items():
            if aid not in {s["id"] for s in catalog.CLI_AGENTS} or not isinstance(spec, dict):
                continue
            row = sel.setdefault(aid, {})
            if "enabled" in spec:
                row["enabled"] = bool(spec["enabled"])
                changed[f"agents.cli.{aid}.enabled"] = row["enabled"]
            if "model" in spec:
                row["model"] = str(spec["model"])
                changed[f"agents.cli.{aid}.model"] = row["model"]

        # key
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        touched = []
        for name, value in keys.items():
            if name not in dict(catalog.KEY_FIELDS):
                continue
            self.set_key(name, str(value or ""))
            touched.append(name if value else f"-{name}")
        if touched:
            self.write_secrets()
            changed["keys"] = touched

        self.conf["version"] = 3
        if any(k != "keys" for k in changed):
            self.save()
        return changed
