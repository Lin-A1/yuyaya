"""端点检测（VAD）：从连续的麦克风 PCM 里切出"一句话"。

只用响度，不依赖任何第三方库，但比固定阈值靠谱得多：

- **自适应噪声底**：非说话时持续估计环境噪声，阈值是噪声底的几倍，换房间/换麦克风不用调。
- **滞回**：开口要超过阈值，说话中只要不低于 0.7 倍阈值就算还在说，不会被拖长音/换气切断。
- **起点确认**：持续发声超过 ``onset_ms`` 才算开口，一声咳嗽、敲桌子不算。
  她自己在说话时（``strict=True``）要求更久（``onset_strict_ms``）且更响，
  这是防回声误触发插话的第一道关。
- **预录**：开口确认时把前 ``preroll_ms`` 的音频一起带上，句首的辅音不会被吃掉。
- **收尾**：连续安静 ``end_ms`` 才算说完；太短的（< ``min_speech_ms``）当噪声丢弃。
"""
from __future__ import annotations

from collections import deque

from .audio import rms


class Endpointer:
    def __init__(self, rate: int = 16000, frame_ms: int = 20, onset_ms: int = 120,
                 onset_strict_ms: int = 320, end_ms: int = 650, min_speech_ms: int = 250,
                 preroll_ms: int = 320, max_ms: int = 25000, min_rms: float = 0.012,
                 ratio: float = 3.0, strict_boost: float = 1.5) -> None:
        self.rate = rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(rate * frame_ms / 1000) * 2
        self.onset = max(1, onset_ms // frame_ms)
        self.onset_strict = max(1, onset_strict_ms // frame_ms)
        self.end = max(1, end_ms // frame_ms)
        self.min_speech = max(1, min_speech_ms // frame_ms)
        self.max_frames = max(1, max_ms // frame_ms)
        self.min_rms = min_rms
        self.ratio = ratio
        self.strict_boost = strict_boost
        self._pre_len = max(1, preroll_ms // frame_ms)
        self.floor = 0.004
        self.reset()

    def reset(self) -> None:
        self.tail = b""
        self.pre: deque = deque(maxlen=self._pre_len)
        self.buf: list = []
        self.in_speech = False
        self.run = 0
        self.silence = 0
        self.speech_frames = 0

    @property
    def threshold(self) -> float:
        return max(self.min_rms, self.floor * self.ratio)

    def feed(self, pcm: bytes, strict: bool = False) -> list[tuple]:
        """喂一段 PCM16。返回事件列表：
        ``("start",)``            确认开口
        ``("end", pcm, ms)``      说完了，pcm 是这一整句（含预录）
        ``("drop", "short")``     太短，当噪声丢掉
        """
        events: list[tuple] = []
        data = self.tail + pcm
        fb = self.frame_bytes
        n = len(data) // fb * fb
        self.tail = data[n:]
        for i in range(0, n, fb):
            self._frame(data[i:i + fb], strict, events)
        return events

    def _frame(self, frame: bytes, strict: bool, events: list) -> None:
        level = rms(frame)
        thr = self.threshold
        if self.in_speech:
            thr *= 0.7
        elif strict:
            thr *= self.strict_boost
        loud = level > thr

        if not self.in_speech:
            self.pre.append(frame)
            if loud:
                self.run += 1
            else:
                self.run = 0
                # 只在安静时学习噪声底：往上慢、往下快，别被偶发的响声带偏
                self.floor += (level - self.floor) * (0.04 if level > self.floor else 0.2)
                self.floor = min(0.05, max(0.001, self.floor))
            need = self.onset_strict if strict else self.onset
            if self.run >= need:
                self.in_speech = True
                self.buf = list(self.pre)
                self.pre.clear()
                self.silence = 0
                self.speech_frames = self.run
                events.append(("start",))
            return

        self.buf.append(frame)
        if loud:
            self.silence = 0
            self.speech_frames += 1
        else:
            self.silence += 1
        if self.silence >= self.end or len(self.buf) >= self.max_frames:
            keep = len(self.buf) - max(0, self.silence - 8)    # 尾部留 ~160ms，别截得太生硬
            pcm = b"".join(self.buf[:max(1, keep)])
            ms = self.speech_frames * self.frame_ms
            long_enough = self.speech_frames >= self.min_speech
            self.in_speech = False
            self.run = 0
            self.silence = 0
            self.speech_frames = 0
            self.buf = []
            events.append(("end", pcm, ms) if long_enough else ("drop", "short"))
