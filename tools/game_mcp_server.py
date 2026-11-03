"""MCP 服务器（stdio）：把"操作桌面"和"让她说话"暴露成 MCP 工具。

    claude mcp add game -- python /path/to/yuyaya/tools/game_mcp_server.py

工具：
    game_screenshot   截屏（返回图片，模型能直接"看见"）
    game_click        点击
    game_key          按键
    game_type         输入文字
    game_windows      列窗口
    game_focus        聚焦窗口
    game_say          让大肥鱼以"游戏解说"的身份说一句话

只依赖标准库（截图优先用 Pillow，没有就退回纯 ctypes 抓屏）。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from game_control import win  # noqa: E402

URL = os.environ.get("YUYAYA_URL", "http://127.0.0.1:8848").rstrip("/")

TOOLS = [
    {
        "name": "game_screenshot",
        "description": "截屏并返回画面，用来确认当前游戏状态。返回的是图片，你能看到画面里的内容。"
                       "game_click 的 x/y 用这张图上的像素坐标就行（截图缩放过也会自动换算，"
                       "返回的文字里会写清屏幕分辨率和缩放比例）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "可选，x,y,w,h 只截这一块"},
                "max_width": {"type": "integer", "description": "缩放后的最大宽度，默认 1280",
                              "default": 1280},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_click",
        "description": "点击。x/y 用 game_screenshot 那张图上的像素坐标，"
                       "内部会按截图的缩放比例换算成真实屏幕坐标。"
                       "如果 x/y 已经是屏幕坐标，加 raw=true。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1},
                "scale": {"type": "number", "description": "可选，覆盖截图的缩放比例"},
                "raw": {"type": "boolean", "default": False,
                        "description": "x/y 已经是真实屏幕坐标时不换算"},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_key",
        "description": "按一个键。name 可以是单字符（w / a / 空格）或特殊键"
                       "（enter / esc / space / tab / up / down / left / right / shift / ctrl / alt / f1…）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "action": {"type": "string", "enum": ["press", "down", "up"], "default": "press"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_type",
        "description": "输入一段文字（游戏里打字/聊天用）。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_windows",
        "description": "列出当前打开的窗口（找游戏窗口用）。"
                       "x/y/w/h 是真实屏幕坐标，ix/iy 是截图上的坐标。",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "game_focus",
        "description": "把某个标题的窗口切到前台。",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "窗口标题的一部分"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_say",
        "description": "让数字人大肥鱼用'游戏解说'的口吻说一句话（会显示成字幕并有口型）。"
                       "游戏里发生了什么、观众问什么，都可以吐槽一句，但每句别超过 20 个字，别刷屏。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要说的话，可含 [happy] [shy] [thinking] 情绪标签"},
                "interrupt": {"type": "boolean", "default": False},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
]


def post(path: str, body: dict, method: str = "POST") -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _region(s: str | None):
    if not s:
        return None
    parts = [int(x) for x in s.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError("region 格式是 x,y,w,h")
    return tuple(parts)


def call(name: str, args: dict) -> dict:
    if name == "game_screenshot":
        meta = win.screenshot_meta(max_width=int(args.get("max_width") or 1280),
                                   region=_region(args.get("region")))
        png = meta.pop("png")
        info = (f"屏幕 {meta['screen'][0]}x{meta['screen'][1]}，"
                f"这张图 {meta['width']}x{meta['height']}，"
                f"图像坐标 × {meta['scale']} = 屏幕坐标，图像左上角对应屏幕 {meta['origin']}。"
                f"game_click 直接用图上的像素坐标即可。")
        return {"content": [{"type": "text", "text": info},
                            {"type": "image", "data": base64.b64encode(png).decode("ascii"),
                             "mimeType": "image/png"}]}
    if name == "game_click":
        detail = win.click(int(args["x"]), int(args["y"]),
                           args.get("button") or "left", int(args.get("clicks") or 1),
                           scale=args.get("scale"), raw=bool(args.get("raw")))
        return {"content": [{"type": "text", "text": detail}]}
    if name == "game_key":
        detail = win.key(args["name"], args.get("action") or "press")
        return {"content": [{"type": "text", "text": detail}]}
    if name == "game_type":
        detail = win.type_text(args.get("text") or "")
        return {"content": [{"type": "text", "text": detail}]}
    if name == "game_windows":
        return {"content": [{"type": "text",
                             "text": json.dumps(win.list_windows(), ensure_ascii=False)}]}
    if name == "game_focus":
        return {"content": [{"type": "text", "text": win.focus(args["title"])}]}
    if name == "game_say":
        res = post("/api/say", {"text": args.get("text", ""), "ch": "game",
                                "interrupt": bool(args.get("interrupt"))})
        return {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]}
    raise ValueError(f"unknown tool {name}")


def handle(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "yuyaya-game", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        p = msg.get("params", {})
        name, args = p.get("name"), p.get("arguments") or {}
        try:
            return call(name, args)
        except Exception as e:   # 失败也不要打断 agent 的主任务
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is None:
        return None
    raise LookupError(method)


def main() -> None:
    stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", newline="\n")
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        try:
            result = handle(msg)
            if mid is None:
                continue
            resp = {"jsonrpc": "2.0", "id": mid, "result": result}
        except LookupError as e:
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {e}"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
