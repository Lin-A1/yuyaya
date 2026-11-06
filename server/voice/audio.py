"""语音流水线用的音频小工具。

流水线内部统一用 **PCM16 单声道**：麦克风是 16 kHz，朗读回传是 24 kHz。
TTS 提供者返回的是 WAV（Edge 是 MP3），这里负责转成统一的 PCM。
"""
from __future__ import annotations

import io
import shutil
import subprocess
import wave
from array import array

try:                                   # 3.13 起标准库里没有 audioop 了
    import audioop
except ImportError:                    # pragma: no cover
    audioop = None


class AudioError(RuntimeError):
    pass


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def rms(pcm: bytes) -> float:
    """归一化到 0..1 的均方根响度。"""
    if not pcm:
        return 0.0
    if audioop is not None:
        return audioop.rms(pcm, 2) / 32768.0
    a = array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    return (sum(x * x for x in a) / max(1, len(a))) ** 0.5 / 32768.0


def _resample(raw: bytes, src: int, dst: int) -> bytes:
    if src == dst or not raw:
        return raw
    if audioop is not None:
        return audioop.ratecv(raw, 2, 1, src, dst, None)[0]
    a = array("h")                                    # 退路：线性插值
    a.frombytes(raw)
    n = int(len(a) * dst / src)
    out = array("h", [0]) * n
    for i in range(n):
        p = i * src / dst
        j = int(p)
        f = p - j
        x0 = a[j]
        x1 = a[min(j + 1, len(a) - 1)]
        out[i] = int(x0 + (x1 - x0) * f)
    return out.tobytes()


def wav_to_pcm(data: bytes, rate: int = 24000) -> bytes:
    """PCM WAV → 单声道 PCM16 @ rate。头里的长度不对（流式占位头）也能读。"""
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise AudioError(f"不是能读的 PCM WAV：{e}") from e
    if width == 1 and audioop is not None:
        raw = audioop.bias(raw, 1, -128)
    if width != 2:
        if audioop is None:
            raise AudioError(f"不支持 {width * 8} 位 WAV")
        raw = audioop.lin2lin(raw, width, 2)
    if ch == 2:
        if audioop is None:
            raise AudioError("不支持立体声 WAV")
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    return _resample(raw, sr, rate)


def to_pcm(data: bytes, mime: str = "audio/wav", rate: int = 24000) -> bytes:
    """TTS 返回的任意音频 → PCM16 @ rate。WAV 直接解；MP3 之类借 ffmpeg。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return wav_to_pcm(data, rate)
    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg                       # 可选依赖，自带 ffmpeg 二进制
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ff = None
    if not ff:
        raise AudioError(f"这个 TTS 返回 {mime}，需要 ffmpeg 才能转成流式音频")
    try:
        r = subprocess.run(
            [ff, "-v", "quiet", "-i", "pipe:0", "-f", "s16le", "-ar", str(rate), "-ac", "1",
             "pipe:1"], input=data, capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        raise AudioError(f"ffmpeg 转码失败：{e}") from e
    if r.returncode != 0 or not r.stdout:
        raise AudioError("ffmpeg 转码失败")
    return r.stdout


def chunk_pcm(pcm: bytes, ms: int = 200, rate: int = 24000):
    """按时长切块（偶数字节对齐，别把一个采样切成两半）。"""
    size = max(2, int(rate * ms / 1000) * 2)
    for i in range(0, len(pcm), size):
        yield pcm[i:i + size]
