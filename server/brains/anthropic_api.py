"""Claude（Anthropic 官方 Python SDK，需 pip install anthropic）。"""
from __future__ import annotations

import os
import threading
from typing import Iterator

from . import Brain
from ..protocol import PERSONA


class AnthropicBrain(Brain):
    kind = "anthropic"

    def __init__(self, name: str, conf: dict) -> None:
        super().__init__(name, conf)
        self._client = None

    def client(self):
        if self._client is None:
            import anthropic  # 延迟导入：不用 Claude 的人不必安装

            key = self.conf.get("api_key") or os.environ.get(self.conf.get("api_key_env", "ANTHROPIC_API_KEY"))
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        c = self.conf
        system = c.get("system", PERSONA)
        if context:
            system = system + "\n\n" + context
        kwargs = dict(
            model=c.get("model", "claude-opus-5-5"),
            max_tokens=c.get("max_tokens", 4096),
            system=system,
            messages=[*history, {"role": "user", "content": prompt}],
            output_config={"effort": c.get("effort", "low")},
        )
        api = self.client().messages
        if c.get("fallbacks", "default"):
            # 安全分类器拒答时由服务端自动换模型重试，而不是直接返回 refusal
            api = self.client().beta.messages
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["extra_body"] = {"fallbacks": c.get("fallbacks", "default")}

        with api.stream(**kwargs) as s:
            for text in s.text_stream:
                if cancel.is_set():
                    return
                yield text
            if s.get_final_message().stop_reason == "refusal":
                yield "[sad]这个问题我不能回答呢。"
