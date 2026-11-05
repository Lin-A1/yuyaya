/* 立绘"骨骼"：把一张静态立绘切成网格，按头 / 身体 / 头发 / 呆毛 / 耳朵的参数实时形变。
 *
 * 纯数学，不碰 DOM / WebGL。avatar.js 每帧把参数交给 rig.deform()，拿到新的顶点坐标去画。
 * 坐标都是立绘像素（manifest.sprites.width × height），骨骼点来自 manifest.sprites.rig。
 */
const clamp01 = (x) => Math.min(Math.max(x, 0), 1);
const smooth = (a, b, x) => { const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };

/** 阻尼弹簧：所有参数都经过它，动作之间自然过渡、带一点惯性和回弹。 */
class Spring {
  constructor(k = 120, d = 18, x = 0) { this.k = k; this.d = d; this.x = x; this.v = 0; }
  step(target, dt) {
    this.v += (this.k * (target - this.x) - this.d * this.v) * dt;
    this.x += this.v * dt;
    return this.x;
  }
  kick(v) { this.v += v; }
}

/* 一次性动作：t 从 0 走到 1，往 o 里叠加参数增量。
 * roll/lean 弧度；yaw/pitch 为 -1~1（转头 / 低头程度）；dx/dy 立绘像素（不经过弹簧，直接生效）；
 * sq 压扁(+)/拉长(-)；ear 耳朵上扬(+)/耷拉(-)。 */
const bump = (t, a, b) => smooth(0, a, t) * smooth(1, b, t);   // 先升后降的包络
const MOTIONS = {
  nod:     { dur: 0.75, f: (t, o) => { o.pitch += 0.9 * Math.max(0, Math.sin(Math.PI * 4 * t)) * (1 - 0.4 * t); } },
  shake:   { dur: 0.9,  f: (t, o) => { o.yaw += 0.85 * Math.sin(Math.PI * 6 * t) * (1 - t); o.pitch += 0.15 * bump(t, 0.2, 0.7); } },
  hop:     { dur: 0.5,  f: (t, o) => hopCurve(t, o, 34, 0.05) },
  jump:    { dur: 0.75, f: (t, o) => { hopCurve(t, o, 80, 0.08); o.ear += 0.35 * bump(t, 0.2, 0.6); o.pitch -= 0.3 * bump(t, 0.2, 0.6); } },
  droop:   { dur: 2.2,  f: (t, o) => { const e = bump(t, 0.2, 0.7); o.pitch += 0.8 * e; o.dy += 10 * e; o.ear -= 0.45 * e; o.roll += 0.04 * e; o.sq += 0.015 * e; } },
  tremble: { dur: 1.0,  f: (t, o) => { const e = 1 - t; o.dx += 2.6 * Math.sin(Math.PI * 2 * 17 * t) * e; o.ear += 0.06 * Math.sin(Math.PI * 2 * 13 * t) * e; o.pitch += 0.2 * e; } },
  tilt:    { dur: 1.6,  f: (t, o) => { const e = bump(t, 0.25, 0.65); o.roll += 0.15 * e; o.yaw += 0.25 * e; o.ear += 0.08 * e; } },
  wiggle:  { dur: 1.1,  f: (t, o) => { const s = Math.sin(Math.PI * 4 * t) * (1 - 0.5 * t); o.lean += 0.045 * s; o.roll -= 0.06 * s; } },
  startle: { dur: 0.45, f: (t, o) => { const e = bump(t, 0.12, 0.5); o.pitch -= 0.7 * e; o.dy -= 12 * e; o.ear += 0.4 * e; o.sq -= 0.03 * e; } },
  // 抽两下鼻子：哭完正在平复
  sniff:   { dur: 0.9,  f: (t, o) => { const s = Math.max(0, Math.sin(Math.PI * 4 * t)) ** 3 * (1 - 0.5 * t); o.pitch -= 0.3 * s; o.dy -= 3 * s; o.ear += 0.08 * s; } },
  // 叹一口气：先吸气挺起来，再呼气塌下去，慢慢回正
  sigh:    { dur: 1.8,  f: (t, o) => {
    const a = smooth(0, 0.3, t) * (1 - smooth(0.3, 0.5, t)), b = smooth(0.3, 0.5, t) * (1 - smooth(0.6, 1, t));
    o.dy += -5 * a + 6 * b; o.pitch += -0.2 * a + 0.35 * b; o.sq += -0.015 * a + 0.012 * b; o.ear += 0.05 * a - 0.12 * b;
  } },
  // 凑近：身体前倾、头低一点，像在认真听或者想看清楚
  lean:    { dur: 1.4,  f: (t, o) => { const e = bump(t, 0.15, 0.75); o.pitch += 0.28 * e; o.dy += 6 * e; o.sq += 0.012 * e; o.ear += 0.12 * e; } },
};

/** 起跳：蹲（压扁）→ 腾空 → 落地再压一下。 */
function hopCurve(t, o, height, squash) {
  if (t < 0.18) o.sq += squash * Math.sin(Math.PI * t / 0.18);
  else if (t < 0.82) { const u = (t - 0.18) / 0.64; o.dy -= height * Math.sin(Math.PI * u); o.sq -= squash * 0.5 * Math.sin(Math.PI * u); }
  else o.sq += squash * 0.7 * Math.sin(Math.PI * (t - 0.82) / 0.18);
}

