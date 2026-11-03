"""Small, local intent router for desktop actions.

The common path must stay fast: ordinary questions go straight to the chat
model. Only explicit imperative requests are handed to a computer-use agent.
"""
from __future__ import annotations

import re

_POLITE = r"(?:请你|请|麻烦你|麻烦|帮我|帮忙|替我|给我|可以帮我|能不能帮我|能帮我|你可以|你能不能|能不能|可以)"
_ACTION = (
    r"(?:打开|关闭|关掉|启动|运行|点击|点一下|点开|按下|按一下|按键|输入|截图|截屏|"
    r"切到|切换到|切换|操作|控制|操控|移动鼠标|滚动|滚轮|聚焦|搜索|播放|暂停|继续|"
    r"登录|退出|安装|卸载|下载|上传|保存|删除|重命名|复制|粘贴|"
    r"去玩|玩一下|玩一局|打一局|开始玩)"
)
_QUESTION_PREFIX = re.compile(
    r"^(?:怎么|怎样|如何|为什么|为啥|什么是|什么是|能不能教|可以教|会不会|是否)"
)
_EXPLAIN = re.compile(r"(?:是什么意思|什么意思|怎么用|如何使用|为什么|原理)")
_LEADING = re.compile(r"^(?:大肥鱼|肥鱼|鱼鱼|爸爸|喂)[，,、:：\s]*")
_IMPERATIVE = re.compile(rf"^(?:{_POLITE}[，,\s]*)?{_ACTION}")
_REQUEST = re.compile(rf"{_POLITE}.{{0,12}}?{_ACTION}|(?:去|快|立刻|马上).{{0,6}}?{_ACTION}")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_action_request(text: str) -> bool:
    """Return True only for an explicit request to operate the computer.

    Questions about how to do something stay on the fast chat path. Past-tense
    statements such as "我刚刚打开了游戏" also stay there.
    """
    s = _LEADING.sub("", normalize_text(text))
    if not s or _QUESTION_PREFIX.search(s) or _EXPLAIN.search(s):
        return False
    if s.startswith("我") and "了" in s[:12]:
        return False
    return bool(_IMPERATIVE.search(s) or _REQUEST.search(s))


def action_reason(text: str) -> str:
    return "explicit desktop action" if is_action_request(text) else "chat"
