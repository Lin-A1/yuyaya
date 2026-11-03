"""Run a CLI computer-use agent without blocking the chat path."""
from __future__ import annotations

import codecs
import logging
import os
import pathlib
import subprocess
import threading
import time

from .brains.cli import _kill_tree

log = logging.getLogger("yuyaya.actions")


def action_prompt(root: pathlib.Path, goal: str) -> str:
    return f"""你是在用户电脑上执行任务的 Codex 代理。

任务：
{goal}

规则：
- 直接完成任务，不要解释准备过程。
- 需要看屏幕或操作窗口时，优先使用：
  python {root.as_posix()}/tools/game.py windows
  python {root.as_posix()}/tools/game.py shot --out {root.as_posix()}/state/action-shot.png
  python {root.as_posix()}/tools/game.py click X Y
  python {root.as_posix()}/tools/game.py key w
  python {root.as_posix()}/tools/game.py type "文字"
- 截图坐标就是 click 使用的坐标，工具会处理 DPI 和缩放。
- 完成或失败后用一句话告诉用户：
  python {root.as_posix()}/tools/yaya.py say "结果"
- stdout 只进日志，不会自动变成字幕。
- 不要删除用户文件，不要修改与任务无关的内容。
"""


class ActionRunner:
    def __init__(self, hub, conf: dict | None, root: pathlib.Path) -> None:
        self.hub = hub
        self.conf = conf or {}
        self.root = root
        self.enabled = bool(self.conf.get("enabled", False) and self.conf.get("cmd"))
        self.cmd = list(self.conf.get("cmd") or [])
        self.cwd = self.conf.get("cwd") or None
        self.timeout = float(self.conf.get("timeout", 180))
        self.ack = (self.conf.get("ack") or "好，我去弄一下！").strip()
        self._busy = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._started = 0.0
        self._goal = ""

    def start(self, text: str, source: str = "user") -> dict:
        if not self.enabled:
            return {"accepted": False, "reason": "actions disabled"}
        if not self._busy.acquire(blocking=False):
            return {"accepted": False, "reason": "busy"}
        try:
            goal = (text or "").strip()
            if not goal:
                raise ValueError("empty action")
            self._goal = goal
            self._started = time.time()
            threading.Thread(target=self._worker, args=(goal, source),
                             name="computer-action", daemon=True).start()
            return {"accepted": True, "ack": self.ack, "goal": goal}
        except Exception:
            self._busy.release()
            raise

    def _worker(self, goal: str, source: str) -> None:
        proc = None
        try:
            prompt = action_prompt(self.root, goal)
            joined = " ".join(self.cmd)
            use_stdin = "{goal}" not in joined and "{prompt}" not in joined
            args = [a.replace("{goal}", prompt).replace("{prompt}", prompt)
                    .replace("{goal_only}", goal) for a in self.cmd]
            env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
            proc = subprocess.Popen(
                args, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self._proc = proc
            if use_stdin and proc.stdin is not None:
                try:
                    proc.stdin.write(prompt.encode("utf-8"))
                    proc.stdin.close()
                except OSError:
                    pass

            reader = threading.Thread(target=self._read_output, args=(proc,), daemon=True)
            reader.start()
            code = proc.wait(timeout=self.timeout)
            reader.join(timeout=1.0)
            self.hub.publish({"type": "action", "state": "finished", "source": source,
                              "code": code, "goal": goal})
        except subprocess.TimeoutExpired:
            if proc is not None:
                _kill_tree(proc)
            self.hub.publish({"type": "action", "state": "timeout", "source": source,
                              "goal": goal})
            log.warning("动作代理超时：%s", goal)
        except Exception as e:
            self.hub.publish({"type": "action", "state": "failed", "source": source,
                              "error": str(e), "goal": goal})
            log.exception("动作代理失败")
        finally:
            self._proc = None
            self._busy.release()

    def _read_output(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            for line in dec.decode(chunk).splitlines():
                if line.strip():
                    self.hub.publish({"type": "action_log", "text": "[action] " + line.strip()})

    def stop(self) -> dict:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
            return {"stopped": True, "pid": proc.pid}
        return {"stopped": False}

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "busy": self._busy.locked(),
            "pid": self._proc.pid if self._proc else None,
            "started_at": self._started or None,
            "goal": self._goal,
        }
