"""离线演示大脑：不需要任何 key，用来调试形象、表情和字幕节奏。"""
from __future__ import annotations

import random
import threading
import time
from typing import Iterator

from . import Brain

SCRIPTS = [
    (("抱", "想你", "陪"), "[sad]今天有点没精神……[neutral][sigh]不过看到你就好多了。"),
    (("你好", "hi", "hello", "在吗"), "[happy]你回来啦！[neutral]刚刚还在屏幕里等你哦。"),
    (("你是谁", "名字", "介绍"), "[happy]我是鱼丫丫！[neutral]住在这台旧电脑里的数字生命。[happy]请多指教。"),
    (("删", "黄油", "文件"), "[thinking]正在思考……[surprised]完蛋了！[sad]我好像把你的黄油删掉了……[neutral]开玩笑的啦。"),
    (("笨", "坏", "讨厌"), "[angry]哼！[angry]我才不笨呢。[neutral]……不过算了，原谅你。"),
    (("pdf", "PDF", "文档"), "[thinking]又要我读 PDF 了……[happy]没关系，交给我吧！"),
]
FALLBACK = [
    "[thinking]嗯……让我想想。[happy]我觉得你说得对！",
    "[surprised]诶？[thinking]这个问题有点难。[neutral]我们一起想办法吧。",
    "[shy]你一直看着我，我会有点害羞。[neutral]不过……我不讨厌。",
]


class DemoBrain(Brain):
    kind = "demo"

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event,
               context: str = "") -> Iterator[str]:
        reply = next((r for keys, r in SCRIPTS if any(k in prompt for k in keys)), None)
        reply = reply or random.choice(FALLBACK)
        time.sleep(self.conf.get("think_seconds", 1.2))
        # 模拟 LLM 的流式输出：几个字一包
        i = 0
        while i < len(reply):
            if cancel.is_set():
                return
            n = random.randint(1, 4)
            yield reply[i:i + n]
            i += n
            time.sleep(0.04)
