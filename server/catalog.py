"""能力目录：这台机器上"能配什么"。

配置只认**通用接口形态**，不绑定具体服务商：

- LLM 三类：`openai`（OpenAI 兼容）、`anthropic`（Messages）、`gemini`（Google）。任何兼容
  这些接口的服务商只要填 base_url + model + key 就能用；预设只是帮你快速填表。
- TTS / ASR 也按接口形态分（OpenAI 兼容的 /audio/speech、/audio/transcriptions 能吃一堆服务商）。
- 命令行 agent 自动探测装没装、各自支持哪些模型。

探测结果有缓存（CLI 的模型列表要起子进程，`opencode models` 约 2 秒），页面打开时不会每次重扫。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import threading
import time

CACHE_TTL = 300.0          # 模型列表缓存时间（秒）
_cache: dict = {}
_lock = threading.Lock()


def _cached(key: str, build, ttl: float = CACHE_TTL):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _run(cmd: list[str], timeout: float = 20) -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags,
                           env={**os.environ, "NO_COLOR": "1"})
        return (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- 音色

QWEN_VOICES = [
    ("qiaoxiaojiao_v3.1", "俏小娇 · 幼态活泼（默认）"),
    ("longanqian_v3.1", "龙安茜 · 偏高少女音"),
    ("longanqian", "龙安茜 · 旧版"),
    ("xiaxiaochen_v3.1", "夏小晨 · 偏高音"),
    ("anxiaolan_v3.1", "安小岚 · 偏高音"),
    ("longhuohuo_v3.6", "火火 · 最高音，活泼"),
    ("longpaopao_v3.6", "泡泡 · 语速偏慢、软"),
    ("longanxiaoxin", "小欣"),
    ("longjielidou_v3.6", "结粒豆"),
    ("longanlingxin", "灵心"),
    ("longanlingxi", "灵犀 · 最低音"),
    ("yuxiaoyun_v3.1", "于晓云"),
    ("wenhuaiqing_v3.1", "温怀清"),
    ("xieshurou_v3.1", "谢书柔"),
    ("baiqinglan_v3.1", "白清岚"),
    ("longanfengyue", "龙安风月"),
    ("longanyuanfei", "龙安远飞"),
    ("longanhuan_v3.6", "龙安欢"),
    ("longchuanshu_v3.6", "龙川舒"),
    ("longanlufeng", "龙安陆风"),
    ("anmingyuan_v3.1", "安明远"),
    ("loongmary", "Loong Mary"),
    ("loongeva_v3.6", "Loong Eva"),
    ("loongjohn", "Loong John"),
    ("daniel", "Daniel"),
    ("echo", "Echo"),
    ("hannah", "Hannah"),
    ("sherry", "Sherry"),
]

EDGE_VOICES = [
    ("zh-CN-XiaoyiNeural", "晓伊 · 活泼少女"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 温和"),
    ("zh-CN-YunxiNeural", "云希 · 男声，阳光"),
    ("zh-CN-YunxiaNeural", "云夏 · 男声，偏童声"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 东北话"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 陕西话"),
]

SAPI_VOICES = [
    ("Microsoft Huihui Desktop", "慧慧 · 中文女声（系统自带）"),
    ("Microsoft Yaoyao", "瑶瑶 · 中文女声（装了语言包才有）"),
    ("Microsoft Zira Desktop", "Zira · 英文女声"),
    ("Microsoft David Desktop", "David · 男声"),
]

OPENAI_TTS_VOICES = [
    ("nova", "nova · 明亮女声"),
    ("shimmer", "shimmer · 柔和女声"),
    ("alloy", "alloy · 中性"),
    ("fable", "fable · 叙事感"),
    ("onyx", "onyx · 低沉男声"),
    ("echo", "echo · 男声"),
]

# ---------------------------------------------------------------- 全双工

FULL_PROVIDERS = [
    {
        "id": "qwen",
        "label": "Qwen Audio Realtime",
        "models": ["qwen-audio-3.1-realtime-flash"],
        "key_env": "DASHSCOPE_API_KEY",
        "voices": QWEN_VOICES,
        "note": "音频级全双工：随时能听、随时打断。目前只有这一家。",
    },
]

# ---------------------------------------------------------------- LLM：按接口形态分
#
# 只认三种"线协议"，服务商只是填表用的预设：
#   completions  /chat/completions（OpenAI 兼容，最普遍）
#   responses    /v1/responses（OpenAI Responses API）
#   messages     Anthropic Messages（Claude）
# 其他不兼容这几类的（如 Google 原生 generateContent）走单独的适配器。
LLM_TYPES = [
    {
        "id": "completions", "label": "Chat Completions（OpenAI 兼容）",
        "fields": ["base_url", "model", "api_key_env"],
        "default_base": "https://api.deepseek.com",
        "note": "最常见：DeepSeek、通义、Kimi、智谱、硅基流动、Ollama、vLLM、LM Studio、one-api…",
    },
    {
        "id": "responses", "label": "Responses API",
        "fields": ["base_url", "model", "api_key_env"],
        "default_base": "https://api.openai.com/v1",
        "note": "OpenAI 新的 /responses 接口；部分网关也开始支持。",
    },
    {
        "id": "messages", "label": "Messages（Anthropic）",
        "fields": ["model", "api_key_env"],
        "default_base": "https://api.anthropic.com",
        "note": "Claude 官方接口（需要 pip install anthropic）。",
    },
    {
        "id": "generateContent", "label": "generateContent（Google 原生）",
        "fields": ["model", "api_key_env"],
        "default_base": "https://generativelanguage.googleapis.com",
        "note": "Google Gemini 原生接口，不属于上面三类，单独适配。",
    },
]

# 只是帮页面快速填表的预设，配置里存的是 type + base_url + model + api_key_env
# ---------------------------------------------------------------- 通道配置
#
# 每个通道（LLM / TTS / ASR / 全双工）都是同一套形状，页面上就是
# 厂商 + Base URL + API Key + 模型列表（可增删、选一个启用）：
#
#   {"vendor": "deepseek", "type": "completions", "base_url": "...",
#    "models": [{"id": "deepseek-chat", "label": "deepseek-chat"}], "model": "deepseek-chat"}
#
# key 不写进 config.json，按通道存在 state/secrets.json 里。

def _models(*ids: str) -> list[dict]:
    return [{"id": i, "label": i} for i in ids]


LLM_PRESETS = [
    {"id": "deepseek", "label": "DeepSeek", "type": "completions",
     "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
     "api_key_env": "DEEPSEEK_API_KEY", "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "qwen", "label": "通义千问", "type": "completions",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
     "api_key_env": "DASHSCOPE_API_KEY",
     "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"]},
    {"id": "kimi", "label": "Kimi", "type": "completions",
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
     "api_key_env": "MOONSHOT_API_KEY",
     "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k3"]},
    {"id": "zhipu", "label": "智谱 GLM", "type": "completions",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus",
     "api_key_env": "ZHIPU_API_KEY", "models": ["glm-4-plus", "glm-4-flash"]},
    {"id": "siliconflow", "label": "硅基流动", "type": "completions",
     "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen2.5-72B-Instruct",
     "api_key_env": "SILICONFLOW_API_KEY",
     "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3"]},
    {"id": "openai", "label": "OpenAI", "type": "responses",
     "base_url": "https://api.openai.com/v1", "model": "gpt-5",
     "api_key_env": "OPENAI_API_KEY", "models": ["gpt-5", "gpt-5-mini", "gpt-4.1"]},
    {"id": "claude", "label": "Claude API", "type": "messages",
     "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5",
     "api_key_env": "ANTHROPIC_API_KEY",
     "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]},
    {"id": "gemini", "label": "Google Gemini", "type": "generateContent",
     "base_url": "https://generativelanguage.googleapis.com", "model": "gemini-2.5-flash",
     "api_key_env": "GEMINI_API_KEY",
     "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    {"id": "ollama", "label": "Ollama（本机）", "type": "completions",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b", "api_key_env": "",
     "models": ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b"]},
    {"id": "demo", "label": "离线演示", "type": "demo",
     "base_url": "", "model": "", "api_key_env": "",
     "models": [], "note": "不联网、不用 key，台词写死，用来调形象。"},
]

# ---------------------------------------------------------------- TTS

TTS_PROVIDERS = [
    {
        "id": "qwen-realtime", "label": "Qwen 实时音色", "key_env": "DASHSCOPE_API_KEY",
        "voices": QWEN_VOICES,
        "note": "用实时模型朗读，和全双工语音是同一个声音。",
    },
    {
        "id": "edge", "label": "Edge 在线语音", "key_env": "",
        "voices": EDGE_VOICES,
        "note": "微软 Edge 的在线音色，免 key，音色多。",
    },
    {
        "id": "openai", "label": "OpenAI 兼容 /audio/speech", "key_env": "OPENAI_API_KEY",
        "voices": OPENAI_TTS_VOICES,
        "base_url_field": True,
        "note": "OpenAI、硅基流动、Groq、自建 one-api 等只要是这个接口形态都能用，填 base_url 即可。",
    },
    {
        "id": "sapi", "label": "Windows 本机语音", "key_env": "",
        "voices": SAPI_VOICES,
        "note": "离线可用，但中文只有慧慧，比较机械。",
    },
]

# ---------------------------------------------------------------- ASR

ASR_PROVIDERS = [
    {
        "id": "none", "label": "不启用语音输入", "key_env": "", "models": [],
        "note": "只能在控制台打字；麦克风按钮会禁用。",
    },
    {
        "id": "qwen", "label": "Qwen 语音识别", "key_env": "DASHSCOPE_API_KEY",
        "models": ["qwen3-asr-flash"],
        "note": "浏览器录音 → 服务端转写，不依赖浏览器自带的语音识别。",
    },
    {
        "id": "openai", "label": "OpenAI 兼容 /audio/transcriptions", "key_env": "OPENAI_API_KEY",
        "models": ["whisper-1", "whisper-large-v3"],
        "base_url_field": True,
        "note": "OpenAI Whisper、Groq、硅基流动等只要是这个接口形态都能用，填 base_url 即可。",
    },
]

KEY_FIELDS = [
    ("DASHSCOPE_API_KEY", "通义千问 / 全双工语音 / Qwen 识别 / Qwen 音色"),
    ("DEEPSEEK_API_KEY", "DeepSeek"),
    ("MOONSHOT_API_KEY", "Kimi"),
    ("ZHIPU_API_KEY", "智谱 GLM"),
    ("SILICONFLOW_API_KEY", "硅基流动"),
    ("OPENAI_API_KEY", "OpenAI / OpenAI 兼容的语音服务"),
    ("ANTHROPIC_API_KEY", "Claude（Anthropic）"),
    ("GEMINI_API_KEY", "Google Gemini"),
]


def provider_table(kind: str) -> list[dict]:
    return {"full": FULL_PROVIDERS, "tts": TTS_PROVIDERS, "asr": ASR_PROVIDERS,
            "llm_type": LLM_TYPES, "llm_preset": LLM_PRESETS}[kind]


def provider(kind: str, pid: str) -> dict:
    for p in provider_table(kind):
        if p["id"] == pid:
            return p
    return {}


def voices_for(tts_provider: str) -> list[dict]:
    p = provider("tts", tts_provider)
    return [{"id": i, "label": l} for i, l in p.get("voices") or QWEN_VOICES]


def voice_ids(tts_provider: str) -> set:
    return {v["id"] for v in voices_for(tts_provider)}


# ---------------------------------------------------------------- CLI agent

CLI_AGENTS = [
    {
        "id": "claude", "label": "Claude Code", "bin": "claude",
        "model_flag": ["--model"], "version_flag": ["--version"],
        "model_source": "claude_settings",
        "builtin_models": ["opus", "sonnet", "haiku"],
        "default_model": "",
        "note": "一次 -p 调用，7 秒左右。",
    },
    {
        "id": "codex", "label": "Codex", "bin": "codex",
        "model_flag": ["-m"], "version_flag": ["--version"],
        "model_source": "codex_catalog",
        "builtin_models": [],
        "default_model": "",
        "note": "一次 exec 调用，20~60 秒。",
    },
    {
        "id": "opencode", "label": "OpenCode", "bin": "opencode",
        "model_flag": ["-m"], "version_flag": ["--version"],
        "model_source": "opencode",
        "builtin_models": ["opencode/deepseek-v4.1-flash-free"],
        "default_model": "opencode/deepseek-v4.1-flash-free",
        "note": "免费模型不用登录；输出带 ANSI，已自动剥掉。",
    },
]


def _claude_models() -> list[dict]:
    out: list[dict] = []
    cfg = pathlib.Path.home() / ".claude" / "settings.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        for opt in (data.get("modelPicker") or {}).get("options") or []:
            mid = opt.get("model") or ""
            if mid:
                out.append({"id": mid, "label": opt.get("label") or mid})
    except (OSError, ValueError):
        pass
    for alias in ("opus", "sonnet", "haiku"):
        if not any(m["id"] == alias for m in out):
            out.append({"id": alias, "label": f"{alias}（别名）"})
    return out


def _codex_models() -> list[dict]:
    out: list[dict] = []
    home = pathlib.Path.home() / ".codex"
    try:
        cat = json.loads((home / "cc-switch-model-catalog.json").read_text(encoding="utf-8"))
        for m in cat.get("models") or []:
            slug = m.get("slug") or ""
            if slug:
                out.append({"id": slug, "label": m.get("display_name") or slug})
    except (OSError, ValueError):
        pass
    current = ""
    try:
        for line in (home / "config.toml").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("model ") or s.startswith("model="):
                current = s.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        pass
    if current and not any(m["id"] == current for m in out):
        out.insert(0, {"id": current, "label": f"{current}（当前默认）"})
    return out


def _opencode_models(path: str = "") -> list[dict]:
    exe = path or shutil.which("opencode") or "opencode"
    text = _run([exe, "models"], timeout=40)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or " " in line or line.startswith("-"):
            continue
        if "/" in line:
            out.append({"id": line, "label": line})
    return out


_MODEL_SOURCES = {"claude_settings": _claude_models, "codex_catalog": _codex_models}


def _models_for(spec: dict, path: str) -> list[dict]:
    if spec["model_source"] == "opencode":
        return _opencode_models(path)
    return _MODEL_SOURCES[spec["model_source"]]()


def cli_status(spec: dict, refresh: bool = False) -> dict:
    """一个 CLI agent 的现状：装没装、版本、能选哪些模型。"""
    bin_name = spec["bin"]
    path = shutil.which(bin_name)
    info = {"id": spec["id"], "label": spec["label"], "bin": bin_name,
            "note": spec.get("note", ""), "installed": bool(path), "path": path or "",
            "version": "", "models": [], "default_model": spec.get("default_model", ""),
            "reason": "" if path else f"没找到命令 {bin_name}，请先安装并在 PATH 里"}
    if not path:
        return info
    if refresh:
        clear_cache()

    def build():
        ver = _run([path] + spec.get("version_flag", ["--version"]), timeout=20)
        ver = (ver.strip().splitlines() or [""])[0][:60]
        return ver, _models_for(spec, path)

    try:
        ver, models = _cached(f"cli:{spec['id']}", build)
    except Exception:                     # 探测失败不能让页面打不开
        ver, models = "", []
    info["version"] = ver
    info["models"] = models or [{"id": m, "label": m} for m in spec.get("builtin_models", [])]
    if not info["default_model"] and info["models"]:
        info["default_model"] = info["models"][0]["id"]
    return info


def agents_catalog(refresh: bool = False) -> list[dict]:
    return [cli_status(spec, refresh) for spec in CLI_AGENTS]


def cli_spec(agent_id: str) -> dict:
    for spec in CLI_AGENTS:
        if spec["id"] == agent_id:
            return spec
    return {}


def apply_model(cmd: list[str], flag: list[str], model: str) -> list[str]:
    """把 `--model X` 塞进命令行：已经有这个开关就替换它的值，没有就插到子命令之后。"""
    if not model:
        return list(cmd)
    cmd = list(cmd)
    for i, token in enumerate(cmd):
        if token in flag or any(token.startswith(f + "=") for f in flag):
            if "=" in token:
                cmd[i] = f"{token.split('=', 1)[0]}={model}"
            elif i + 1 < len(cmd):
                cmd[i + 1] = model
            else:
                cmd.append(model)
            return cmd
    insert_at = 1
    subcommands = {"run", "exec", "e", "review"}
    for i, token in enumerate(cmd[1:], start=1):
        if token in subcommands:
            insert_at = i + 1
            break
        if token.startswith("-"):
            break
        insert_at = i + 1
    return cmd[:insert_at] + list(flag) + [model] + cmd[insert_at:]
