"""Google Gemini（原生 generateContent 接口，流式通过 :streamGenerateContent?alt=sse）。"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterator

import requests

from . import Brain
from ..protocol import PERSONA

DEFAULT_BASE = "https://generativelanguage.googleapis.com"
API_VERSION = "v1beta"


class GeminiBrain(Brain):
    kind = "gemini"

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        c = self.conf
        key = os.environ.get(c.get("api_key_env", "GEMINI_API_KEY"), "") or c.get("api_key", "")
        if not key:
            raise RuntimeError(f"缺少环境变量 {c.get('api_key_env') or 'GEMINI_API_KEY'}")
        system = c.get("system", PERSONA)
        if context:
            system += "\n\n" + context
        contents = []
        for m in history:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content") or ""}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        model = c.get("model") or "gemini-2.5-flash"
        base = (c.get("base_url") or DEFAULT_BASE).rstrip("/")
        url = f"{base}/{API_VERSION}/models/{model}:streamGenerateContent"
        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"temperature": c.get("temperature", 0.9),
                                 "maxOutputTokens": c.get("max_tokens", 2048)},
            **(c.get("extra") or {}),
        }
        with requests.post(url, params={"alt": "sse", "key": key}, json=body,
                           stream=True, timeout=(10, c.get("timeout", 120))) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.content.decode('utf-8', 'ignore')[:300]}")
            for raw in r.iter_lines():
                if cancel.is_set():
                    return
                if not raw:
                    continue
                line = raw.decode("utf-8", "ignore")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                for cand in ev.get("candidates") or []:
                    for part in (cand.get("content") or {}).get("parts") or []:
                        text = part.get("text")
                        if text:
                            yield text

    def availability(self) -> tuple[bool, str]:
        c = self.conf
        if c.get("api_key") or os.environ.get(c.get("api_key_env", "GEMINI_API_KEY")):
            return True, ""
        return False, f"需要设置环境变量 {c.get('api_key_env') or 'GEMINI_API_KEY'}"
