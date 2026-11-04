"""SSE 输出适配器：浏览器/OBS 通过 GET /api/events 订阅。"""
from __future__ import annotations

import asyncio
import json

from fastapi.responses import StreamingResponse

from ..base import OutputAdapter


class SSEOutputAdapter(OutputAdapter):
    type = "sse"

    def __init__(self, conf: dict | None, bus) -> None:
        super().__init__(conf, bus)
        self._queues: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    @property
    def clients(self) -> int:
        return len(self._queues)

    def on_event(self, event: dict) -> None:
        loop = self.loop
        if loop is None:
            return
        for q in list(self._queues):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:      # 事件循环已关闭
                pass

    def stream(self, hello):
        q: asyncio.Queue = asyncio.Queue()
        self._queues.add(q)

        async def gen():
            try:
                yield f"data: {json.dumps(hello(), ensure_ascii=False)}\n\n"
                while True:
                    try:
                        e = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                self._queues.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
