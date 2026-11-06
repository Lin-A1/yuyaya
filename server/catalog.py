"""能力目录：这台机器上"能配什么"。

配置只认**通用接口形态**，不绑定具体服务商：

- LLM 三类：`openai`（OpenAI 兼容）、`anthropic`（Messages）、`gemini`（Google）。任何兼容
  这些接口的服务商只要填 base_url + model + key 就能用；预设只是帮你快速填表。
- TTS / ASR 也按接口形态分（OpenAI 兼容的 /audio/speech、/audio/transcriptions 能吃一堆服务商）。

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

import requests

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

# qwen3-tts 系列自己的音色（和实时语音的 28 个不是一套，实测可用）
QWEN3_TTS_VOICES = [
    ("Cherry", "Cherry · 女声，亲和"),
    ("Serena", "Serena · 女声，温柔"),
    ("Ethan", "Ethan · 男声，阳光"),
    ("Chelsie", "Chelsie · 女声，活泼"),
    ("Dylan", "Dylan · 男声，年轻"),
    ("Jada", "Jada · 女声，成熟"),
    ("Sunny", "Sunny · 女声，明亮"),
    ("Nofish", "Nofish · 女声，清亮"),
    ("Kiki", "Kiki · 女声，甜"),
    ("Elias", "Elias · 男声，沉稳"),
    ("Jennifer", "Jennifer · 女声，播音"),
    ("Ryan", "Ryan · 男声，有力"),
    ("Katerina", "Katerina · 女声，低沉"),
    ("Li", "Li · 男声，中文"),
    ("Marcus", "Marcus · 男声，沉稳"),
    ("Roy", "Roy · 男声，磁性"),
    ("Peter", "Peter · 男声，厚重"),
    ("Rocky", "Rocky · 男声，粗犷"),
    ("Eric", "Eric · 男声"),
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


# LLM 只按**协议**分类；下面是常见服务商的现成参数，页面上点一下自动填表，
# 但你完全可以只填 base_url + model + key 去接任何没列出来的服务。
LLM_PRESETS = [
    {"id": "deepseek", "label": "DeepSeek", "type": "completions",
     "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
     "api_key_env": "DEEPSEEK_API_KEY", "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "qwen", "label": "通义千问", "type": "completions",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
     "api_key_env": "DASHSCOPE_API_KEY",
     "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"]},
    {"id": "kimi", "label": "Kimi（月之暗面）", "type": "completions",
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
    {"id": "azure-openai", "label": "Azure OpenAI", "type": "completions",
     "base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
     "model": "gpt-4o", "api_key_env": "AZURE_OPENAI_API_KEY",
     "models": ["gpt-4o", "gpt-4o-mini"]},
    {"id": "claude", "label": "Claude API", "type": "messages",
     "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5",
     "api_key_env": "ANTHROPIC_API_KEY",
     "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]},
    {"id": "moonshot-anthropic", "label": "Kimi（Anthropic 协议）", "type": "messages",
     "base_url": "https://api.moonshot.cn/anthropic", "model": "kimi-k2-turbo-preview",
     "api_key_env": "MOONSHOT_API_KEY", "models": ["kimi-k2-turbo-preview"]},
    {"id": "gemini", "label": "Google Gemini", "type": "generateContent",
     "base_url": "https://generativelanguage.googleapis.com", "model": "gemini-2.5-flash",
     "api_key_env": "GEMINI_API_KEY",
     "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    {"id": "ollama", "label": "Ollama（本机）", "type": "completions",
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b", "api_key_env": "",
     "models": ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b"]},
    {"id": "lmstudio", "label": "LM Studio（本机）", "type": "completions",
     "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "api_key_env": "",
     "models": []},
    {"id": "demo", "label": "离线演示", "type": "demo",
     "base_url": "", "model": "", "api_key_env": "",
     "models": [], "note": "不联网、不用 key，台词写死，用来调形象。"},
]

# ---------------------------------------------------------------- TTS

TTS_PROVIDERS = [
    {
        "id": "qwen-realtime", "label": "实时语音模型（Realtime，和全双工同一套音色）",
        "key_env": "DASHSCOPE_API_KEY",
        "models": [{"id": "qwen-audio-3.1-realtime-flash", "label": "qwen-audio-3.1-realtime-flash"}],
        "voices_by_model": {"*": QWEN_VOICES},
        "voices": QWEN_VOICES,
        "note": "用实时模型朗读，和全双工语音是同一套 28 个音色。",
    },
    {
        "id": "qwen-tts", "label": "原生语音合成（multimodal-generation）",
        "default_base": "https://dashscope.aliyuncs.com",
        "key_env": "DASHSCOPE_API_KEY",
        "api": "dashscope-tts",
        "models": [{"id": "qwen3-tts-flash", "label": "qwen3-tts-flash"}],
        "voices_by_model": {"qwen3-tts-flash": QWEN3_TTS_VOICES, "*": QWEN3_TTS_VOICES},
        "voices": QWEN3_TTS_VOICES,
        "note": "千问语音合成模型，音色和实时语音那套不同（Cherry/Ethan/Serena…）。",
    },
    {
        "id": "edge", "label": "Edge 在线语音（免 Key）", "key_env": "", "local": True,
        "models": [], "voices": EDGE_VOICES,
        "note": "微软 Edge 的在线音色，免 key。",
    },
    {
        "id": "openai", "label": "OpenAI 兼容 /audio/speech", "key_env": "OPENAI_API_KEY",
        "default_base": "https://api.openai.com/v1",
        "models": [{"id": "tts-1", "label": "tts-1"}, {"id": "tts-1-hd", "label": "tts-1-hd"}],
        "voices_by_model": {"*": OPENAI_TTS_VOICES},
        "voices": OPENAI_TTS_VOICES,
        "base_url_field": True,
        "note": "OpenAI、Groq、硅基流动、自建 one-api 等只要是这个接口形态都能用。",
    },
    {
        "id": "sapi", "label": "Windows 本机语音（离线）", "key_env": "", "local": True,
        "models": [], "voices": SAPI_VOICES,
        "note": "离线可用，但中文只有慧慧，比较机械。",
    },
]

# ---------------------------------------------------------------- ASR

ASR_PROVIDERS = [
    {
        "id": "none", "label": "不启用语音输入", "key_env": "", "models": [], "local": True,
        "note": "只能在控制台打字；麦克风按钮会禁用。",
    },
    {
        "id": "qwen", "label": "对话式音频识别（/chat/completions + input_audio）",
        "key_env": "DASHSCOPE_API_KEY",
        "default_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3-asr-flash"],
        "note": "浏览器录音 → 服务端转写，不依赖浏览器自带的语音识别。",
    },
    {
        "id": "openai", "label": "OpenAI 兼容 /audio/transcriptions", "key_env": "OPENAI_API_KEY",
        "default_base": "https://api.openai.com/v1",
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


def fetch_models_ex(base_url: str, api_key: str, kind: str = "completions",
                    timeout: float = 8) -> tuple[list[dict], str]:
    """从任意服务拉模型列表，按接口格式用对的路径和鉴权头。

    返回 ``(模型列表, 失败原因)``，成功时原因是空串。拉不到不影响使用，
    页面会把原因直接显示出来，用户可以手动输入模型名。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return [], "还没填 Base URL"
    headers: dict = {}
    params: dict = {}
    if kind == "messages":
        from .brains.anthropic_api import auth_headers
        headers = auth_headers(api_key)
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    elif kind == "generateContent":
        url = base + ("/models" if base.endswith(("/v1beta", "/v1")) else "/v1beta/models")
        params = {"key": api_key, "pageSize": 200} if api_key else {"pageSize": 200}
    else:                                           # completions / responses
        url = base + "/models"
        if api_key:
            headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as e:
        return [], f"连不上 {base}（{type(e).__name__}）"
    if r.status_code in (401, 403):
        return [], f"HTTP {r.status_code}：Key 不对或没有权限"
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}：这个地址没有 /models 接口，手动输入模型名即可"
    try:
        data = r.json()
    except ValueError:
        return [], "返回的不是 JSON"
    items = data.get("data") or data.get("models") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], "返回格式不认识，手动输入模型名即可"
    out, seen = [], set()
    for m in items:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name") or ""
            methods = m.get("supportedGenerationMethods")
            if kind == "generateContent" and methods and "generateContent" not in methods:
                continue
        else:
            mid = str(m)
        mid = mid.removeprefix("models/")
        if mid and mid not in seen:
            seen.add(mid)
            out.append({"id": mid, "label": mid})
    out.sort(key=lambda x: x["id"])
    return out, ("" if out else "接口返回了空列表")


def fetch_models(base_url: str, api_key: str, kind: str = "completions",
                 timeout: float = 8) -> list[dict]:
    """``fetch_models_ex`` 的只要列表版本，带 60 秒缓存。"""
    import hashlib
    ck = "models:%s:%s:%s" % (kind, base_url, hashlib.sha1((api_key or "").encode()).hexdigest()[:10])
    return _cached(ck, lambda: fetch_models_ex(base_url, api_key, kind, timeout)[0], ttl=60.0)


def voice_ids(tts_provider: str) -> set:
    return {v["id"] for v in voices_for(tts_provider)}
