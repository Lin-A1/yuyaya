/* 数字人形象层（WebGL2）。
 *
 * 输入：setEmotion / setState / setTalking / motion(name) / syllable()（打字机每个字一次）/ beat()（句末）
 *       setMouthLevel / playMouthLevels（外部口型，预留给 TTS，会覆盖内置口型）
 * 可选：avatar.remote = new Tha3View(...)（remote.js）时改由 THA3 渲染，断开自动退回 WebGL。
 * 输出：一张 canvas。立绘被切成网格（rig.js），每帧由弹簧驱动的参数形变：
 *       头部转动/点头、呼吸、身体倾斜、跳跃压扁、头发摆动、呆毛和耳朵的惯性。
 *       脸部由 表情底图 + 口型贴片(0~3 级) + 闭眼贴片 在着色器里合成，贴片跟着网格一起动；
 *       切换表情时先闭眼，在闭眼的瞬间交叉淡化到新表情，看不出"换图"。
 *       情绪有惯性（DWELL / BRIDGE）：新情绪按路线逐步过渡，例如 哭 → 抽鼻子平复 → 害羞 → 笑。
 */
const MARGIN = { l: 0.16, r: 0.16, t: 0.14, b: 0.02 };   // canvas 比立绘大一圈，留出动作空间

// 每种表情的常驻姿态
const EMO_POSE = {
  neutral:   {},
  happy:     { ear: 0.12, pitch: -0.15 },
  sad:       { ear: -0.32, pitch: 0.45, roll: 0.03 },
  surprised: { ear: 0.28, pitch: -0.3 },
  thinking:  { roll: -0.07, pitch: -0.35, yaw: -0.25 },
  shy:       { roll: 0.08, pitch: 0.3, yaw: 0.2, ear: -0.12 },
  angry:     { ear: -0.2, pitch: 0.15, lean: 0.015 },
};
const STATE_POSE = { listening: { roll: 0.06, pitch: 0.12, lean: 0.012 } };
// 切换到某表情时自动做的反应动作（大脑同时给了动作标签时以标签为准）
const REACTION = { happy: "hop", surprised: "startle", sad: "droop", angry: "tremble", shy: "wiggle" };

/* 情绪惯性：情绪不会一收到新指令就翻脸，而是沿着路线一步步走过去。
 * DWELL：进入一个情绪后至少停留多久（秒）才开始离开；再次收到同一情绪会重新计时（"又哭了"）。
 * BRIDGE[从][到]：途经的过渡阶段 [表情, 停留秒数, 进入时的动作]；"*" 是默认路线，没写就直接过去。
 * POSE_FADE：离开某情绪时，耳朵、低头这些姿态慢慢恢复的时长（秒）。 */
const DWELL = { neutral: 0.8, happy: 1.5, sad: 4, surprised: 1.2, thinking: 1, shy: 2, angry: 3.5 };
const BRIDGE = {
  sad:   { "*": [["neutral", 2, "sniff"]], happy: [["neutral", 2, "sniff"], ["shy", 1.5]], angry: [], surprised: [] },
  angry: { "*": [["neutral", 1.8, "sigh"]], happy: [["neutral", 1.8, "sigh"], ["shy", 1.4]], surprised: [] },
  happy: { sad: [["neutral", 1.2]], angry: [["surprised", 0.7]] },
  shy:   { angry: [["neutral", 0.8]] },
};
const POSE_FADE = { sad: 2.2, angry: 1.6, shy: 1, default: 0.6 };
const POSE_KEYS = ["roll", "yaw", "pitch", "lean", "ear"];
/* 空闲收表情：停止说话/倾听后，强情绪再挂多久（秒）就自然回到平静。
 * 不在这里的表情（neutral）不衰减；有路线时仍走 BRIDGE 平滑过渡。 */
const EMO_LINGER = { happy: 6, sad: 12, surprised: 4, shy: 4, angry: 8 };

