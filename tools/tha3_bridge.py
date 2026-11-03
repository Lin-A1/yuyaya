"""THA3 渲染桥（可选）：用 talking-head-anime-3 在 GPU 上实时渲染大肥鱼，前端 ?render=tha3 时使用。

    # 在装了 CUDA PyTorch 的环境里运行；--tha3 指向 talking-head-anime-3-demo 源码目录（含 data/models）
    python tools/tha3_bridge.py --tha3 ~/yuyaya-lab/talking-head-anime-3-demo-main
    python tools/tha3_bridge.py --mock        # 不加载模型，只把立绘按姿态做简单旋转，用来联调前端

分工：前端照常计算所有动作（弹簧、眨眼、口型、表情切换），每帧把姿态 POST 过来；这里负责
    1. 合成输入图：当前表情底图 + 口型贴片 + 闭眼贴片 → 按 THA3 的要求摆进 512x512（头部落在上半部中间 128x128）
    2. THA3 只做转头 / 点头 / 歪头 / 身体转动 / 呼吸，表情和口型用的仍是生成的原画，不会被模型画坏
    3. 返回裁好的 RGBA 原始像素（本机传输，不压缩更快）

接口（允许跨域）：
    GET  /info    {"mode", "model", "layout": {x, y, w, h（立绘像素）, width, height（返回图尺寸）}}
    POST /frame   {"emo", "mouth": 0~3, "blink": 0|1, "pitch", "yaw", "roll", "lean", "breath"} → RGBA 字节
"""
import argparse, importlib, json, pathlib, queue, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SIZE = 512

# 前端姿态 → THA3 参数：(THA3 参数名, 前端字段, 倍数)。符号和幅度凭目测调整
POSE_MAP = [
    ("head_x", "pitch", -0.6),     # 前端 pitch>0 是低头
    ("head_y", "yaw", 0.8),
    ("neck_z", "roll", -4.0),      # 前端 roll 是弧度（约 ±0.15），THA3 方向相反
    ("body_y", "yaw", 0.25),
    ("body_z", "lean", 10.0),
    ("breathing", "breath", 1.0),
]


class Inputs:
    """合成并缓存 THA3 的输入图：(表情, 口型级别, 是否闭眼) → 512x512 RGBA。"""

    def __init__(self, head_px: int):
        self.spr = json.loads((WEB / "assets/manifest.json").read_text(encoding="utf-8"))["sprites"]
        rig = self.spr["rig"]
        self.s = head_px / (rig["neck"][1] - rig["headTop"][1])
        self.ox = SIZE / 2 - rig["face"][0] * self.s           # 立绘 → 512 画布的平移
        self.oy = SIZE * 3 / 8 - rig["neck"][1] * self.s       # 下巴对齐 y=192
        self.files, self.cache = {}, {}
        # 只返回人物附近的区域（留出动作空间），传输量约为整图的 40%
        bb = self.image("neutral", 0, False).getchannel("A").getbbox()
        m = 40
        self.crop = (max(bb[0] - m, 0), max(bb[1] - m, 0), min(bb[2] + m, SIZE), min(bb[3] + m // 2, SIZE))

    def _open(self, src: str) -> Image.Image:
        if src not in self.files:
            self.files[src] = Image.open(WEB / src).convert("RGBA")
        return self.files[src]

    def image(self, emo: str, mouth: int, blink: bool) -> Image.Image:
        key = (emo, mouth, blink)
        if key not in self.cache:
            e = self.spr["emotions"].get(emo) or self.spr["emotions"]["neutral"]
            im = self._open(e["src"]).copy()
            layers = [e["mouth"][min(mouth, len(e["mouth"])) - 1] if mouth and e["mouth"] else None,
                      e["blink"] if blink else None]
            for p in filter(None, layers):
                im.alpha_composite(self._open(p["src"]), (p["x"], p["y"]))
            im = im.resize((round(im.width * self.s), round(im.height * self.s)), Image.LANCZOS)
            canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
            canvas.alpha_composite(im, (round(self.ox), round(self.oy)))
            self.cache[key] = canvas
        return self.cache[key]

    def layout(self) -> dict:
        x0, y0, x1, y1 = self.crop
        return {"x": (x0 - self.ox) / self.s, "y": (y0 - self.oy) / self.s,
                "w": (x1 - x0) / self.s, "h": (y1 - y0) / self.s, "width": x1 - x0, "height": y1 - y0}


class Tha3Renderer:
    def __init__(self, src: pathlib.Path, model: str, inputs: Inputs):
        import torch
        sys.path.insert(0, str(src))
        mod = importlib.import_module(f"tha3.poser.modes.{model}")
        self.torch, self.inputs = torch, inputs
        self.dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        torch.backends.cudnn.benchmark = True   # 不开的话 separable 模型的深度卷积会慢好几倍
        files = {n.name: str(src / f"data/models/{model}/{n.name}.pt") for n in mod.Network}
        self.poser = mod.create_poser(self.dev, files)
        self.dtype = self.poser.get_dtype()
        names = [n for g in self.poser.get_pose_parameter_groups() for n in g.get_parameter_names()]
        self.n = len(names)
        self.index = {k: [i for i, n in enumerate(names) if n == k] for k, _, _ in POSE_MAP}
        self.tensors = {}
        self.render({"emo": "neutral"})   # 预热：第一次推理要编译 CUDA kernel

    def _tensor(self, key):
        if key not in self.tensors:
            torch = self.torch
            a = torch.from_numpy(np.asarray(self.inputs.image(*key), np.float32) / 255).to(self.dev)
            a[a[..., 3] <= 0] = 0                                   # THA3 要求全透明像素为 0
            rgb = a[..., :3]
            rgb = torch.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)   # sRGB → 线性
            t = torch.cat([rgb, a[..., 3:]], 2).permute(2, 0, 1) * 2 - 1
            self.tensors[key] = t.to(self.dtype).unsqueeze(0)
        return self.tensors[key]

    def render(self, p: dict) -> bytes:
        torch = self.torch
        pose = torch.zeros(1, self.n, device=self.dev, dtype=self.dtype)
        for name, field, k in POSE_MAP:
            v = max(-1.0, min(1.0, float(p.get(field, 0)) * k))
            for i in self.index[name]:
                pose[0, i] = v
        key = (p.get("emo", "neutral"), int(p.get("mouth", 0)), bool(p.get("blink")))
        x0, y0, x1, y1 = self.inputs.crop
        with torch.no_grad():
            o = self.poser.pose(self._tensor(key), pose)[0, :, y0:y1, x0:x1].float()
            o = ((o + 1) / 2).clamp(0, 1)
            rgb = o[:3]
            rgb = torch.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)   # 线性 → sRGB
            out = torch.cat([rgb, o[3:]]).mul(255).add(0.5).to(torch.uint8).permute(1, 2, 0).contiguous()
        return out.cpu().numpy().tobytes()


