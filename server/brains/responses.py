"""OpenAI Responses API（``/v1/responses``）流式适配器。

和 Chat Completions 的区别：入参是 ``instructions`` + ``input``，流里的事件是
``response.output_text.delta`` 这种带 type 的对象，不是 ``choices[].delta.content``。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterator

import requests

from . import Brain
from ..protocol import PERSONA

DEFAULT_BASE = "https://api.openai.com/v1"


class ResponsesBrain(Brain):
    kind = "responses"

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        c = self.conf
        key = os.environ.get(c.get("api_key_env", "OPENAI_API_KEY"), "") or c.get("api_key", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        system = c.get("system", PERSONA)
        if context:
            system += "\n\n" + context
        # Responses API 的 input 用 role/content 列表，assistant 侧同理
        items = [{"role": m.get("role") or "user", "content": m.get("content") or ""}
                 for m in history]
        items.append({"role": "user", "content": prompt})
        body = {
            "model": c.get("model") or "gpt-5",
            "instructions": system,
            "input": items,
            "stream": True,
            **(c.get("extra") or {}),
        }
        url = (c.get("base_url") or DEFAULT_BASE).rstrip("/") + "/responses"
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
                if not data or data == b"[DONE]":
                    continue
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                etype = ev.get("type") or ""
                if etype.endswith("output_text.delta"):
                    text = ev.get("delta")
                    if text:
                        yield text
                elif etype in ("error", "response.failed"):
                    err = ev.get("error") or ev.get("response") or ev
                    raise RuntimeError(str(err)[:300])
                elif etype == "response.completed":
                    return
