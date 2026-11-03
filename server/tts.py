"""Windows SAPI text-to-speech with no cloud dependency."""
from __future__ import annotations

import base64
import os
import pathlib
import shutil
import subprocess
import tempfile


def available() -> bool:
    return os.name == "nt" and shutil.which("powershell.exe") is not None


def synth_wav(text: str, voice: str = "", rate: int = 0, volume: int = 100) -> bytes:
    if not available():
        raise RuntimeError("当前系统没有可用的 SAPI TTS")
    text = (text or "").strip()
    if not text:
        raise ValueError("text is required")
    fd, name = tempfile.mkstemp(prefix="yuyaya-tts-", suffix=".wav")
    os.close(fd)
    out = pathlib.Path(name)
    env = {
        **os.environ,
        "YUYAYA_TTS_OUT": str(out),
        "YUYAYA_TTS_TEXT": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "YUYAYA_TTS_VOICE": base64.b64encode((voice or "").encode("utf-8")).decode("ascii"),
        "YUYAYA_TTS_RATE": str(max(-10, min(10, int(rate)))),
        "YUYAYA_TTS_VOLUME": str(max(0, min(100, int(volume)))),
    }
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:YUYAYA_TTS_TEXT))
$wanted = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:YUYAYA_TTS_VOICE))
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($wanted) {
  $match = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Name -eq $wanted } | Select-Object -First 1
  if (-not $match) { $match = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Name -like "*$wanted*" } | Select-Object -First 1 }
  if ($match) { $s.SelectVoice($match.VoiceInfo.Name) }
} else {
  $match = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1
  if ($match) { $s.SelectVoice($match.VoiceInfo.Name) }
}
$s.Rate = [int]$env:YUYAYA_TTS_RATE
$s.Volume = [int]$env:YUYAYA_TTS_VOLUME
$s.SetOutputToWaveFile($env:YUYAYA_TTS_OUT)
$s.Speak($text)
$s.Dispose()
"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, check=True, capture_output=True, timeout=30,
            creationflags=flags,
        )
        data = out.read_bytes()
        if not data.startswith(b"RIFF"):
            raise RuntimeError("TTS 没有生成有效 WAV")
        return data
    finally:
        try:
            out.unlink()
        except OSError:
            pass