class MockRenderer:
    """不做推理：把合成好的输入图按 roll 绕脖子旋转、按 yaw 平移，验证前端链路和对位。"""

    def __init__(self, inputs: Inputs):
        self.inputs = inputs

    def render(self, p: dict) -> bytes:
        im = self.inputs.image(p.get("emo", "neutral"), int(p.get("mouth", 0)), bool(p.get("blink")))
        neck = (SIZE / 2, SIZE * 3 / 8)
        im = im.rotate(-np.degrees(float(p.get("roll", 0))), Image.BILINEAR, center=neck,
                       translate=(float(p.get("yaw", 0)) * 6, float(p.get("pitch", 0)) * 4))
        return im.crop(self.inputs.crop).tobytes()


def serve(renderer, info: dict, host: str, port: int):
    """HTTP 在后台线程收请求，推理全部在主线程（加载模型的线程）里做。
    CUDA / cuDNN 的句柄和算法缓存是跟线程走的，每个连接一个新线程去推理会慢几十倍。"""
    jobs: queue.Queue = queue.Queue()
    stats = {"n": 0, "ms": 0.0}

    def render(p: dict) -> bytes:
        box, done = {}, threading.Event()
        jobs.put((p, box, done))
        done.wait()
        if "err" in box:
            raise box["err"]
        return box["data"]

    class Handler(BaseHTTPRequestHandler):
        def _head(self, code=200, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Expose-Headers", "X-Render-Ms")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def do_OPTIONS(self):
            self._head(204)

        def do_GET(self):
            if self.path != "/info":
                return self._head(404)
            self._head()
            self.wfile.write(json.dumps({**info, "avg_ms": stats["ms"]}).encode())

        def do_POST(self):
            if self.path != "/frame":
                return self._head(404)
            p = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            t0 = time.perf_counter()
            data = render(p)
            ms = (time.perf_counter() - t0) * 1000
            stats["ms"] = ms if not stats["n"] else stats["ms"] * 0.95 + ms * 0.05
            stats["n"] += 1
            self._head(200, "application/octet-stream", {"Content-Length": str(len(data)), "X-Render-Ms": f"{ms:.1f}"})
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"THA3 bridge ({info['mode']}) on http://{host}:{port}  layout={info['layout']}", flush=True)
    while True:
        try:
            p, box, done = jobs.get(timeout=0.5)   # 带超时，Ctrl+C 才能退出
        except queue.Empty:
            continue
        try:
            box["data"] = renderer.render(p)
        except Exception as e:
            box["err"] = e
        done.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tha3", type=pathlib.Path, help="talking-head-anime-3-demo 源码目录")
    ap.add_argument("--model", default="separable_half",
                    choices=["standard_float", "standard_half", "separable_float", "separable_half"])
    ap.add_argument("--head", type=int, default=128, help="头部（头顶到下巴）缩放到多少像素，THA3 要求不超过 128 左右")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8849)
    a = ap.parse_args()
    inputs = Inputs(a.head)
    if a.mock:
        renderer, mode = MockRenderer(inputs), "mock"
    elif a.tha3:
        renderer, mode = Tha3Renderer(a.tha3.expanduser(), a.model, inputs), "tha3"
    else:
        sys.exit("需要 --tha3 <源码目录> 或 --mock")
    serve(renderer, {"mode": mode, "model": a.model if mode == "tha3" else None, "layout": inputs.layout()},
          a.host, a.port)


if __name__ == "__main__":
    main()
