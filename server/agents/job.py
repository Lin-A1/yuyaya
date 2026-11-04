"""后台任务（Job）：状态机 + 落盘。"""
from __future__ import annotations

import json
import pathlib
import threading
import time

ACTIVE = ("queued", "running")
TERMINAL = ("done", "failed", "timeout", "cancelled", "lost")


class Job:
    __slots__ = ("id", "backend", "kind", "task", "cwd", "status", "created_at", "started_at",
                 "finished_at", "progress", "result", "error", "attempts", "depends_on",
                 "origin", "quiet", "handle", "cancelled", "last_output", "question", "answers")

    def __init__(self, jid: int, backend: str, task: str, kind: str = "general",
                 cwd: str = "", depends_on: list[int] | None = None, origin: str = "") -> None:
        self.id = jid
        self.backend = backend
        self.kind = kind
        self.task = task
        self.cwd = cwd
        self.status = "queued"
        self.created_at = time.time()
        self.started_at = 0.0
        self.finished_at = 0.0
        self.progress: list[str] = []
        self.result = ""
        self.error = ""
        self.attempts = 0
        self.depends_on = list(depends_on or [])
        self.origin = origin
        self.quiet = False            # 用户主动取消的不用再汇报
        self.handle = None            # 运行中的 subprocess，不落盘
        self.cancelled = False
        self.last_output = time.time()
        self.question = ""           # needs_input 时后台想问的话
        self.answers: list[str] = []  # 已得到的回答，重跑时拼进提示词

    def public(self) -> dict:
        end = self.finished_at or time.time()
        return {
            "id": self.id, "backend": self.backend, "kind": self.kind, "task": self.task,
            "status": self.status, "attempts": self.attempts, "depends_on": self.depends_on,
            "elapsed": round(end - self.started_at, 1) if self.started_at else 0,
            "progress": self.progress[-3:], "question": self.question, "result": self.result[-1500:], "error": self.error,
            "created_at": self.created_at,
        }

    def dump(self) -> dict:
        d = self.public()
        d.update(cwd=self.cwd, started_at=self.started_at, finished_at=self.finished_at,
                 origin=self.origin)
        return d

    @classmethod
    def load(cls, d: dict) -> "Job":
        j = cls(int(d["id"]), d.get("backend", ""), d.get("task", ""), d.get("kind", "general"),
                d.get("cwd", ""), d.get("depends_on") or [], d.get("origin", ""))
        j.status = d.get("status", "lost")
        j.created_at = d.get("created_at", 0.0)
        j.started_at = d.get("started_at", 0.0)
        j.finished_at = d.get("finished_at", 0.0)
        j.result = d.get("result", "")
        j.error = d.get("error", "")
        j.attempts = d.get("attempts", 0)
        return j


class JobStore:
    """job 表落盘到 state/jobs.json；重启时仍在运行的标成 lost。"""

    MAX = 100

    def __init__(self, path: pathlib.Path | None) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.jobs: dict[int, Job] = {}
        self.next_id = 1
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        for d in data.get("jobs", []):
            j = Job.load(d)
            if j.status in ACTIVE:
                j.status, j.error = "lost", "服务重启，任务中断"
                j.finished_at = time.time()
            self.jobs[j.id] = j
        self.next_id = max([data.get("next_id", 1)] + [i + 1 for i in self.jobs])

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            keep = sorted(self.jobs.values(), key=lambda j: j.id)[-self.MAX:]
            data = {"next_id": self.next_id, "jobs": [j.dump() for j in keep]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def add(self, job_factory) -> Job:
        with self.lock:
            job = job_factory(self.next_id)
            self.next_id += 1
            self.jobs[job.id] = job
            return job
