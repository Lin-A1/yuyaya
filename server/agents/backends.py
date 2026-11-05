"""后台 agent 后端：只负责拼命令行、读输出、杀进程。

加一个后端 = 在 config.json 的 agents.backends 里加一项（命令行型），
或者写一个 AgentBackend 子类再 register()。
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Callable

from ..brains.cli import _kill_tree

_ANSI = re.compile(r"\[[0-9;?]*[ -/]*[@-~]")


class AgentBackend:
    name = "backend"
    capabilities = {"resume": False, "ask": False, "stream": True}

    def __init__(self, name: str, conf: dict | None = None) -> None:
        self.name = name
        self.conf = conf or {}
        self.timeout = float(self.conf.get("timeout", 180))

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, job, prompt: str, cwd: str, emit: Callable[[str], None],
            timeout: float) -> tuple[int, str]:
        """同步跑完一个 job，返回 (退出码, 最终输出)。job.handle 要设置好，便于 cancel。"""
        raise NotImplementedError

    def cancel(self, job) -> None:
        proc = job.handle
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)

    def info(self) -> dict:
        ok, why = self.available()
        return {"name": self.name, "available": ok, "reason": why,
                "capabilities": self.capabilities, "timeout": self.timeout}


class CLIBackend(AgentBackend):
    """任何命令行 agent。

    cmd 里的占位符：{prompt}（提示词）、{outfile}（让 CLI 把最终答复写到这个文件）。
    没有 {prompt} 时提示词走 stdin —— claude -p 的 --allowedTools 是可变参数，
    会吞掉后面的位置参数，所以 claude 必须走 stdin。
    """

    def __init__(self, name: str, conf: dict | None = None) -> None:
        super().__init__(name, conf)
        self.cmd = list(self.conf.get("cmd") or [])

    def available(self) -> tuple[bool, str]:
        if not self.cmd:
            return False, "没有配置 cmd"
        if not shutil.which(self.cmd[0]):
            return False, f"找不到命令 {self.cmd[0]!r}，请确认已安装并在 PATH 中"
        return True, ""

    def run(self, job, prompt: str, cwd: str, emit, timeout: float) -> tuple[int, str]:
        exe = shutil.which(self.cmd[0])
        if not exe:
            raise RuntimeError(f"找不到命令 {self.cmd[0]!r}")
        joined = " ".join(self.cmd)
        use_stdin = "{prompt}" not in joined
        outfile = None
        if "{outfile}" in joined:
            fd, outfile = tempfile.mkstemp(prefix="yuyaya-job-", suffix=".txt")
            os.close(fd)
        args = [exe] + [a.replace("{prompt}", prompt).replace("{outfile}", outfile or "")
                        for a in self.cmd[1:]]
        env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
        for k in self.conf.get("env_drop", []):
            env.pop(k, None)
        try:
            proc = subprocess.Popen(
                args, cwd=cwd or None, env=env,
                stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
            job.handle = proc
            if job.cancelled:                      # 启动瞬间就被取消了
                _kill_tree(proc)
            chunks: list[bytes] = []

            def reader() -> None:
                buf = b""
                while True:
                    c = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                    if not c:
                        break
                    chunks.append(c)
                    buf += c
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        t = _ANSI.sub("", ln.decode("utf-8", "replace")).strip()
                        if t:
                            emit(t)

            th = threading.Thread(target=reader, daemon=True)
            th.start()
            try:
                out_b, _ = b"", None
                if use_stdin and proc.stdin is not None:
                    try:
                        proc.stdin.write(prompt.encode("utf-8"))
                        proc.stdin.close()
                    except OSError:
                        pass
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                th.join(timeout=2)
                raise
            th.join(timeout=2)
            text = _ANSI.sub("", b"".join(chunks).decode("utf-8", "replace")).strip()
            if outfile and os.path.exists(outfile):
                final = pathlib.Path(outfile).read_text(encoding="utf-8", errors="replace").strip()
                if final:
                    text = final
            return proc.returncode, text
        finally:
            job.handle = None
            if outfile:
                try:
                    os.unlink(outfile)
                except OSError:
                    pass


_KINDS = {"cli": CLIBackend}


def register_backend(type_name: str, cls) -> None:
    _KINDS[type_name] = cls


def build_backends(conf: dict) -> dict[str, AgentBackend]:
    """从 config.agents.backends 构建；没配时用 config.actions 的命令当作 codex 后端（兼容旧配置）。"""
    agents = conf.get("agents") or {}
    specs = dict(agents.get("backends") or {})
    if not specs:
        act = conf.get("actions") or {}
        if act.get("cmd"):
            specs["codex"] = {"cmd": act["cmd"], "timeout": act.get("timeout", 180),
                              "cwd": act.get("cwd", "")}
    out: dict[str, AgentBackend] = {}
    for name, spec in specs.items():
        if spec.get("enabled", True) is False:
            continue
        cls = _KINDS.get(spec.get("type", "cli"))
        if cls is None:
            raise ValueError(f"未知的 agent 后端类型 {spec.get('type')!r}（{name}）")
        out[name] = cls(name, spec)
    return out
