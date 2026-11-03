"""大脑适配器：把 prompt 变成文本流。

每个大脑只需要实现 stream(prompt, history, cancel, context="") -> Iterator[str]，
情绪标签、字幕切分、状态机都在 app 层统一处理，所以接入新的 LLM / agent 很简单。

history 是 [{"role": "user"|"assistant", "content": str}, ...]，不含本轮 prompt。
cancel 是 threading.Event，被打断时置位，适配器应尽快停止产出。
context 是"她此刻的情绪 + 长期记忆"这段附加说明，不属于用户说的话。
  各适配器按自己的方式安放：API 大脑并进 system，CLI 大脑并进提示词模板。
  不需要就原样忽略这个参数。
"""
from __future__ import annotations

import threading
from typing import Iterator


class Brain:
    kind = "base"

    def __init__(self, name: str, conf: dict) -> None:
        self.name = name
        self.conf = conf
        self.label = conf.get("label", name)

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name, "kind": self.kind, "label": self.label,
                "model": self.conf.get("model") or " ".join(self.conf.get("cmd", [])[:1])}


def build_brain(name: str, conf: dict) -> Brain:
    from .openai_compat import OpenAICompatBrain
    from .anthropic_api import AnthropicBrain
    from .cli import CLIBrain
    from .demo import DemoBrain

    kinds = {"openai": OpenAICompatBrain, "anthropic": AnthropicBrain,
             "cli": CLIBrain, "demo": DemoBrain}
    kind = conf.get("type", "demo")
    if kind not in kinds:
        raise ValueError(f"brain {name}: unknown type {kind!r}, expected one of {list(kinds)}")
    return kinds[kind](name, conf)
