"""语音识别：浏览器的语音输入不再依赖浏览器自带的 SpeechRecognition。

前端录音 → 一段音频（webm/ogg/wav）→ 这里转成文本。目前只有 Qwen
（``qwen3-asr-flash``，走 DashScope 的 OpenAI 兼容接口，base64 传音频，不用上传文件）。

选 ``none`` 时这条链路直接关掉，麦克风按钮会被禁用，只能打字。
"""
from __future__ import annotations

import base64
import os
import pathlib
import subprocess
import tempfile

import requests

DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-asr-flash"


class ASRError(RuntimeError):
    pass


def transcribe(audio: bytes, provider: str, model: str = "", key_env: str = "",
               base_url: str = "", timeout: float = 60, api_key: str = "") -> str:
    if provider in ("", "none"):
        raise ASRError("没有启用语音识别")
    if not audio:
        raise ASRError("没收到音频")
    if provider == "openai":
        return _openai_transcribe(audio, model, key_env, base_url, timeout, api_key)
    if provider != "qwen":
        raise ASRError(f"不支持的语音识别：{provider}")
    key = api_key or os.environ.get(key_env or "DASHSCOPE_API_KEY", "")
    if not key:
        raise ASRError(f"需要设置环境变量 {key_env or 'DASHSCOPE_API_KEY'}")
    fmt, data = _normalize(audio)
    body = {
        "model": model or DEFAULT_MODEL,
        "messages": [{"role": "user", "content": [{
            "type": "input_audio",
            "input_audio": {"data": f"data:audio/{fmt};base64,{base64.b64encode(data).decode()}",
                            "format": fmt},
        }]}],
    }
    url = (base_url or DEFAULT_BASE).rstrip("/") + "/chat/completions"
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=body,
                          timeout=(10, timeout))
    except requests.RequestException as e:
        raise ASRError(f"识别请求失败：{e}") from e
    if r.status_code != 200:
        raise ASRError(f"识别失败 HTTP {r.status_code}：{r.text[:200]}")
    try:
        text = r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError) as e:
        raise ASRError(f"识别返回格式不对：{r.text[:200]}") from e
    return text.strip()


def _openai_transcribe(audio: bytes, model: str, key_env: str, base_url: str,
                       timeout: float, api_key: str = "") -> str:
    """OpenAI 兼容的 /audio/transcriptions（multipart 上传）。"""
    key = api_key or os.environ.get(key_env or "OPENAI_API_KEY", "")
    if not key:
        raise ASRError(f"需要设置环境变量 {key_env or 'OPENAI_API_KEY'}")
    fmt, data = _normalize(audio)
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    files = {"file": (f"speech.{fmt}", data, f"audio/{fmt}")}
    form = {"model": model or "whisper-1", "language": "zh"}
    try:
        r = requests.post(f"{base}/audio/transcriptions",
                          headers={"Authorization": f"Bearer {key}"},
                          files=files, data=form, timeout=(10, timeout))
    except requests.RequestException as e:
        raise ASRError(f"识别请求失败：{e}") from e
    if r.status_code != 200:
        raise ASRError(f"识别失败 HTTP {r.status_code}：{r.text[:200]}")
    try:
        return (r.json().get("text") or "").strip()
    except ValueError as e:
        raise ASRError(f"识别返回格式不对：{r.text[:200]}") from e


def _normalize(audio: bytes) -> tuple[str, bytes]:
    """认出容器格式；浏览器默认录 webm/ogg，Qwen 都认，认不出就当 wav 试一次。"""
    head = audio[:12]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav", audio
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "mp3", audio
    if head[:4] == b"OggS":
        return "ogg", _to_wav(audio) or audio
    if head[:4] == b"\x1a\x45\xdf\xa3":          # EBML / webm
        return "webm", audio
    return "wav", audio


def _to_wav(audio: bytes):
    """ogg 在部分环境下不被接受时用 ffmpeg 转 wav；没有 ffmpeg 就原样返回。"""
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        return b""
    src = dst = None
    try:
        fd, src = tempfile.mkstemp(suffix=".ogg")
        os.close(fd)
        pathlib.Path(src).write_bytes(audio)
        fd, dst = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run([ff, "-y", "-i", src, "-ar", "16000", "-ac", "1", dst],
                       capture_output=True, timeout=30, creationflags=flags)
        return pathlib.Path(dst).read_bytes()
    except (OSError, subprocess.SubprocessError):
        return b""
    finally:
        for p in (src, dst):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
