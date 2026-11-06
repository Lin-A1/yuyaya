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

import os
import shutil
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

    def availability(self) -> tuple[bool, str]:
        """这个大脑现在能不能用：(可用?, 不可用的原因)。

        只做静态检查（环境变量有没有、命令装没装），不发请求 ——
        真正连不上的错误还是会在大脑调用时报出来。
        """
        c = self.conf
        kind = c.get("type", self.kind)
        if kind == "demo":
            return True, ""
        if kind in ("openai", "completions", "responses", "anthropic", "messages",
                    "gemini", "generateContent"):
            if c.get("api_key"):
                return True, ""
            env = c.get("api_key_env") or ("ANTHROPIC_API_KEY" if kind in ("anthropic", "messages")
                                           else "GEMINI_API_KEY" if kind in ("gemini", "generateContent")
                                           else "")
            if env:
                if os.environ.get(env):
                    return True, ""
                if kind in ("anthropic", "messages") and os.environ.get("ANTHROPIC_AUTH_TOKEN"):
                    return True, ""       # 走代理 / 网关时用的令牌
                return False, f"需要设置环境变量 {env}"
            # 没配 key 的：本地服务（Ollama / vLLM / LM Studio）不需要，但得看它开没开
            base = c.get("base_url", "")
            if "127.0.0.1" in base or "localhost" in base or "[::1]" in base:
                import socket
                import urllib.parse
                u = urllib.parse.urlparse(base)
                host = u.hostname or "127.0.0.1"
                port = u.port or (443 if u.scheme == "https" else 80)
                try:
                    with socket.create_connection((host, port), timeout=0.3):
                        pass
                except OSError:
                    return False, f"本地服务没开（{host}:{port}）"
                return True, ""
            if kind == "anthropic":
                return False, "需要设置 ANTHROPIC_API_KEY"
            return True, ""               # 不要求 key 的自建服务
        if kind == "cli":
            cmd = c.get("cmd") or []
            exe = cmd[0] if cmd else ""
            if not exe:
                return False, "没有配置 cmd"
            if shutil.which(exe):
                return True, ""
            return False, f"找不到命令 {exe}"
        return True, ""

    def info(self) -> dict:
        ok, why = self.availability()
        kind = self.conf.get("type", self.kind)
        return {"name": self.name, "kind": self.kind, "label": self.label,
                "model": self.conf.get("model") or " ".join(self.conf.get("cmd", [])[:1]),
                "available": ok, "reason": why,
                # 聊天用 API 大脑（快）；CLI 是"干活"的，每次要起进程，慢得多
                "role": "agent" if kind == "cli" else "chat",
                "slow": kind == "cli"}


def build_brain(name: str, conf: dict) -> Brain:
    from .openai_compat import OpenAICompatBrain
    from .responses import ResponsesBrain
    from .anthropic_api import AnthropicBrain
    from .gemini import GeminiBrain
    from .cli import CLIBrain
    from .demo import DemoBrain

    kinds = {"completions": OpenAICompatBrain, "openai": OpenAICompatBrain,
             "responses": ResponsesBrain,
             "messages": AnthropicBrain, "anthropic": AnthropicBrain,
             "generateContent": GeminiBrain, "gemini": GeminiBrain,
             "cli": CLIBrain, "demo": DemoBrain}
    kind = conf.get("type", "demo")
    if kind not in kinds:
        raise ValueError(f"brain {name}: unknown type {kind!r}, 可选 {list(kinds)}")
    return kinds[kind](name, conf)
