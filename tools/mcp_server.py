"""MCP 服务器（stdio）：让 Claude Code / Codex / Cursor 等 agent 把大肥鱼当成"嘴巴和脸"来用。

agent 在干活时可以调用 yaya_say 向用户播报进度、用表情表达状态，
数字人页面会实时显示字幕和表情。只依赖标准库，按 MCP 2025-06-18 的 JSON-RPC 规范实现。

注册（Claude Code）:
    claude mcp add yuyaya -- python /path/to/yuyaya/tools/mcp_server.py
注册（Codex，~/.codex/config.toml）:
    [mcp_servers.yuyaya]
    command = "python"
    args = ["/path/to/yuyaya/tools/mcp_server.py"]

数字人服务地址默认 http://127.0.0.1:8848，可用环境变量 YUYAYA_URL 修改。
"""
import json, os, sys, urllib.request

URL = os.environ.get("YUYAYA_URL", "http://127.0.0.1:8848").rstrip("/")
EMOTIONS = ["neutral", "happy", "sad", "surprised", "thinking", "shy", "angry"]
MOTIONS = ["nod", "shake", "hop", "jump", "droop", "tremble", "tilt", "wiggle", "sniff", "sigh"]

TOOLS = [
    {
        "name": "yaya_say",
        "description": "让数字人大肥鱼对用户说一段话（显示为字幕并带口型）。适合播报进度、汇报结果、"
                       "和用户打招呼。每句话尽量短（不超过 25 字）。文本里可插入情绪标签切换表情，"
                       "例如 \"[happy]搞定啦！[shy]夸夸我。\"；也可插入动作标签 [nod] [hop] [tilt] 等。默认排在上一句后面播放。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要说的话，可含 [happy] [sad] [surprised] [thinking] [shy] [angry] [neutral] 标签"},
                "emotion": {"type": "string", "enum": EMOTIONS, "description": "开口前先切换到的表情（可选）"},
                "interrupt": {"type": "boolean", "description": "true 则立即打断正在说的话", "default": False},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yaya_emotion",
        "description": "只切换数字人的表情，不说话。例如开始长任务时设为 thinking，出错时设为 sad。"
                       "情绪有惯性：默认会按自然路线逐步过渡（例如刚哭过要先平复再笑），需要立即切换时传 instant。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": EMOTIONS},
                "hold": {"type": "number", "description": "临时表情持续秒数，立即生效，结束后恢复之前的情绪；0 或不填表示永久切换"},
                "instant": {"type": "boolean", "description": "true 则跳过过渡立即切换", "default": False},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yaya_motion",
        "description": "让数字人做一个动作：nod 点头 / shake 摇头 / hop 蹦一下 / jump 跳起来 / "
                       "droop 垂头丧气 / tremble 发抖 / tilt 歪头 / wiggle 扭一扭 / sniff 抽鼻子 / sigh 叹气。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": MOTIONS}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yaya_remember",
        "description": "让数字人记住一件事（长期记忆，重启后还在）。适合记下用户的喜好、约定、称呼。"
                       "同一件事不要重复记；要忘掉用 yaya_forget。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要记住的事，一句话"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yaya_forget",
        "description": "让数字人忘掉一件事。不给 text 就全部忘掉。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要忘掉的事；不给就全忘"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "yaya_state",
        "description": "设置数字人的状态：idle 空闲 / thinking 思考中 / listening 倾听中 / speaking 说话中。",
        "inputSchema": {
            "type": "object",
            "properties": {"state": {"type": "string", "enum": ["idle", "listening", "thinking", "speaking"]}},
            "required": ["state"],
            "additionalProperties": False,
        },
    },
]

ROUTES = {"yaya_say": "/api/say", "yaya_emotion": "/api/emotion", "yaya_motion": "/api/motion",
           "yaya_state": "/api/state", "yaya_remember": "/api/memory"}
METHODS = {"yaya_forget": "DELETE"}


def post(path, body, method="POST"):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(URL + path, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "yuyaya", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        p = msg.get("params", {})
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in ROUTES:
            raise ValueError(f"unknown tool {name}")
        try:
            result = post(ROUTES[name], args or None, METHODS.get(name, "POST"))
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except Exception as e:  # 数字人没开不应该让 agent 的主任务失败
            return {"content": [{"type": "text", "text": f"数字人服务不可用（{URL}）：{e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is None:
        return None  # 通知类消息（notifications/initialized 等）无需回复
    raise LookupError(method)


def main():
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
