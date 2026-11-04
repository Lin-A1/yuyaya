"""操作桌面：截图 / 点击 / 按键 / 窗口 —— 任何能执行 shell 的 agent 都能用。

    python tools/game.py shot --out shot.png        截屏（存 png）
    python tools/game.py click 640 360              点击
    python tools/game.py key w                      按键（press/down/up）
    python tools/game.py type "hello"               输入文字
    python tools/game.py windows                    列窗口
    python tools/game.py focus "记事本"              聚焦窗口
    python tools/game.py info                       屏幕尺寸 / DPI 模式

配套的操作入口见 server/actions.py。
三种外壳共用 tools/game_control/win.py 一份实现。

⚠ 坐标：click / move 收的是**截图上的像素坐标**（和 shot 出来的图一致）。
  截图缩放过也没关系 —— shot 会把缩放比例记下来，click 自动换算成真实屏幕坐标。
  已经是屏幕坐标的话加 --raw。
⚠ 独占全屏的游戏抓不到画面，请让游戏用窗口化 / 无边框模式。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from game_control import win  # noqa: E402


def _region(s: str | None):
    if not s:
        return None
    parts = [int(x) for x in s.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise SystemExit("--region 格式是 x,y,w,h")
    return tuple(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="操作桌面（截图/点击/按键）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("shot", help="截屏")
    p.add_argument("--out", default="", help="保存到哪个 png（留空就不存）")
    p.add_argument("--max-width", type=int, default=1280)
    p.add_argument("--region", default="", help="x,y,w,h")

    p = sub.add_parser("click", help="点击")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--button", default="left", choices=["left", "right", "middle"])
    p.add_argument("--clicks", type=int, default=1)
    p.add_argument("--scale", type=float, default=0.0,
                   help="图上坐标对应的缩放比例，默认用上次截图记下来的")
    p.add_argument("--raw", action="store_true", help="x/y 已经是屏幕坐标，不换算")

    p = sub.add_parser("move", help="移动鼠标")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--scale", type=float, default=0.0)
    p.add_argument("--raw", action="store_true")

    p = sub.add_parser("scroll", help="滚轮")
    p.add_argument("clicks", type=int)

    p = sub.add_parser("key", help="按键")
    p.add_argument("name")
    p.add_argument("--action", default="press", choices=["press", "down", "up"])

    p = sub.add_parser("type", help="输入一段文字")
    p.add_argument("text")

    p = sub.add_parser("windows", help="列出窗口")
    p.add_argument("--all", action="store_true", help="包括不可见的窗口")

    p = sub.add_parser("focus", help="聚焦窗口")
    p.add_argument("title")

    sub.add_parser("info", help="屏幕信息")

    a = ap.parse_args()
    try:
        if a.cmd == "shot":
            meta = win.screenshot_meta(max_width=a.max_width, region=_region(a.region))
            png = meta.pop("png")
            path = ""
            if a.out:
                out = pathlib.Path(a.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(png)
                path = str(out.resolve())
            meta["hint"] = (f"图 {meta['width']}x{meta['height']}，图像坐标 × {meta['scale']} "
                            f"= 屏幕坐标（click 自动换算，不用自己乘）")
            print(json.dumps({"ok": True, "bytes": len(png), "path": path, **meta},
                             ensure_ascii=False))
        elif a.cmd == "click":
            print(json.dumps({"ok": True, "detail": win.click(
                a.x, a.y, a.button, a.clicks,
                scale=a.scale or None, raw=a.raw)}, ensure_ascii=False))
        elif a.cmd == "move":
            print(json.dumps({"ok": True, "detail": win.move(
                a.x, a.y, scale=a.scale or None, raw=a.raw)}, ensure_ascii=False))
        elif a.cmd == "scroll":
            print(json.dumps({"ok": True, "detail": win.scroll(a.clicks)}, ensure_ascii=False))
        elif a.cmd == "key":
            print(json.dumps({"ok": True, "detail": win.key(a.name, a.action)}, ensure_ascii=False))
        elif a.cmd == "type":
            print(json.dumps({"ok": True, "detail": win.type_text(a.text)}, ensure_ascii=False))
        elif a.cmd == "windows":
            print(json.dumps({"ok": True, "windows": win.list_windows(not a.all)},
                             ensure_ascii=False, indent=2))
        elif a.cmd == "focus":
            print(json.dumps({"ok": True, "detail": win.focus(a.title)}, ensure_ascii=False))
        elif a.cmd == "info":
            print(json.dumps({"ok": True, **win.info()}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
