"""输出适配器注册表。"""
from __future__ import annotations

from .action_log import ActionLogOutput
from .recorder import RecorderOutput
from .sse import SSEOutputAdapter

OUTPUTS = {
    "sse": SSEOutputAdapter,
    "action_log": ActionLogOutput,
    "recorder": RecorderOutput,
}

__all__ = ["OUTPUTS", "SSEOutputAdapter", "ActionLogOutput", "RecorderOutput"]