class Rig {
  constructor(spr, cols = 40, rows = 50) {
    const W = (this.W = spr.width), H = (this.H = spr.height), r = (this.r = spr.rig);
    this.cx = r.neck[0];
    const n = (cols + 1) * (rows + 1);
    this.rest = new Float32Array(n * 2);
    this.pos = new Float32Array(n * 2);
    this.w = { head: new Float32Array(n), face: new Float32Array(n), chest: new Float32Array(n),
               hair: new Float32Array(n), ahoge: new Float32Array(n), earL: new Float32Array(n), earR: new Float32Array(n) };
    const [ax, ay] = r.ahoge[0], ahogeLen = r.ahoge[0][1] - r.ahoge[1][1];
    const th = r.torsoHalf, hairTop = r.neck[1] - 120;
    let i = 0;
    for (let y = 0; y <= rows; y++) for (let x = 0; x <= cols; x++, i++) {
      const px = (x / cols) * W, py = (y / rows) * H;
      this.rest[i * 2] = px; this.rest[i * 2 + 1] = py;
      const dx = px - this.cx;
      this.w.head[i] = smooth(r.neck[1] + 50, r.neck[1] - 70, py);
      const fx = (px - r.face[0]) / 340, fy = (py - r.face[1]) / 300;
      this.w.face[i] = clamp01(1 - fx * fx - fy * fy) ** 1.5;
      this.w.chest[i] = smooth(H, r.neck[1] + 60, py);
      this.w.hair[i] = smooth(th * 0.95, th * 1.25, Math.abs(dx)) * clamp01((py - hairTop) / (H - hairTop)) ** 1.3;
      this.w.ahoge[i] = clamp01((ay - py) / ahogeLen) * smooth(115, 75, Math.abs(px - ax));
      this.w.earL[i] = earWeight(px, py, r.earL);
      this.w.earR[i] = earWeight(px, py, r.earR);
    }
    const idx = [];
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const a = y * (cols + 1) + x, b = a + 1, c = a + cols + 1, d = c + 1;
      idx.push(a, b, c, b, d, c);
    }
    this.index = new Uint16Array(idx);
  }

  /** p: { roll, yaw, pitch, lean, breath, dx, dy, sq, hair, ahoge, earL, earR } → this.pos */
  deform(p) {
    const { rest, pos, w, r, W, H, cx } = this;
    const [nx, ny] = r.neck, [ax, ay] = r.ahoge[0], [lx, ly] = r.earL[0], [rx, ry] = r.earR[0];
    const sy = 1 + p.sq, sx = 1 - p.sq * 0.6;
    const cl = Math.cos(p.lean), sl = Math.sin(p.lean);
    for (let i = 0, n = rest.length / 2; i < n; i++) {
      let x = rest[i * 2], y = rest[i * 2 + 1];
      // 挂在头上的小部件先在自己的根部转
      if (w.ahoge[i]) [x, y] = rot(x, y, ax, ay, p.ahoge * w.ahoge[i]);
      if (w.earL[i]) [x, y] = rot(x, y, lx, ly, p.earL * w.earL[i]);
      if (w.earR[i]) [x, y] = rot(x, y, rx, ry, -p.earR * w.earR[i]);
      x += p.hair * w.hair[i];
      // 转头 / 低头：脸中心移得多、轮廓移得少，看起来像有一点立体感
      const wh = w.head[i];
      x += (p.yaw * 18 * w.face[i] + p.yaw * 5 * wh);
      y += (p.pitch * 14 * w.face[i] + p.pitch * 5 * wh);
      if (wh) [x, y] = rot(x, y, nx, ny, p.roll * wh);
      // 呼吸：胸口以上整体微微上提、躯干微微变宽
      y -= p.breath * 4 * w.chest[i];
      x += (x - cx) * p.breath * 0.004 * w.chest[i];
      // 压扁拉长（以底边为基准）+ 整体倾斜 + 平移
      x = cx + (x - cx) * sx; y = H - (H - y) * sy;
      const ox = x - cx, oy = y - H;
      pos[i * 2] = cx + ox * cl - oy * sl + p.dx;
      pos[i * 2 + 1] = H + ox * sl + oy * cl + p.dy;
    }
    return pos;
  }
}

function rot(x, y, ox, oy, a) {
  const c = Math.cos(a), s = Math.sin(a), dx = x - ox, dy = y - oy;
  return [ox + dx * c - dy * s, oy + dx * s + dy * c];
}

/** 耳朵：沿 根部→尖端 方向投影，靠近耳朵中轴的点才跟着转。 */
function earWeight(px, py, [[rx, ry], [tx, ty]]) {
  const dx = tx - rx, dy = ty - ry, L2 = dx * dx + dy * dy, L = Math.sqrt(L2);
  const s = ((px - rx) * dx + (py - ry) * dy) / L2;
  const perp = Math.abs((px - rx) * dy - (py - ry) * dx) / L;
  return smooth(-0.1, 0.25, s) * smooth(1.4, 1.1, s) * smooth(0.75, 0.45, perp / L);
}
