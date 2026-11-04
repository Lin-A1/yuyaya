"""动作日志输出：把 action / action_log 写进 logging（不进字幕）。"""
from __future__ import annotations

import logging

from ..base import OutputAdapter

log = logging.getLogger("yuyaya.action")


class ActionLogOutput(OutputAdapter):
    type = "action_log"

    def on_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "action":
            log.info("动作 %s：%s", event.get("state"), event.get("goal") or event.get("error") or "")
        elif etype == "action_log":
            log.info("%s", event.get("text", ""))
