/* 可选：THA3 渲染（页面加 ?render=tha3，桥地址默认 http://127.0.0.1:8849，可用 &tha3=URL 修改）。
 *
 * 动作仍由 avatar.js 计算，这里把每帧姿态发给 tools/tha3_bridge.py，拿回 RGBA 像素画到 canvas 上。
 * 同一时间只有一个请求在路上，GPU 跟不上时自动丢帧（总是发最新的姿态）。
 * 桥没开或断开时 ready=false，avatar 自动用回 WebGL 网格渲染，并每 5 秒重连一次。
 */
class Tha3View {
  constructor(charEl, spr, url = "http://127.0.0.1:8849") {
    this.url = url.replace(/\/$/, "");
    this.spr = spr;
    this.canvas = document.createElement("canvas");
    Object.assign(this.canvas.style, { position: "absolute", display: "none" });
    charEl.querySelector("#char-body").appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.ready = false;
    this.busy = false;
    this.latest = null;
    this.fps = 0;
    this.renderMs = 0;
    this._frames = 0;
    setInterval(() => { this.fps = this._frames / 2; this._frames = 0; }, 2000);
    this.connect();
  }

  async connect() {
    try {
      const info = await fetch(this.url + "/info").then((r) => r.json());
      const L = (this.layout = info.layout), { width: W, height: H } = this.spr;
      this.canvas.width = L.width;
      this.canvas.height = L.height;
      // 返回图在立绘坐标里的位置 → 相对 #char-body 的百分比
      Object.assign(this.canvas.style, {
        display: "", left: (L.x / W) * 100 + "%", top: (L.y / H) * 100 + "%",
        width: (L.w / W) * 100 + "%", height: (L.h / H) * 100 + "%",
        transformOrigin: `50% ${((H - L.y) / L.h) * 100}%`,   // 压扁拉长以立绘底边为基准
      });
      this.info = info;
      this.ready = true;
      console.info("[tha3] connected", info);
    } catch {
      this.ready = false;
      this.canvas.style.display = "none";
      setTimeout(() => this.connect(), 5000);
    }
  }

  /** 每帧调用：p 是 avatar 的姿态参数，另含 emo / mouth / blink。 */
  update(p, pxPerSprite) {
    // 蹦跳的位移和压扁 THA3 不管，直接用 CSS 变换叠加
    this.canvas.style.transform = `translateY(${p.dy * pxPerSprite}px) scale(${1 - p.sq * 0.6}, ${1 + p.sq})`;
    this.latest = { emo: p.emo, mouth: p.mouth, blink: p.blink, pitch: p.pitch, yaw: p.yaw,
                    roll: p.roll, lean: p.lean, breath: p.breath };
    if (!this.busy) this._send();
  }

  async _send() {
    this.busy = true;
    while (this.latest && this.ready) {
      const body = JSON.stringify(this.latest);
      this.latest = null;
      try {
        const r = await fetch(this.url + "/frame", { method: "POST", body });   // text/plain，不触发预检
        const buf = await r.arrayBuffer();
        this.renderMs = +r.headers.get("X-Render-Ms") || 0;
        this.ctx.putImageData(new ImageData(new Uint8ClampedArray(buf), this.canvas.width, this.canvas.height), 0, 0);
        this._frames++;
      } catch {
        console.warn("[tha3] bridge lost, falling back to WebGL");
        this.ready = false;
        this.canvas.style.display = "none";
        setTimeout(() => this.connect(), 5000);
      }
    }
    this.busy = false;
  }
}
