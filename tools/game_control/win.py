"""Windows 桌面控制内核：截图 / 鼠标 / 键盘 / 窗口。

只用标准库 ctypes；Pillow 有就用 Pillow 截图+缩放，没有就退回纯 ctypes 的 GDI 抓屏
（自己编码 PNG）。三种外壳（shell CLI / MCP / HTTP）都调这里，逻辑只有一份。

⚠ DPI 是 Windows 上最大的坑：不设进程 DPI 感知的话，125%/150% 缩放下
"截图里的像素" 和 "鼠标坐标" 不是一回事，agent 点哪儿都偏。所以导入这个模块
第一件事就是设置 DPI 感知。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import io
import json
import os
import pathlib
import struct
import time
import zlib

# ---- DPI 感知：必须在任何坐标 API 之前设置 ----
DPI_MODE = "none"
try:
    if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
        DPI_MODE = "per-monitor-v2"
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        DPI_MODE = "system"
    except Exception:
        DPI_MODE = "none"

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
try:
    shcore = ctypes.windll.shcore
except Exception:
    shcore = None

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

# ---- 类型与函数签名（不写全 argtypes，句柄会被当成 32 位 int，64 位上直接 OverflowError）----
user32.GetDC.restype = w.HDC
user32.GetDC.argtypes = [w.HWND]
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = w.BOOL
user32.keybd_event.argtypes = [w.BYTE, w.BYTE, w.DWORD, ctypes.c_void_p]
user32.mouse_event.argtypes = [w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.IsWindowVisible.argtypes = [w.HWND]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]

gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleBitmap.restype = w.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
gdi32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, w.UINT]

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
                ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


# ---------------- 屏幕 ----------------

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def virtual_screen() -> tuple[int, int, int, int]:
    """整个虚拟桌面 (x, y, w, h)。多显示器时坐标和 SetCursorPos 一致。"""
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w_ = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h_ = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w_, h_


# ---------------- 截图 ----------------

def _capture_gdi(region: tuple[int, int, int, int] | None) -> tuple[int, int, bytes]:
    """纯 ctypes 抓屏，返回 (宽, 高, BGRA 像素)。"""
    vx, vy, vw, vh = virtual_screen()
    x, y, cw, ch = region or (vx, vy, vw, vh)
    # 屏幕 DC 的原点在主显示器左上角（Windows 总是把主显示器放在 0,0），
    # 而我们的坐标是"虚拟桌面"坐标（SetCursorPos 用的那套），两者差 (vx, vy)。
    sx, sy = x - vx, y - vy
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, cw, ch)
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.BitBlt(mem, 0, 0, cw, ch, hdc, sx, sy, SRCCOPY)
        hdr = _BITMAPINFOHEADER()
        hdr.biSize = ctypes.sizeof(hdr)
        hdr.biWidth = cw
        hdr.biHeight = -ch          # 负数 = 自上而下
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(cw * ch * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, ch, buf, ctypes.byref(hdr), DIB_RGB_COLORS):
            raise RuntimeError("GetDIBits 失败")
        return cw, ch, buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, hdc)


def _encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """把 RGBA 像素编成 PNG（纯标准库）。"""
    raw = bytearray()
    stride = width * 4
    for row in range(height):
        raw.append(0)                       # 每行的 filter 类型
        raw += rgba[row * stride:(row + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)   # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _bgra_to_rgba(bgra: bytes) -> bytes:
    out = bytearray(len(bgra))
    out[0::4] = bgra[2::4]     # R
    out[1::4] = bgra[1::4]     # G
    out[2::4] = bgra[0::4]     # B
    out[3::4] = bgra[3::4]     # A
    return bytes(out)


def _region_or_virtual(region: tuple[int, int, int, int] | None) -> tuple[int, int, int, int]:
    """要截的区域 (x, y, w, h)，没指定就是整个虚拟桌面。"""
    vx, vy, vw, vh = virtual_screen()
    if region:
        x, y, cw, ch = (int(v) for v in region)
        return x, y, cw, ch
    return vx, vy, vw, vh


def _capture_pil():
    """Pillow 抓屏。返回 (图像, 图像左上角对应的屏幕 x, 屏幕 y)。"""
    from PIL import ImageGrab
    vx, vy, _vw, _vh = virtual_screen()
    try:
        # all_screens：抓整个虚拟桌面，图像左上角 = 虚拟桌面左上角
        return ImageGrab.grab(all_screens=True), vx, vy
    except Exception:
        # 退路：只抓主显示器，它的左上角就是屏幕 (0,0)
        return ImageGrab.grab(), 0, 0


def _screenshot_png(max_width: int, region=None):
    """截屏。返回 (png 字节, 图像宽, 图像高, 区域 x, 区域 y, 区域宽, 区域高)。"""
    rx, ry, rw, rh = _region_or_virtual(region)
    try:
        from PIL import ImageGrab
    except ImportError:
        ImageGrab = None

    if ImageGrab is not None:
        im, ox, oy = _capture_pil()
        # im 的原点在 (ox, oy)，区域要换算过去再裁
        im = im.crop((rx - ox, ry - oy, rx - ox + rw, ry - oy + rh))
        if max_width and im.width > max_width:
            h = max(1, int(im.height * max_width / im.width))
            im = im.resize((max_width, h))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), im.width, im.height, rx, ry, rw, rh

    w_, h_, bgra = _capture_gdi(region)
    rgba = _bgra_to_rgba(bgra)
    if max_width and w_ > max_width:
        # 纯 ctypes 缩放：最近邻取样，够用了
        nw = max_width
        nh = max(1, int(h_ * nw / w_))
        small = bytearray(nw * nh * 4)
        for y in range(nh):
            sy = min(h_ - 1, y * h_ // nh)
            row_off = sy * w_ * 4
            base = y * nw * 4
            for x in range(nw):
                sx = min(w_ - 1, x * w_ // nw)
                s = row_off + sx * 4
                o = base + x * 4
                small[o:o + 4] = rgba[s:s + 4]
        return _encode_png(nw, nh, bytes(small)), nw, nh, rx, ry, rw, rh
    return _encode_png(w_, h_, rgba), w_, h_, rx, ry, rw, rh


# ---- 上一次截图的换算信息（截图上的像素 ↔ 真实屏幕坐标）----
_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_SHOT_FILE = pathlib.Path(os.environ.get("YUYAYA_GAME_STATE")
                          or (_ROOT / "state" / "last_shot.json"))
_LAST_SHOT: dict = {}


def _remember_shot(iw: int, ih: int, rx: int, ry: int, rw: int, rh: int) -> dict:
    # scale = 一个图像像素对应几个屏幕像素（截图缩到一半 → scale = 2）
    scale = (rw / iw) if iw else 1.0
    data = {"scale": scale, "origin": [rx, ry], "image": [iw, ih], "region": [rx, ry, rw, rh]}
    _LAST_SHOT.clear()
    _LAST_SHOT.update(data)
    try:
        _SHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SHOT_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return data


def shot_info() -> dict:
    """上一次截图的换算信息。

    scale  = 真实像素 / 图像像素（截图被缩放过就 > 1）
    origin = 图像左上角对应的真实屏幕坐标
    """
    if not _LAST_SHOT:
        try:
            data = json.loads(_SHOT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _LAST_SHOT.update(data)
        except Exception:
            pass
    return dict(_LAST_SHOT)


def to_screen(x: float, y: float, scale: float | None = None) -> tuple[int, int, float]:
    """把"截图上的坐标"换算成"真实屏幕坐标"。

    scale 省略时用上一次截图的比例；截图还没截过时按 1:1 处理。
    """
    info = shot_info()
    s = float(scale) if scale else float(info.get("scale") or 1.0)
    ox, oy = (info.get("origin") or [0, 0])[:2]
    return int(round(ox + x * s)), int(round(oy + y * s)), s


def screenshot(max_width: int = 1280, region: tuple[int, int, int, int] | None = None,
               fmt: str = "png") -> bytes:
    """截屏。返回 PNG 字节。"""
    png, iw, ih, rx, ry, rw, rh = _screenshot_png(max_width, region)
    _remember_shot(iw, ih, rx, ry, rw, rh)
    return png


def screenshot_meta(max_width: int = 1280, region: tuple[int, int, int, int] | None = None) -> dict:
    """截屏 + 坐标换算信息。给 agent 用：它照着图上的像素点，坐标不会偏。"""
    png, iw, ih, rx, ry, rw, rh = _screenshot_png(max_width, region)
    _remember_shot(iw, ih, rx, ry, rw, rh)
    scale = (rw / iw) if iw else 1.0
    return {"png": png, "width": iw, "height": ih, "scale": round(scale, 6),
            "origin": [rx, ry], "region": [rx, ry, rw, rh],
            "screen": list(screen_size()), "virtual": list(virtual_screen()),
            "dpi_mode": DPI_MODE}



# ---------------- 鼠标 ----------------

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800

_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def set_cursor(x: int, y: int) -> None:
    if not user32.SetCursorPos(int(x), int(y)):
        raise RuntimeError(f"移动鼠标到 ({x}, {y}) 失败")


def click(x: int, y: int, button: str = "left", clicks: int = 1, delay: float = 0.05,
          scale: float | None = None, raw: bool = False) -> str:
    """点击。

    x / y 默认是"截图上的像素坐标"（和 game_screenshot 看到的一致），
    内部按上一次截图的比例换算成真实屏幕坐标；raw=True 表示已经是屏幕坐标。
    """
    down, up = _BUTTONS.get((button or "left").lower(), _BUTTONS["left"])
    if raw:
        sx, sy, s = int(x), int(y), 1.0
    else:
        sx, sy, s = to_screen(x, y, scale)
    set_cursor(sx, sy)
    time.sleep(0.02)
    for _ in range(max(1, clicks)):
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(delay)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(delay)
    return f"已在屏幕 ({sx}, {sy}) {button} 点击 {clicks} 次（图上坐标 {x},{y}，缩放 {s:g}）"


def move(x: int, y: int, scale: float | None = None, raw: bool = False) -> str:
    if raw:
        sx, sy, s = int(x), int(y), 1.0
    else:
        sx, sy, s = to_screen(x, y, scale)
    set_cursor(sx, sy)
    return f"鼠标移到屏幕 ({sx}, {sy})（图上坐标 {x},{y}，缩放 {s:g}）"



def scroll(clicks: int) -> str:
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(clicks) * 120, 0)
    return f"滚动了 {clicks} 格"


# ---------------- 键盘 ----------------

VK = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
KEYEVENTF_KEYUP = 0x0002


def vk_code(name: str) -> int:
    n = (name or "").strip()
    if not n:
        raise ValueError("按键名不能为空")
    low = n.lower()
    if low in VK:
        return VK[low]
    if len(n) == 1:
        c = ord(n)
        if 0x21 <= c <= 0x7E:            # 可打印 ASCII
            return c
        raise ValueError(f"不支持的按键 {name!r}")
    raise ValueError(f"未知的按键 {name!r}（可用：{', '.join(sorted(VK))}）")


def key(name: str, action: str = "press", hold: float = 0.05) -> str:
    """action: press（按下松开）/ down / up"""
    vk = vk_code(name)
    if action == "down":
        user32.keybd_event(vk, 0, 0, 0)
        return f"按下 {name}"
    if action == "up":
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        return f"松开 {name}"
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(hold)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return f"按了 {name}"


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.POINTER(ULONG_PTR))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT


KEYEVENTF_UNICODE = 0x0004


def type_text(text: str, delay: float = 0.02) -> str:
    """输入一段文字（非 ASCII 走 Unicode 输入）。"""
    for ch in text:
        if 0x20 <= ord(ch) < 0x7F:
            vk = vk_code(ch.upper())
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(delay)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        else:
            for flags, code in ((0, ord(ch)), (KEYEVENTF_KEYUP, ord(ch))):
                inp = _INPUT()
                inp.type = 1                        # INPUT_KEYBOARD
                inp.u.ki.wVk = 0
                inp.u.ki.wScan = code
                inp.u.ki.dwFlags = KEYEVENTF_UNICODE | flags
                inp.u.ki.time = 0
                inp.u.ki.dwExtraInfo = None
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(delay)
    return f"输入了 {len(text)} 个字符"


# ---------------- 窗口 ----------------

def list_windows(only_visible: bool = True) -> list[dict]:
    out: list[dict] = []
    info = shot_info()
    scale = float(info.get("scale") or 1.0) or 1.0
    ox, oy = (info.get("origin") or [0, 0])[:2]

    @WNDENUMPROC
    def cb(hwnd, _lparam):
        if only_visible and not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if not title:
            return True
        rect = w.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append({"hwnd": int(hwnd), "title": title,
                    "x": rect.left, "y": rect.top,
                    "w": rect.right - rect.left, "h": rect.bottom - rect.top,
                    # 图上的位置（和 game_screenshot 同一套坐标），方便直接点
                    "ix": int(round((rect.left - ox) / scale)),
                    "iy": int(round((rect.top - oy) / scale))})
        return True

    user32.EnumWindows(cb, 0)
    return out


def focus(title: str) -> str:
    for win in list_windows():
        if title.lower() in win["title"].lower():
            user32.SetForegroundWindow(win["hwnd"])
            return f"已聚焦窗口：{win['title']}"
    raise RuntimeError(f"没找到标题含 {title!r} 的窗口")


def info() -> dict:
    return {"dpi_mode": DPI_MODE, "screen": screen_size(), "virtual": virtual_screen(),
            "pid": os.getpid(), "last_shot": shot_info()}