class Avatar {
  constructor(charEl, manifest) {
    this.el = charEl;
    this.body = charEl.querySelector("#char-body");
    this.spr = manifest.sprites;
    this.emotion = "neutral";         // 目标情绪：不会被说话、思考或空闲自动清掉
    this.mood = "neutral";            // 此刻所处的情绪阶段，沿 path 一步步走向 emotion
    this.moodSince = 0;
    this.moodUntil = 0;
    this.idleSince = performance.now();   // 进入空闲的时刻：空闲多久后才收掉强情绪
    this.path = [];                   // 还要经过的阶段 [{ emo, dur?, motion? }]，最后一个是目标
    this.state = "idle";
    this.talking = false;
    this.temporaryEmotion = null;     // hold>0 时的临时表情，结束后回到 mood
    this.temporaryUntil = 0;
    this.poseEmo = "neutral";         // 姿态从 poseFrom 渐变到 EMO_POSE[poseEmo]
    this.poseFrom = {};
    this.poseT0 = 0;
    this.poseDur = 1;
    this.curPose = {};
    this.onChange = () => {};

    // 脸
    this.layers = [{ emo: "neutral" }];  // [当前] 或 [淡出中, 淡入中]
    this.mix = 0;
    this.pending = null;
    this.blink = 0;                  // 闭眼贴片不透明度
    this.blinkUntil = 0;
    this.nextBlink = performance.now() + 2000;
    this.env = 0;                    // 口型包络 0~1
    this.envPeak = 0;
    this.attackUntil = 0;
    this.lastSyllable = 0;
    this.extLevel = 0;
    this.extUntil = 0;

    // 身体
    this.rig = new Rig(this.spr);
    const S = (k, d) => new Spring(k, d);
    this.sp = { roll: S(60, 11), yaw: S(50, 10), pitch: S(80, 12), lean: S(40, 9),
                ear: S(140, 10), hair: S(22, 3.2), ahoge: S(90, 4.5), yLag: S(120, 7) };
    this.motions = [];
    this.lastMotion = -1e9;
    this.reaction = null;
    this.glance = 0;
    this.nextGlance = performance.now() + 4000;
  }

