"""会话记忆：短期上下文 + 长期记忆点。

    turns   最近的对话轮次（user / assistant 交替），重启后仍在
    facts   长期记忆点（"爸爸喜欢喝奶茶"），由 [记住:xxx] 标签或 /api/memory 写入

两个都存在 state/memory.json（可用 YUYAYA_STATE 指定路径），写入是原子的，
避免服务被 kill 时写坏文件。
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

MAX_FACTS = 60          # 长期记忆条数上限，超了丢最老的


class Session:
    """一个会话的上下文。

    对大脑来说，只需要两样东西：
        messages()  ->  [{"role", "content"}]，可以直接发给 LLM
        context()    ->  一段文字，告诉她"你记得的事"和"你现在的情绪"
    """

    def __init__(self, path: pathlib.Path | None = None, max_turns: int = 12) -> None:
        self.path = path
        self.max_turns = max_turns
        self.turns: list[dict] = []
        self.facts: list[dict] = []
        self.lock = threading.RLock()
        self.load()

    # ---------- 持久化 ----------
    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        with self.lock:
            self.turns = list(data.get("turns", []))[-2 * self.max_turns :]
            self.facts = list(data.get("facts", []))[-MAX_FACTS:]

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            data = {"turns": self.turns[-2 * self.max_turns :], "facts": self.facts[-MAX_FACTS:],
                    "updated": time.time()}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)          # 原子替换，中途被杀也不会写坏
        except OSError:
            pass

    # ---------- 写入 ----------
    def add_turn(self, role: str, content: str, emotion: str | None = None) -> None:
        with self.lock:
            turn = {"role": role, "content": content}
            if emotion and role == "assistant":
                turn["emotion"] = emotion
            self.turns.append(turn)
            del self.turns[: -2 * self.max_turns]

    def add_fact(self, text: str) -> bool:
        """记住一件事。重复的不会记两遍。"""
        text = " ".join(text.split()).strip()
        if not text:
            return False
        with self.lock:
            if any(f["text"] == text for f in self.facts):
                return False
            self.facts.append({"text": text, "at": time.time()})
            del self.facts[:-MAX_FACTS]
        return True

    def forget(self, text: str | None = None) -> int:
        """忘掉某一件事（精确匹配），或者全部忘掉。"""
        with self.lock:
            if text is None:
                n = len(self.facts)
                self.facts.clear()
                return n
            before = len(self.facts)
            self.facts = [f for f in self.facts if f["text"] != text]
            return before - len(self.facts)

    def clear_turns(self) -> None:
        with self.lock:
            self.turns.clear()

    # ---------- 读取 ----------
    def messages(self) -> list[dict]:
        """给 LLM 用的消息列表。助手那侧带上情绪标签，让她知道自己刚才什么情绪。"""
        with self.lock:
            out = []
            for t in self.turns[-2 * self.max_turns :]:
                content = t["content"]
                if t.get("emotion"):
                    content = f"[{t['emotion']}]{content}"
                out.append({"role": t["role"], "content": content})
            return out

    def context(self) -> str:
        """塞进系统提示词的一段话：长期记忆 + 情绪轨迹。"""
        with self.lock:
            parts = []
            if self.facts:
                lines = "\n".join(f"- {f['text']}" for f in self.facts[-12:])
                parts.append(f"（你记得这些事：\n{lines}\n）")
            trail = [t["emotion"] for t in self.turns if t.get("emotion")]
            if len(trail) > 1:
                parts.append(f"（你最近说话时的情绪依次是：{' → '.join(trail[-6:])}。）")
            return "\n".join(parts)

    def stats(self) -> dict:
        with self.lock:
            return {"turns": len(self.turns), "facts": len(self.facts)}
