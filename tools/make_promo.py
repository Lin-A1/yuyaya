# -*- coding: utf-8 -*-
"""录一段宣传视频：虚拟时钟逐帧驱动页面 + 真实 TTS 音轨 + ffmpeg 合成。

为什么要虚拟时钟：无头 Edge 的截图速度只有 3~4 fps，但动画都是"时间戳的函数"。
所以把 performance.now() 换成我们自己推进的虚拟时钟，每抓一帧就前进 1/24 秒，
最后按 24fps 编码——动画和字幕节奏都是对的，只是抓帧比实时慢。

用法：
    python tools/make_promo.py                 # 默认输出 docs/promo.mp4
    python tools/make_promo.py --fps 24 --out docs/promo.mp4
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


# ---------------------------------------------------------------- 音轨

def synth(text: str, voice: str = "qiaoxiaojiao_v3.1", base: str = "http://127.0.0.1:8848") -> bytes:
    r = requests.post(f"{base}/api/tts", json={"text": text, "voice": voice,
                                              "provider": "qwen-realtime"}, timeout=120)
    r.raise_for_status()
    return r.content


def wav_stats(data: bytes) -> tuple[int, int, float]:
    with wave.open(__import__("io").BytesIO(data)) as w:
        return w.getframerate(), w.getnchannels(), w.getnframes() / w.getframerate()


# ---------------------------------------------------------------- 场景

SCENES = [
    # (说话内容, 情绪, 动作, 之后停多久)
    ("你好呀，我是鱼丫丫。", "happy", "hop", 0.9),
    ("住在这台旧电脑里。", "neutral", None, 0.8),
    ("想聊什么都可以。", "shy", "tilt", 0.8),
    ("也能让我帮你做事。", "thinking", "nod", 0.9),
    ("说完我就去弄。", "neutral", "lean", 0.7),
    ("弄好了会告诉你。", "happy", "hop", 1.0),
    ("随时可以打断我。", "surprised", "startle", 0.9),
    ("那我先待机啦。", "neutral", "sigh", 1.2),
]

OPENING = 1.6      # 开场留白（大字标题）
TITLE_FADE = 1.0   # 标题淡出时长
TAIL = 2.2         # 结尾停留（落款）


def build_timeline(fps: int, durations: list[float]) -> tuple[list[dict], float]:
    """把每个场景排到时间轴上：字幕从 t 开始，按字数均分推进。"""
    t = OPENING
    cues = []
    for (text, emo, motion, hold), dur in zip(SCENES, durations):
        cues.append({"start": t, "dur": dur, "text": text, "emotion": emo, "motion": motion})
        t += dur + hold
    return cues, t + TAIL


# ---------------------------------------------------------------- 页面驱动

SETUP = r"""
(() => {
  // 1) 虚拟时钟：动画、字幕、眨眼全部按它推进
  window.__vt = performance.now();
  performance.now = () => window.__vt;
  window.__advance = (dt) => { window.__vt += dt; window.avatar._frame(window.__vt); };
  // 2) 停掉真实 rAF 循环，避免和虚拟时钟打架（但保留一次手动驱动）
  window.requestAnimationFrame = () => 0;
  // 3) 页面 HUD 用 setInterval + 真实时钟刷新计时器，会盖掉我们写的值 —— 停掉它
  if (window.Hud && Hud.prototype.tick) Hud.prototype.tick = function () {};
  return "clock ready";
})()
"""


def page_script(cues: list[dict], total: float) -> str:
    """返回一段 JS：给定虚拟时间 t，把页面状态摆成那个时刻该有的样子。"""
    data = json.dumps(cues, ensure_ascii=False)
    js = r"""