  async load() {
    const canvas = document.createElement("canvas");
    Object.assign(canvas.style, {
      position: "absolute", left: -MARGIN.l * 100 + "%", top: -MARGIN.t * 100 + "%",
      width: (1 + MARGIN.l + MARGIN.r) * 100 + "%", height: (1 + MARGIN.t + MARGIN.b) * 100 + "%",
    });
    this.body.appendChild(canvas);
    this.canvas = canvas;
    this.gfx = new Renderer(canvas, this.rig);
    // 时间基准先定下来：贴图要等一会儿，这期间如果有别的东西驱动了 _frame，
    // t0 还没设会让 (now - t0) 变成 NaN。
    this.t0 = this.last = performance.now();
    const jobs = [];
    for (const [emo, e] of Object.entries(this.spr.emotions)) {
      jobs.push(this.gfx.texture(e.src));
      e.mouth.forEach((m) => jobs.push(this.gfx.texture(m.src)));
      if (e.blink) jobs.push(this.gfx.texture(e.blink.src));
    }
    await Promise.all(jobs);
    this.t0 = this.last = performance.now();
    const frame = (now) => { this._frame(now); requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
    this.onChange({ emotion: this.displayEmotion, state: this.state });
  }

  // ---------- 输入 ----------
  /** 默认永久改变情绪，并按 DWELL / BRIDGE 逐步过渡过去；instant 时立即切换（刷新恢复、强制切换用）。
   *  hold>0 时只临时表现 hold 秒（惊吓一类的瞬间反应，立即生效），之后回到原来的情绪。 */
  setEmotion(name, { hold = 0, react = true, instant = false } = {}) {
    if (!this.spr.emotions[name]) return;
    const prev = this.displayEmotion, now = performance.now();
    if (hold > 0) {
      this.temporaryEmotion = name;
      this.temporaryUntil = now + hold * 1000;
      return this._refresh(prev, react);
    }
    this.temporaryEmotion = null;
    this.temporaryUntil = 0;
    if (instant) {
      this.emotion = name;
      this.path = [];
      this._enterMood(name, now, 0, null);
    } else if (name === this.mood && (!this.path.length || name !== this.emotion)) {
      // 已经处在这个情绪里：取消正在进行的过渡，并重新计时，情绪被"强化"
      this.emotion = name;
      this.path = [];
      this.moodSince = now;
    } else if (name !== this.emotion) {
      this.emotion = name;
      this._plan(now, react);
    }
    this._refresh(prev, false);
  }

  /** 从当前阶段规划去 emotion 的路线；当前阶段至少要停满 DWELL。 */
  _plan(now, react) {
    const from = this.mood, to = this.emotion, routes = BRIDGE[from] || {};
    this.path = [];
    for (const [emo, dur, motion] of routes[to] || routes["*"] || []) {
      if (emo === from) continue;
      if (emo === to) break;
      this.path.push({ emo, dur, motion });
    }
    const last = (routes[to] || routes["*"] || []).find(([emo]) => emo === to);
    this.path.push({ emo: to, motion: (last && last[2]) || (react ? REACTION[to] : null) });
    this.moodUntil = this.moodSince + (DWELL[from] || 0) * 1000;
  }

  _enterMood(emo, now, dur, motion) {
    this.mood = emo;
    this.moodSince = now;
    this.moodUntil = now + (dur || 0) * 1000;
    if (motion && now - this.lastMotion > 400) this.reaction = { name: motion, at: now + 90 };
  }

  setState(state) {
    const prev = this.displayEmotion;
    if (state === "idle" && this.state !== "idle") this.idleSince = performance.now();
    this.state = state;
    this._refresh(prev, false);
  }

  setTalking(on) {
    this.talking = on;
  }

  /** 一个音节：嘴巴张开到随机幅度再合上。SpeechPlayer 打字机每出一个字调用一次。 */
  syllable() {
    const now = performance.now();
    this.envPeak = 0.45 + Math.random() * 0.55;
    this.attackUntil = now + 45;
    this.lastSyllable = now;
    if (Math.random() < 0.22) this.sp.pitch.kick(1.6 + Math.random());   // 说话时偶尔轻轻点一下头
    this.sp.ahoge.kick((Math.random() - 0.5) * 0.6);
  }

  /** 一句话说完：点一下头。 */
  beat() {
    if (Math.random() < 0.6) this.sp.pitch.kick(3 + Math.random() * 1.5);
    this.sp.roll.kick((Math.random() - 0.5) * 0.25);
  }

  motion(name) {
    if (!MOTIONS[name]) return;
    this.lastMotion = performance.now();
    this.reaction = null;
    this.motions.push({ name, t0: performance.now() });
  }

  /** 外部口型（0~1）。TTS 接入后用音频响度驱动，这里会覆盖内置的随机开合。 */
  setMouthLevel(level) {
    this.extLevel = level;
    this.extUntil = performance.now() + 300;
  }

  /** 按固定帧率播放一串口型（例如 TTS 服务返回的 viseme / 响度序列）。 */
  playMouthLevels(levels, fps = 60) {
    const t0 = performance.now();
    const step = () => {
      const i = Math.floor((performance.now() - t0) / (1000 / fps));
      if (i >= levels.length) return this.setMouthLevel(0);
      this.setMouthLevel(levels[i]);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------- 状态 ----------
  get displayEmotion() {
    if (this.temporaryEmotion) return this.temporaryEmotion;
    // 只有平静且不在过渡中时才用思考脸表示工作状态；难过/生气等情绪思考时仍然保留
    return this.state === "thinking" && this.mood === "neutral" && !this.path.length ? "thinking" : this.mood;
  }

  _refresh(prev, react) {
    const emo = this.displayEmotion;
    if (emo !== prev) {
      // 先闭眼，闭眼的瞬间换表情
      const now = performance.now();
      this.blinkUntil = Math.max(this.blinkUntil, now + 170);
      this.pending = { emo, at: now + 55 };
      if (react && REACTION[emo] && now - this.lastMotion > 400) this.reaction = { name: REACTION[emo], at: now + 90 };
    }
    this.onChange({ emotion: emo, target: this.path.length ? this.emotion : null, state: this.state });
  }

  get mouthLevel() {
    const now = performance.now();
    const e = now < this.extUntil ? this.extLevel : this.env;
    return e > 0.72 ? 3 : e > 0.42 ? 2 : e > 0.14 ? 1 : 0;
  }

  // ---------- 每帧 ----------
  _frame(now) {
    // dt 不能为负或 NaN：时钟被外部改写（比如录屏脚本换成虚拟时钟）时可能倒流，
    // 一旦 dt 变成 NaN，弹簧会被污染成 NaN，立绘就再也画不出来了。
    if (!Number.isFinite(now)) now = this.last;
    const raw = (now - this.last) / 1000;
    const dt = Number.isFinite(raw) ? Math.max(0, Math.min(raw, 1 / 20)) : 0;
    this.last = now;
    // t0 在 load() 里（等贴图加载完）才设好；万一第一帧来得更早，
    // 用 last 兜底，否则 t = (now - undefined) / 1000 = NaN，
    // 待机正弦全变 NaN，弹簧被污染，立绘就再也画不出来了。
    const t = (now - (this.t0 ?? this.last)) / 1000;
    this._face(now, dt);
    const p = this._pose(now, t, dt);
    const remote = this.remote && this.remote.ready;
    this.canvas.style.visibility = remote ? "hidden" : "";
    if (remote) {
      // THA3 模式：脸用当前（淡入中的）表情；切换发生在闭眼期间，所以直接切也看不出来
      const emo = this.layers[this.layers.length - 1].emo;
      this.remote.update({ ...p, emo, mouth: this.mouthLevel, blink: this.blink > 0.5 ? 1 : 0 },
                         this.el.clientHeight / this.rig.H);
    } else {
      this.gfx.draw(this.rig.deform(p), this._layerUniforms());
    }
  }

  _face(now, dt) {
    // 空闲时强情绪自然回落：害羞/惊讶这类反应情绪不能一直挂在脸上
    const linger = EMO_LINGER[this.mood];
    if (linger && this.state === "idle" && !this.path.length && !this.temporaryEmotion &&
        now - Math.max(this.idleSince, this.moodSince) > linger * 1000) {
      this.setEmotion("neutral");
    }
    // 临时表情到时只恢复之前的情绪，不清空持续情绪
    if (this.temporaryEmotion && now >= this.temporaryUntil) {
      const prev = this.displayEmotion;
      this.temporaryEmotion = null;
      this.temporaryUntil = 0;
      this._refresh(prev, false);
    }
    // 沿着过渡路线走到下一个阶段
    if (this.path.length && now >= this.moodUntil) {
      const prev = this.displayEmotion, s = this.path.shift();
      this._enterMood(s.emo, now, s.dur, s.motion);
      this._refresh(prev, false);
    }
    // 自主眨眼，偶尔连眨两次
    if (now > this.nextBlink) {
      this.blinkUntil = now + 110;
      this.nextBlink = now + (Math.random() < 0.18 ? 260 : 2400 + Math.random() * 3600);
    }
    this.blink += ((now < this.blinkUntil ? 1 : 0) - this.blink) * Math.min(1, dt * 45);
    if (this.pending && now >= this.pending.at) {
      const cur = this.layers[this.layers.length - 1];
      if (cur.emo !== this.pending.emo) { this.layers = [cur, { emo: this.pending.emo }]; this.mix = 0; }
      this.pending = null;
    }
    if (this.layers.length > 1) {
      this.mix += dt / 0.1;
      if (this.mix >= 1) { this.layers = [this.layers[1]]; this.mix = 0; }
    }
    // 口型包络：音节到来时快速张开，之后指数回落
    if (this.talking && now - this.lastSyllable > 400 && now > this.extUntil) this.syllable();  // 只有 setTalking 时的兜底
    if (now < this.attackUntil) this.env += (this.envPeak - this.env) * Math.min(1, dt * 40);
    else this.env *= Math.exp(-dt / 0.085);
    if (this.reaction && now >= this.reaction.at) {
      this.motions.push({ name: this.reaction.name, t0: now });
      this.reaction = null;
    }
  }

  _pose(now, t, dt) {
    const emo = this.displayEmotion;
    // 表情姿态渐变：离开难过、生气时耳朵和头慢慢抬起来，而不是跟着换脸一下子弹回去
    if (emo !== this.poseEmo) {
      this.poseFrom = { ...this.curPose };
      this.poseDur = (POSE_FADE[this.poseEmo] || POSE_FADE.default) * 1000;
      this.poseT0 = now;
      this.poseEmo = emo;
    }
    const fade = smooth(0, 1, (now - this.poseT0) / this.poseDur), goal = EMO_POSE[emo] || {};
    for (const key of POSE_KEYS) this.curPose[key] = (this.poseFrom[key] || 0) * (1 - fade) + (goal[key] || 0) * fade;
    const tgt = { roll: 0, yaw: 0, pitch: 0, lean: 0, ear: 0, dx: 0, dy: 0, sq: 0 };
    for (const pose of [this.curPose, STATE_POSE[this.state]]) for (const k in pose || {}) tgt[k] += pose[k];
    // 非平静情绪下思考只轻轻歪头，不换脸；已经是思考脸时不重复叠加
    if (this.state === "thinking" && emo !== "thinking") {
      tgt.roll -= 0.035;
      tgt.pitch -= 0.15;
      tgt.yaw -= 0.12;
    }
    // 待机的小动作：几个不同周期的正弦叠加，永远不会完全静止
    tgt.roll += 0.015 * Math.sin(t * 0.7) + 0.008 * Math.sin(t * 1.9 + 1);
    tgt.yaw += 0.07 * Math.sin(t * 0.43 + 1);
    tgt.pitch += 0.05 * Math.sin(t * 0.61 + 2);
    tgt.lean += 0.008 * Math.sin(t * 0.5);
    if (this.state === "idle" && now > this.nextGlance) {      // 偶尔东张西望
      this.glance = Math.random() < 0.4 ? 0 : (Math.random() - 0.5) * 0.5;
      this.nextGlance = now + 4000 + Math.random() * 6000;
    }
    if (this.state !== "idle") this.glance = 0;
    tgt.yaw += this.glance;
    if (this.state === "listening") tgt.pitch += 0.18 * Math.max(0, Math.sin(t * 2.4)) ** 8;   // 嗯嗯地轻轻点头
    if (this.state === "thinking") tgt.roll += 0.03 * Math.sin(t * 1.2);
    // 一次性动作
    this.motions = this.motions.filter((m) => {
      const u = (now - m.t0) / 1000 / MOTIONS[m.name].dur;
      if (u >= 1) return false;
      MOTIONS[m.name].f(u, tgt);
      return true;
    });
    // 弹簧积分（细分步长，保证稳定）。
    // n 至少为 1：dt 为 0 时 ceil(0)=0 会让 h = 0/0 = NaN，弹簧一旦 NaN 就再也画不出来了。
    const sp = this.sp, n = Math.max(1, Math.ceil(dt / (1 / 120))), h = dt / n;
    for (let i = 0; i < n; i++) {
      sp.roll.step(tgt.roll, h); sp.yaw.step(tgt.yaw, h); sp.pitch.step(tgt.pitch, h);
      sp.lean.step(tgt.lean, h); sp.ear.step(tgt.ear, h);
      const drive = sp.roll.x + sp.lean.x + tgt.dx / 200;
      sp.hair.step(drive * 110, h);       // 头发跟着头和身体走，但慢半拍、会来回荡
      sp.ahoge.step(drive, h);            // 呆毛同理，更灵敏
      sp.yLag.step(tgt.dy, h);
    }
    const drive = sp.roll.x + sp.lean.x + tgt.dx / 200;
    const vert = sp.yLag.x - tgt.dy;      // 跳起时为正：耳朵、呆毛被"甩"下去，落地时甩上来
    const wob = 0.05 * Math.sin(t * 2.1) + 0.03 * Math.sin(t * 3.7 + 2);
    return {
      roll: sp.roll.x, yaw: sp.yaw.x, pitch: sp.pitch.x, lean: sp.lean.x,
      breath: 0.5 - 0.5 * Math.cos((t / 4.2) * Math.PI * 2),
      dx: tgt.dx, dy: tgt.dy, sq: tgt.sq,
      hair: sp.hair.x,
      ahoge: (drive - sp.ahoge.x) * 2.2 + wob - vert * 0.004,
      earL: sp.ear.x - vert * 0.006 + 0.03 * Math.sin(t * 1.3),
      earR: sp.ear.x - vert * 0.006 + 0.03 * Math.sin(t * 1.3 + 0.8),
    };
  }

  _layerUniforms() {
    const level = this.mouthLevel;
    const layer = ({ emo }) => {
      const e = this.spr.emotions[emo];
      return { base: e.src, mouth: level ? e.mouth[level - 1] : null, eye: e.blink, eyeA: this.blink };
    };
    return { layers: this.layers.map(layer), mix: this.layers.length > 1 ? Math.min(this.mix, 1) : 0 };
  }
}

// ---------------- WebGL2 渲染 ----------------
const VS = `#version 300 es
in vec2 aPos;
in vec2 aRest;
uniform vec2 uSize;
uniform vec4 uView;
out vec2 vRest;
void main() {
  vRest = aRest;
  vec2 n = aPos / uSize;
  gl_Position = vec4(n.x * uView.x + uView.z, n.y * uView.y + uView.w, 0.0, 1.0);
}`;

const FS = `#version 300 es
precision highp float;
in vec2 vRest;
uniform vec2 uSize;
uniform sampler2D uBase0, uMouth0, uEye0, uBase1, uMouth1, uEye1;
uniform vec4 uMouthR0, uEyeR0, uMouthR1, uEyeR1;   // 贴片在立绘中的矩形（像素），w=0 表示没有
uniform float uEyeA0, uEyeA1, uMix;
out vec4 color;

vec4 over(vec4 top, vec4 bottom) { return top + bottom * (1.0 - top.a); }   // 预乘 alpha

vec4 decal(sampler2D tex, vec4 r, float a) {
  vec2 q = (vRest - r.xy) / max(r.zw, vec2(1.0));
  float inside = step(0.0, q.x) * step(q.x, 1.0) * step(0.0, q.y) * step(q.y, 1.0);
  return texture(tex, clamp(q, 0.0, 1.0)) * (a * inside * step(0.5, r.z));
}

vec4 layer(sampler2D base, sampler2D mouth, vec4 mr, sampler2D eye, vec4 er, float ea) {
  vec4 c = texture(base, vRest / uSize);
  c = over(decal(mouth, mr, 1.0), c);
  return over(decal(eye, er, ea), c);
}

void main() {
  vec4 a = layer(uBase0, uMouth0, uMouthR0, uEye0, uEyeR0, uEyeA0);
  vec4 b = layer(uBase1, uMouth1, uMouthR1, uEye1, uEyeR1, uEyeA1);
  color = mix(a, b, uMix);
}`;

class Renderer {
  constructor(canvas, rig) {
    this.canvas = canvas;
    this.rig = rig;
    this.tex = {};
    // preserveDrawingBuffer：让画布内容在"没有下一帧"时也留着。
    // 正常浏览器里每帧都重画，不开也没事；但截图/录屏（tools/make_promo.py）
    // 是一帧一帧手动驱动的，不开的话截图会拿到空画布。
    const gl = (this.gl = canvas.getContext("webgl2", {
      premultipliedAlpha: true, antialias: true, preserveDrawingBuffer: true,
    }));
    if (!gl) throw new Error("浏览器不支持 WebGL2");
    const prog = (this.prog = gl.createProgram());
    for (const [type, src] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]]) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      gl.attachShader(prog, s);
    }
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    this.u = {};
    for (const name of ["uSize", "uView", "uBase0", "uMouth0", "uEye0", "uBase1", "uMouth1", "uEye1",
                        "uMouthR0", "uEyeR0", "uMouthR1", "uEyeR1", "uEyeA0", "uEyeA1", "uMix"])
      this.u[name] = gl.getUniformLocation(prog, name);
    // 纹理单元：base0=0 mouth0=1 eye0=2 base1=3 mouth1=4 eye1=5
    this.texUnit = { uBase0: 0, uMouth0: 1, uEye0: 2, uBase1: 3, uMouth1: 4, uEye1: 5 };
    for (const [n, unit] of Object.entries(this.texUnit)) gl.uniform1i(this.u[n], unit);
    gl.uniform2f(this.u.uSize, rig.W, rig.H);
    const W = 1 + MARGIN.l + MARGIN.r, H = 1 + MARGIN.t + MARGIN.b;
    gl.uniform4f(this.u.uView, 2 / W, -2 / H, (2 * MARGIN.l) / W - 1, 1 - (2 * MARGIN.t) / H);

    const vao = this.vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const attr = (name, data, usage) => {
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, data, usage);
      const loc = gl.getAttribLocation(prog, name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      return buf;
    };
    attr("aRest", rig.rest, gl.STATIC_DRAW);
    this.posBuf = attr("aPos", rig.rest, gl.DYNAMIC_DRAW);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, rig.index, gl.STATIC_DRAW);
    this.empty = this._upload(new ImageData(1, 1));
  }

  async texture(src) {
    if (this.tex[src]) return;
    const img = new Image();
    img.src = src;
    await img.decode();
    this.tex[src] = this._upload(img);
  }

  _upload(img) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  draw(pos, { layers, mix }) {
    const gl = this.gl, c = this.canvas, dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) {
      // 改 canvas 尺寸会重置整个 WebGL 上下文状态（program / VAO / 纹理绑定全丢），
      // 必须重新绑定一次，否则这一帧什么都画不出来。
      c.width = w; c.height = h;
      this._bindState();
    }
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.posBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pos);
    [layers[0], layers[1] || layers[0]].forEach((L, i) => {
      const bind = (unit, src) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, (src && this.tex[src]) || this.empty); };
      const rect = (name, p) => gl.uniform4f(this.u[name], p ? p.x : 0, p ? p.y : 0, p ? p.w : 0, p ? p.h : 0);
      bind(i * 3, L.base);
      bind(i * 3 + 1, L.mouth && L.mouth.src);
      bind(i * 3 + 2, L.eye && L.eye.src);
      rect("uMouthR" + i, L.mouth);
      rect("uEyeR" + i, L.eye);
      gl.uniform1f(this.u["uEyeA" + i], L.eye ? L.eyeA : 0);
    });
    gl.uniform1f(this.u.uMix, mix);
    gl.drawElements(gl.TRIANGLES, this.rig.index.length, gl.UNSIGNED_SHORT, 0);
  }

  /** 重新绑定 program / VAO / uniform —— canvas 尺寸变化后必须调用 */
  _bindState() {
    const gl = this.gl;
    gl.useProgram(this.prog);
    gl.bindVertexArray(this.vao);
    for (const [name, unit] of Object.entries(this.texUnit || {})) {
      gl.uniform1i(this.u[name], unit);
    }
    gl.uniform2f(this.u.uSize, this.rig.W, this.rig.H);
    const W = 1 + MARGIN.l + MARGIN.r, H = 1 + MARGIN.t + MARGIN.b;
    gl.uniform4f(this.u.uView, 2 / W, -2 / H, (2 * MARGIN.l) / W - 1, 1 - (2 * MARGIN.t) / H);
  }
}
