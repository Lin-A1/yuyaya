"""Messages 接口（Anthropic 格式）：Claude 官方，以及任何兼容 /v1/messages 的网关。

直接走 HTTP，不依赖 anthropic SDK，所以 ``base_url`` 想填什么就是什么。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Iterator

import requests

from . import Brain
from ..protocol import PERSONA

DEFAULT_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


def messages_url(base: str) -> str:
    """base 写到 /v1 或者不写 /v1 都行。"""
    base = (base or DEFAULT_BASE).rstrip("/")
    return base + ("/messages" if base.endswith("/v1") else "/v1/messages")


def auth_headers(key: str) -> dict:
    h = {"anthropic-version": API_VERSION, "Content-Type": "application/json"}
    if key:
        h["x-api-key"] = key                 # 官方
        h["Authorization"] = f"Bearer {key}"  # 不少网关只认这个
    return h


class AnthropicBrain(Brain):
    kind = "anthropic"

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        c = self.conf
        key = c.get("api_key") or os.environ.get(c.get("api_key_env") or "ANTHROPIC_API_KEY", "") \
            or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        system = c.get("system", PERSONA)
        if context:
            system = system + "\n\n" + context
        body = {
            "model": c.get("model") or "claude-sonnet-4-5",
            "max_tokens": int(c.get("max_tokens", 4096)),
            "system": system,
            "messages": [*history, {"role": "user", "content": prompt}],
            "stream": True,
            **c.get("extra", {}),
        }
        with requests.post(messages_url(c.get("base_url", "")), headers=auth_headers(key),
                           json=body, stream=True,
                           timeout=(10, float(c.get("timeout", 120)))) as r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.content.decode('utf-8', 'ignore')[:300]}")
            for raw in r.iter_lines():
                if cancel.is_set():
                    return
                if not raw or not raw.startswith(b"data:"):
                    continue
                try:
                    ev = json.loads(raw[5:].strip())
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif kind == "message_delta":
                    if (ev.get("delta") or {}).get("stop_reason") == "refusal":
                        yield "[sad]这个问题我不能回答呢。"
                elif kind == "message_stop":
                    return
                elif kind == "error":
                    raise RuntimeError(str((ev.get("error") or {}).get("message") or ev)[:300])
