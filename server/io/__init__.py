"""可插拔输入/输出适配层。

    server/io/
      base.py        契约：InputAdapter / OutputAdapter / TTSProvider / InputError
      events.py      规范输入（make_input）
      bus.py         事件总线（订阅 + 过期 turn 过滤）
      turns.py       Turn / TurnManager（打断的最小单位）
      dispatcher.py  规范输入 -> 聊天 / 说话 / 打断
      registry.py    按 config.io 构建适配器
      inputs/        http、browser_speech
      outputs/       sse、action_log、recorder
      tts/           sapi
"""
from .base import InputAdapter, InputError, OutputAdapter, TTSProvider
from .bus import EventBus
from .dispatcher import InputDispatcher
from .events import INPUT_TYPES, make_input
from .registry import build_inputs, build_outputs, build_tts
from .turns import Turn, TurnManager

__all__ = [
    "InputAdapter", "OutputAdapter", "TTSProvider", "InputError",
    "EventBus", "Turn", "TurnManager", "InputDispatcher",
    "INPUT_TYPES", "make_input",
    "build_inputs", "build_outputs", "build_tts",
]
