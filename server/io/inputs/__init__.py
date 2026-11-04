"""输入适配器注册表。"""
from __future__ import annotations

from .browser_speech import BrowserSpeechInputAdapter
from .http import HttpInputAdapter

INPUTS = {
    "http": HttpInputAdapter,
    "browser_speech": BrowserSpeechInputAdapter,
}

__all__ = ["INPUTS", "HttpInputAdapter", "BrowserSpeechInputAdapter"]
