"""命令行控制大肥鱼（只用标准库，任何脚本 / agent 都能直接调用）。

    python tools/yaya.py chat "你好呀"                 # 交给当前大脑回复
    python tools/yaya.py chat "写个快排" --brain codex # 指定大脑
    python tools/yaya.py say "[happy]我回来啦！"        # 直接说话，不经过大脑
    python tools/yaya.py emotion sad
    python tools/yaya.py motion hop                    # nod shake hop jump droop tremble tilt wiggle
    python tools/yaya.py state listening
    python tools/yaya.py interrupt | reset | brains
    python tools/yaya.py brain deepseek                # 切换默认大脑
    echo "长文本" | python tools/yaya.py say -         # 从标准输入读取

服务地址默认 http://127.0.0.1:8848，可用环境变量 YUYAYA_URL 修改。
"""
import argparse, json, os, sys, urllib.request

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


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="控制大肥鱼数字人")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chat"); p.add_argument("text"); p.add_argument("--brain"); p.add_argument("--no-wait", action="store_true")
    p = sub.add_parser("say"); p.add_argument("text"); p.add_argument("-e", "--emotion")
    p = sub.add_parser("emotion"); p.add_argument("name"); p.add_argument("--hold", type=float, default=0); p.add_argument("--instant", action="store_true")
    p = sub.add_parser("motion"); p.add_argument("name")
    p = sub.add_parser("state"); p.add_argument("state")
    p = sub.add_parser("brain"); p.add_argument("name")
    p = sub.add_parser("remember"); p.add_argument("text")
    p = sub.add_parser("forget"); p.add_argument("text", nargs="?")
    p = sub.add_parser("memory")
    for name in ("interrupt", "reset", "brains"):
        sub.add_parser(name)
    a = ap.parse_args()

    if a.cmd == "chat":
        r = call("/api/chat", {"text": text_arg(a.text), "brain": a.brain, "wait": not a.no_wait})
    elif a.cmd == "say":
        r = call("/api/say", {"text": text_arg(a.text), "emotion": a.emotion})
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
