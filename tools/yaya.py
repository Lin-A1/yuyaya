"""命令行控制大肥鱼（只用标准库，任何脚本 / agent 都能直接调用）。

    python tools/yaya.py chat "你好呀"                 # 交给当前大脑回复
    python tools/yaya.py chat "写个快排" --brain codex # 指定大脑
    python tools/yaya.py say "[happy]我回来啦！"        # 直接说话，不经过大脑
    python tools/yaya.py emotion sad
    python tools/yaya.py motion hop                    # nod shake hop jump droop tremble tilt wiggle
    python tools/yaya.py state listening
    python tools/yaya.py interrupt | reset | brains
    python tools/yaya.py brain deepseek                # 切换默认大脑
    python tools/yaya.py game shot --out s.png         # 操作桌面（截图/点击/按键）
    python tools/yaya.py game click 640 360
    echo "长文本" | python tools/yaya.py say -         # 从标准输入读取

服务地址默认 http://127.0.0.1:8848，可用环境变量 YUYAYA_URL 修改。
"""
import argparse, json, os, pathlib, sys, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

URL = os.environ.get("YUYAYA_URL", "http://127.0.0.1:8848").rstrip("/")


def call(path, body=None, method=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    method = method or ("GET" if body is None else "POST")
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}")
    except urllib.error.URLError as e:
        sys.exit(f"连不上数字人服务 {URL}（先运行 python -m server）：{e.reason}")


def text_arg(t):
    return sys.stdin.read() if t == "-" else t


def _game(a):
    """操作桌面。和 tools/game.py 共用同一个内核。

    坐标是"截图上的像素"，shot 会记下缩放比例，click 自动换算。
    """
    from game_control import win
    cmd = a.gcmd
    if cmd == "shot":
        meta = win.screenshot_meta(max_width=a.max_width)
        png = meta.pop("png")
        path = ""
        if a.out:
            out = pathlib.Path(a.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)
            path = str(out.resolve())
        return {"ok": True, "bytes": len(png), "path": path, **meta}
    if cmd == "click":
        return {"ok": True, "detail": win.click(a.x, a.y, a.button, a.clicks,
                                               scale=a.scale or None, raw=a.raw)}
    if cmd == "key":
        return {"ok": True, "detail": win.key(a.name, a.action)}
    if cmd == "type":
        return {"ok": True, "detail": win.type_text(a.text)}
    if cmd == "windows":
        return {"ok": True, "windows": win.list_windows()}
    if cmd == "focus":
        return {"ok": True, "detail": win.focus(a.title)}
    return {"ok": False, "error": f"unknown game command {cmd!r}"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="控制大肥鱼数字人")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chat"); p.add_argument("text"); p.add_argument("--brain"); p.add_argument("--no-wait", action="store_true")
    p = sub.add_parser("say"); p.add_argument("text"); p.add_argument("-e", "--emotion")
    p.add_argument("--interrupt", action="store_true", help="立即打断正在说的话")
    p = sub.add_parser("emotion"); p.add_argument("name"); p.add_argument("--hold", type=float, default=0); p.add_argument("--instant", action="store_true")
    p = sub.add_parser("motion"); p.add_argument("name")
    p = sub.add_parser("state"); p.add_argument("state")
    p = sub.add_parser("brain"); p.add_argument("name")
    p = sub.add_parser("remember"); p.add_argument("text")
    p = sub.add_parser("forget"); p.add_argument("text", nargs="?")
    p = sub.add_parser("memory")
    g = sub.add_parser("game", help="操作桌面（截图/点击/按键）")
    gsub = g.add_subparsers(dest="gcmd", required=True)
    q = gsub.add_parser("shot"); q.add_argument("--out", default=""); q.add_argument("--max-width", type=int, default=1280)
    q = gsub.add_parser("click"); q.add_argument("x", type=int); q.add_argument("y", type=int)
    q.add_argument("--button", default="left", choices=["left", "right", "middle"]); q.add_argument("--clicks", type=int, default=1)
    q.add_argument("--scale", type=float, default=0.0, help="图上坐标的缩放比例，默认用上次截图记下来的")
    q.add_argument("--raw", action="store_true", help="x/y 已经是屏幕坐标，不换算")
    q = gsub.add_parser("key"); q.add_argument("name"); q.add_argument("--action", default="press", choices=["press", "down", "up"])
    q = gsub.add_parser("type"); q.add_argument("text")
    q = gsub.add_parser("windows")
    q = gsub.add_parser("focus"); q.add_argument("title")
    for name in ("interrupt", "reset", "brains"):
        sub.add_parser(name)
    a = ap.parse_args()

    if a.cmd == "game":
        r = _game(a)
    elif a.cmd == "chat":
        r = call("/api/chat", {"text": text_arg(a.text), "brain": a.brain, "wait": not a.no_wait})
    elif a.cmd == "say":
        r = call("/api/say", {"text": text_arg(a.text), "emotion": a.emotion,
                              "interrupt": a.interrupt})
    elif a.cmd == "emotion":
        r = call("/api/emotion", {"name": a.name, "hold": a.hold, "instant": a.instant})
    elif a.cmd == "motion":
        r = call("/api/motion", {"name": a.name})
    elif a.cmd == "state":
        r = call("/api/state", {"state": a.state})
    elif a.cmd == "brain":
        r = call("/api/brain", {"name": a.name})
    elif a.cmd == "brains":
        r = call("/api/brains")
    elif a.cmd == "remember":
        r = call("/api/memory", {"text": a.text})
    elif a.cmd == "forget":
        r = call("/api/memory", {"text": a.text} if a.text else None, method="DELETE")
    elif a.cmd == "memory":
        r = call("/api/memory")
    else:
        r = call(f"/api/{a.cmd}", {})
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
