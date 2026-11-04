"""Run a CLI computer-use agent without blocking the chat path."""
from __future__ import annotations

import codecs
import logging
import os
import pathlib
import shutil
import subprocess
import threading
import time

from .brains.cli import _kill_tree

log = logging.getLogger("yuyaya.actions")


def action_prompt(root: pathlib.Path, goal: str, speak: bool = True) -> str:
    if speak:
        report = f"""- 完成或失败后用一句话告诉用户：
  python {root.as_posix()}/tools/yaya.py say "结果"
"""
    else:
        report = """- 完成后把结果直接打印到标准输出（一两句话，别贴代码）；运行时会转达给用户。
"""
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
{report}
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
            exe = shutil.which(args[0])
            if not exe:
                raise RuntimeError(f"找不到命令 {args[0]!r}，请确认已安装并在 PATH 中")
            args[0] = exe
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

    def run(self, text: str, timeout: float | None = None, speak: bool = False) -> dict:
        """同步执行一次 Codex 任务，返回最终输出（给实时语音当工具用）。

        和 start() 的区别：不另起线程、不靠她说话汇报，而是等进程结束，
        把 stdout 尾部作为工具结果交还给调用方（例如 Realtime 模型）。
        """
        if not self.enabled:
            return {"ok": False, "error": "actions disabled", "output": ""}
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "busy", "output": ""}
        proc = None
        try:
            goal = (text or "").strip()
            if not goal:
                raise ValueError("empty action")
            prompt = action_prompt(self.root, goal, speak=speak)
            joined = " ".join(self.cmd)
            use_stdin = "{goal}" not in joined and "{prompt}" not in joined
            args = [a.replace("{goal}", prompt).replace("{prompt}", prompt)
                    .replace("{goal_only}", goal) for a in self.cmd]
            exe = shutil.which(args[0])
            if not exe:
                raise RuntimeError(f"找不到命令 {args[0]!r}，请确认已安装并在 PATH 中")
            args[0] = exe
            env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
            proc = subprocess.Popen(
                args, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self._proc = proc
            self._goal = goal
            self._started = time.time()
            out, _ = proc.communicate(
                prompt.encode("utf-8") if use_stdin else None,
                timeout=float(timeout or self.timeout),
            )
            output = out.decode("utf-8", "replace").strip()
            for line in output.splitlines()[-40:]:
                if line.strip():
                    self.hub.publish({"type": "action_log", "text": "[codex] " + line.strip()})
            return {"ok": proc.returncode == 0, "code": proc.returncode,
                    "output": output[-2000:]}
        except subprocess.TimeoutExpired:
            if proc is not None:
                _kill_tree(proc)
            self.hub.publish({"type": "action", "state": "timeout", "source": "realtime",
                              "goal": text})
            return {"ok": False, "error": "timeout", "output": ""}
        except Exception as e:
            return {"ok": False, "error": str(e), "output": ""}
        finally:
            self._proc = None
            self._busy.release()

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


class OrchestratedActions:
    """ActionRunner 的新实现：对外接口不变（start / run / stop / status），
    内部交给 agents.Orchestrator —— 异步、可并行、可换后端。"""

    def __init__(self, orch, root: pathlib.Path, default_timeout: float = 180) -> None:
        self.orch = orch
        self.root = root
        self.timeout = default_timeout

    @property
    def enabled(self) -> bool:
        return self.orch.enabled

    def start(self, text: str, source: str = "user") -> dict:
        r = self.orch.start(text, kind="desktop", origin=source, confirmed=True)
        if not r.get("accepted"):
            reason = r.get("reason") or "unknown"
            return {"accepted": False, "reason": "actions disabled" if "disabled" in reason else reason}
        return r

    def run(self, text: str, timeout: float | None = None, speak: bool = False) -> dict:
        """同步等结果（旧 codex 工具 / 兼容用）。"""
        r = self.orch.start(text, kind="desktop", origin="sync", confirmed=True)
        if not r.get("accepted"):
            return {"ok": False, "error": r.get("reason") or "rejected", "output": ""}
        deadline = time.time() + float(timeout or self.timeout) + 5
        job = self.orch.get(r["job_id"])
        while time.time() < deadline:
            if job.status in ("done", "failed", "timeout", "cancelled", "lost", "needs_input"):
                break
            time.sleep(0.2)
        else:
            self.orch.cancel(job.id)
            return {"ok": False, "error": "timeout", "output": ""}
        return {"ok": job.status == "done", "code": 0 if job.status == "done" else 1,
                "output": job.result or job.error, "status": job.status}

    def stop(self) -> dict:
        n = self.orch.cancel_all()
        return {"stopped": n > 0, "count": n}

    def status(self) -> dict:
        active = [j for j in self.orch.store.jobs.values() if j.status in ("queued", "running")]
        cur = active[0] if active else None
        return {"enabled": self.enabled, "busy": bool(active),
                "pid": getattr(getattr(cur, "handle", None), "pid", None),
                "started_at": cur.started_at if cur else None,
                "goal": cur.task if cur else "", "jobs": len(active)}
