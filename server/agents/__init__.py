"""后台 agent：异步任务编排 + 可插拔后端（Codex / Claude Code / OpenCode …）+ 前台自主触发。"""
from .backends import AgentBackend, CLIBackend, build_backends, register_backend
from .initiative import HIGH, LOW, NORMAL, Initiative, Intent
from .job import Job, JobStore
from .orchestrator import Orchestrator

__all__ = ["AgentBackend", "CLIBackend", "build_backends", "register_backend",
           "Job", "JobStore", "Orchestrator", "Initiative", "Intent", "HIGH", "NORMAL", "LOW"]
