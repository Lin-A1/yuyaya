"""桌面控制内核（Windows）。三种外壳都调这里：shell CLI / MCP / HTTP。"""
from . import win  # noqa: F401

__all__ = ["win"]