(() => {
  const cues = __CUES__;
  const OPENING = __OPENING__;
  const TOTAL = __TOTAL__;
  // 开场 / 结尾大字
  const card = document.createElement('div');
  card.id = 'promo-card';
  Object.assign(card.style, {
    position: 'absolute', inset: '0', zIndex: '30', display: 'grid',
    placeItems: 'center', textAlign: 'center', pointerEvents: 'none',
    // 让标题落在"屏幕"那一块，而不是整个舞台
    paddingBottom: '12%',
    background: 'radial-gradient(ellipse at 50% 45%, rgba(3,6,5,.72), rgba(2,4,3,.96))',
    fontFamily: '"PingFang SC","Microsoft YaHei",system-ui,sans-serif',
    color: '#eef4f1', opacity: '0', transition: 'none',
  });
  card.innerHTML = `
    <div>
      <div style="font-size:11px;letter-spacing:.5em;opacity:.5;margin-bottom:18px">DIGITAL LIFE</div>
      <div style="font-size:52px;font-weight:700;letter-spacing:.16em;color:#fff;
                  text-shadow:0 0 40px rgba(127,225,178,.35)">鱼丫丫</div>
      <div style="font-size:14px;letter-spacing:.18em;opacity:.62;margin-top:16px">
        住在旧电脑屏幕里的数字生命</div>
      <div style="font-size:11px;letter-spacing:.3em;opacity:.35;margin-top:26px">YU · YAYA</div>
    </div>`;
  document.querySelector('#stage').appendChild(card);

  window.__applyAt = (t) => {
    const it = (window.__it = window.__it || {});
    const q = (sel) => document.querySelector(sel);
    // 会话计时：按虚拟时间算（页面 HUD 那个 setInterval 走真实时钟，这里直接盖掉）
    const sec = Math.max(0, Math.floor(t - 1.0));
    const timer = q('#timer');
    if (timer) {
      const p2 = (n) => String(n).padStart(2, '0');
      timer.textContent = `00:${p2(Math.floor(sec / 60))}:${p2(sec % 60)}`;
    }
    const bar = q('#progress');
    if (bar) bar.style.width = ((sec % 600) / 6).toFixed(2) + '%';
    // 标题卡：开场不透明随后淡出；最后 TAIL 段再淡入做落款
    const tailFrom = TOTAL - 1.5;
    if (t < OPENING) {
      card.style.opacity = String(Math.min(1, (OPENING - t) / 0.45));
    } else if (t > tailFrom) {
      card.style.opacity = String(Math.min(1, (t - tailFrom) / 0.55));
    } else {
      card.style.opacity = '0';
    }

    let showing = null;
    for (const c of cues) {
      const local = t - c.start;
      if (local < 0) continue;
      if (!it[c.start]) {
        it[c.start] = true;
        if (c.emotion) avatar.setEmotion(c.emotion, { instant: true });
        if (c.motion) avatar.motion(c.motion);
      }
      if (local <= c.dur + 0.5) showing = c;
    }
    const sub = q('#subtitle');
    if (sub) {
      // 保证字幕完整落在画面里（默认贴底 9.5%，录制窗口比例不同会被裁）
      sub.style.bottom = '13%';
      if (!showing) sub.textContent = '';
      else {
        const chars = [...showing.text];
        const local = t - showing.start;
        const shown = Math.max(0, Math.min(chars.length,
          Math.ceil(local / Math.max(showing.dur, 0.001) * chars.length)));
        sub.textContent = chars.slice(0, shown).join('');
        // 口型跟着念的节奏开合
        if (local > 0 && local < showing.dur) {
          avatar.setMouthLevel(0.5 + 0.5 * Math.sin(t * 13));
        }
      }
    }
    avatar._frame(window.__vt);
  };
  return 'timeline ready';
})()
"""
    return (js.replace("__CUES__", data)
              .replace("__OPENING__", repr(OPENING))
              .replace("__TOTAL__", repr(total)))


class Recorder:
    def __init__(self, ws_url: str, width: int, height: int):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=120)
        self.n = 0
        self.width, self.height = width, height

    def call(self, method: str, params: dict | None = None, timeout: float = 90) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error']}")
                return r.get("result", {})
        raise TimeoutError(method)

    def ev(self, expr: str, await_promise: bool = False):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": await_promise, "userGesture": True})
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(f"JS: {res.get('description')[:300]}\n{expr[:200]}")
        return res.get("value")

    def shot(self) -> bytes:
        r = self.call("Page.captureScreenshot", {"format": "jpeg", "quality": 92})
        return base64.b64decode(r["data"])

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def launch(port: int, url: str, width: int, height: int):
    profile = tempfile.mkdtemp(prefix="edge_promo_")
    proc = subprocess.Popen([
        EDGE, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--no-first-run", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--hide-scrollbars", "--force-device-scale-factor=1", "--mute-audio",
        f"--window-size={width},{height}", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, profile


def find_tab(port: int, needle: str, tries: int = 50) -> str:
    for _ in range(tries):
        try:
            for t in requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json():
                if t.get("type") == "page" and needle in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("找不到页面标签")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--out", default=str(ROOT / "docs" / "promo.mp4"))
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--voice", default="qiaoxiaojiao_v3.1")
    args = ap.parse_args()

    base = "http://127.0.0.1:8848"
    try:
        requests.get(base + "/api/settings", timeout=5)
    except Exception as e:
        sys.exit(f"先启动服务（python -m server）：{e}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="promo_"))
    print(f"[1/5] 合成配音 → {tmp}")
    wavs, durations = [], []
    for i, (text, *_rest) in enumerate(SCENES):
        data = synth(text, args.voice, base)
        f = tmp / f"line{i}.wav"
        f.write_bytes(data)
        rate, ch, dur = wav_stats(data)
        wavs.append((f, rate, ch))
        durations.append(dur)
        print(f"      「{text}」 {dur:.2f}s")

    cues, total = build_timeline(args.fps, durations)
    frames_total = int(total * args.fps)
    print(f"[2/5] 时间轴 {total:.1f}s / {frames_total} 帧 @ {args.fps}fps")

    port = 9395
    proc, profile = launch(port, base + "/?obs=1", args.width, args.height)
    frames_dir = tmp / "frames"
    frames_dir.mkdir()
    try:
        rec = Recorder(find_tab(port, "8848"), args.width, args.height)
        rec.call("Runtime.enable")
        rec.call("Page.enable")
        for _ in range(80):
            if rec.ev("typeof window.avatar === 'object' && typeof SettingsPanel !== 'undefined'"):
                break
            time.sleep(0.5)
        print("[3/5] 页面就绪，安装虚拟时钟")
        # 先让立绘真正画一帧出来，确认纹理已加载
        rec.ev("avatar._frame(performance.now()); 1")
        painted = rec.ev("""(() => {
            const c = document.querySelector('#char-body canvas');
            if (!c) return 'no canvas';
            const g = c.getContext('webgl2');
            return g ? 'canvas ' + c.width + 'x' + c.height : 'no gl';
        })()""")
        print("      立绘:", painted)
        print("      ", rec.ev(SETUP))
        print("      ", rec.ev(page_script(cues, total)))
        rec.ev("avatar.setState('idle'); 1")

        t0 = time.time()
        for i in range(frames_total):
            t = i / args.fps
            rec.ev(f"window.__applyAt({t:.4f})")
            (frames_dir / f"{i:05d}.jpg").write_bytes(rec.shot())
            if i % 60 == 0:
                done = i / max(time.time() - t0, 0.001)
                print(f"      帧 {i}/{frames_total}  ({done:.1f} fps 抓取)", flush=True)
        rec.close()
        print(f"[4/5] 抓帧完成，用时 {time.time() - t0:.0f}s")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print("[5/5] 合成音轨 + 编码")
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # 把每句配音按时间轴拼成一条 24k 单声道音轨
    rate, ch = wavs[0][1], wavs[0][2]
    mix = tmp / "voice.wav"
    with wave.open(str(mix), "wb") as out:
        out.setnchannels(ch)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * OPENING))
        for (f, _r, _c), cue in zip(wavs, cues):
            with wave.open(str(f)) as w:
                out.writeframes(w.readframes(w.getnframes()))
            gap = cue["dur"] - wav_stats(f.read_bytes())[2] + 0.0
            pad = max(0.0, 0.0)
            # 场景之间的停顿用结尾的留白补上
            out.writeframes(b"\x00\x00" * int(rate * (SCENES[cues.index(cue)][3])))
        out.writeframes(b"\x00\x00" * int(rate * TAIL))

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-framerate", str(args.fps), "-i", str(frames_dir / "%05d.jpg"),
           "-i", str(mix), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
           str(out_path)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "ignore")[-2000:])
        sys.exit("ffmpeg 编码失败")
    print(f"完成 → {out_path}  ({out_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
