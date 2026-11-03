"""OpenAI 兼容接口：DeepSeek / 通义千问 / Kimi / 智谱 / Ollama / vLLM / LM Studio …"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterator

import requests

from . import Brain
from ..protocol import PERSONA


class OpenAICompatBrain(Brain):
    kind = "openai"

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event) -> Iterator[str]:
        c = self.conf
        key = os.environ.get(c.get("api_key_env", ""), "") or c.get("api_key", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        messages = [{"role": "system", "content": c.get("system", PERSONA)}, *history,
                    {"role": "user", "content": prompt}]
        body = {"model": c["model"], "messages": messages, "stream": True, **c.get("extra", {})}
        url = c.get("base_url", "https://api.deepseek.com").rstrip("/") + "/chat/completions"

        with requests.post(url, headers=headers, json=body, stream=True,
                           timeout=(10, c.get("timeout", 120))) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.content.decode('utf-8', 'ignore')[:300]}")
            for raw in r.iter_lines():
                if cancel.is_set():
                    return
                if not raw or not raw.startswith(b"data:"):
                    continue
                data = raw[5:].strip()
                if data == b"[DONE]":
                    return
                try:
                    choices = json.loads(data).get("choices") or [{}]
                except ValueError:
                    continue
                text = (choices[0].get("delta") or {}).get("content")
                if text:
                    yield text
