"""任意 agent CLI 当大脑：claude / codex / gemini / aider / 自己的脚本 …

配置示例:
    {"type": "cli", "cmd": ["claude", "-p"], "stdin": true}
    {"type": "cli", "cmd": ["codex", "exec", "--skip-git-repo-check",
                            "--output-last-message", "{outfile}", "-"], "stdin": true}

cmd 里可用的占位符:
    {prompt}   完整提示词（含人设与历史）；stdin=true 时改为写入标准输入，避免命令行转义问题
    {outfile}  临时文件路径；出现时以该文件内容作为回复（适合 stdout 夹杂日志的 CLI）
"""
from __future__ import annotations

import codecs
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from typing import Iterator

from . import Brain
from ..protocol import CLI_WRAPPER, PERSONA

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


class CLIBrain(Brain):
    kind = "cli"

    def build_prompt(self, prompt: str, history: list[dict]) -> str:
        if not self.conf.get("persona", True):
            return prompt
        turns = history[-2 * self.conf.get("history_turns", 6):]
        hist = "".join(f"{'用户' if m['role'] == 'user' else '大肥鱼'}：{m['content']}\n" for m in turns)
        return CLI_WRAPPER.format(persona=self.conf.get("system", PERSONA), history=hist, prompt=prompt)

    def stream(self, prompt: str, history: list[dict], cancel: threading.Event) -> Iterator[str]:
        c = self.conf
        full = self.build_prompt(prompt, history)
        use_stdin = c.get("stdin", True)
        outfile = None
        args = []
        for a in c["cmd"]:
            if "{outfile}" in a:
                if outfile is None:
                    fd, outfile = tempfile.mkstemp(prefix="yuyaya_", suffix=".txt")
                    os.close(fd)
                a = a.replace("{outfile}", outfile)
            if "{prompt}" in a:
                a = a.replace("{prompt}", "" if use_stdin else full)
            args.append(a)
        exe = shutil.which(args[0])
        if not exe:
            raise RuntimeError(f"找不到命令 {args[0]!r}，请确认已安装并在 PATH 中")
        args[0] = exe

        env = {**os.environ, **c.get("env", {}), "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            args, cwd=c.get("cwd") or None, env=env,
            stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",  # POSIX 下独立进程组，便于整组杀掉
        )
        stderr_tail: list[bytes] = []
        threading.Thread(target=_drain, args=(proc.stderr, stderr_tail), daemon=True).start()
        done = threading.Event()
        threading.Thread(target=_watch, args=(proc, cancel, done, c.get("timeout", 600)), daemon=True).start()
        try:
            if use_stdin:
                proc.stdin.write(full.encode("utf-8"))
                proc.stdin.close()
            dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                if outfile is None:
                    text = _ANSI.sub("", dec.decode(chunk))
                    if text:
                        yield text
            code = proc.wait()
            if cancel.is_set():
                return
            if outfile is not None:
                with open(outfile, encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
                if text:
                    yield text
            if code != 0:
                tail = b"".join(stderr_tail).decode("utf-8", "replace")[-300:]
                raise RuntimeError(f"{os.path.basename(exe)} 退出码 {code}: {_ANSI.sub('', tail).strip()}")
        finally:
            done.set()
            if proc.poll() is None:
                _kill_tree(proc)
            if outfile:
                try:
                    os.remove(outfile)
                except OSError:
                    pass


def _drain(pipe, tail: list[bytes]) -> None:
    for line in iter(pipe.readline, b""):
        tail.append(line)
        del tail[:-20]


def _watch(proc: subprocess.Popen, cancel: threading.Event, done: threading.Event, timeout: float) -> None:
    """被打断或超时就杀掉子进程。"""
    waited = 0.0
    while not done.wait(0.2):
        waited += 0.2
        if (cancel.is_set() or waited > timeout) and proc.poll() is None:
            _kill_tree(proc)
            return


def _kill_tree(proc: subprocess.Popen) -> None:
    """连同孙进程一起杀掉：codex/claude 往往是 .cmd 或 node 包装，只杀直接子进程会留下真正干活的进程。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
