/* 说话播放器：把服务端推来的 text / emotion / motion 流，按"电影字幕"的节奏逐句播出。
 *
 * 一句话 = 到句末标点为止的文本。每句：清屏 → 打字机显示 → 口型开合 → 停顿 → 下一句。
 * 打字机每出一个字给 avatar 一个音节脉冲（avatar.syllable），驱动 4 级口型；
 * 句中的动作标签在打字机走到那个位置时触发。
 *
 * 预留 TTS：传入 voice = { speak(text): Promise, cancel() }，每句话会交给 voice 朗读，
 * 字幕与语音同时开始、等两者都结束再进入下一句。voice 可以在 speak() 里
 * 调 avatar.setMouthLevel() 用真实音频响度驱动口型（会自动覆盖内置的随机开合）。
 */
const SENTENCE_END = "。！？!?…\n";
const SOFT_PAUSE = "，、；;：:,";

class SpeechPlayer {
  constructor({ avatar, subtitle, voice = null, cps = 9, maxLine = 26, onDone = () => {}, onFinish = () => {} }) {
    this.avatar = avatar;
    this.sub = subtitle;
    this.voice = voice;
    this.cps = cps;              // 每秒显示的字数
    this.maxLine = maxLine;      // 字幕单行最多字数，超出换屏
    this.onDone = onDone;
    this.onFinish = onFinish;    // 一条回复整个播完了（外面据此回 ack）
    this.channel = "main";       // 当前这条是谁在说：main / danmaku / game
    this.gen = 0;
    this.queue = [];
    this.open = new Set();       // 已 begin 但还没 end 的回复 id
    this.accepted = new Set();   // 当前这一轮接受的回复 id（排队的 say 会加进来）
    this.expected = new Map();   // id → 一共推来多少个片段
    this.consumed = new Map();   // id → 已经播了多少个片段
    this.closed = new Set();     // 收到 end 的 id
    this.finished = new Set();   // 已经确认播完的 id
    this.running = false;
    this.mute = false;           // true：口型交给真实音频（语音接管时）
    this.wake = null;
  }

  get ended() { return this.open.size === 0; }

  /** queue=true：排在当前内容后面播放；否则打断当前内容重新开始。 */
  begin(id, { queue = false, ch = "main" } = {}) {
    if (!queue || !this.running) this._reset();
    this.channel = ch || "main";
    this.accepted.add(id);
    this.open.add(id);
    if (!this.running) this._run(this.gen);
  }

  push(ev) {
    if (!this.accepted.has(ev.id)) return;  // 已被打断的旧回复的残余事件
    if (ev.op === "text") {
      const chars = [...ev.text];
      for (const ch of chars) this.queue.push({ ch, id: ev.id });
      this._expect(ev.id, chars.length);
    } else if (ev.op === "emotion") {
      this.queue.push({ emotion: ev.name, id: ev.id });
      this._expect(ev.id, 1);
    } else if (ev.op === "motion") {
      this.queue.push({ motion: ev.name, id: ev.id });
      this._expect(ev.id, 1);
    } else if (ev.op === "end") {
      this.open.delete(ev.id);
      this.closed.add(ev.id);
    }
    if (this.wake) { this.wake(); this.wake = null; }
  }

  interrupt() {
    this._reset();
    this.sub.textContent = "";
    this.avatar.setTalking(false, this.channel);
  }

  _reset() {
    this.gen++;
    this.queue = [];
    this.open.clear();
    this.accepted.clear();
    this.expected.clear();
    this.consumed.clear();
    this.closed.clear();
    this.finished.clear();
    this.running = false;
    if (this.voice) this.voice.cancel();
    if (this.wake) { this.wake(); this.wake = null; }
  }

  _expect(id, n) { this.expected.set(id, (this.expected.get(id) || 0) + n); }

  /** 片段被取走了 —— 数一数这条回复还剩几个片段没播。 */
  _consume(item) {
    if (!item || item.id == null) return;
    this.consumed.set(item.id, (this.consumed.get(item.id) || 0) + 1);
  }

  /** 一条回复的片段全播完了 → 通知外面（服务端据此放下一条）。 */
  _checkFinished() {
    for (const id of this.closed) {
      if (this.finished.has(id)) continue;
      if ((this.consumed.get(id) || 0) < (this.expected.get(id) || 0)) continue;
      this.finished.add(id);
      this.onFinish(id, this.channel);
    }
  }

  /** 等数据用的等待：push() / 打断时会被立即唤醒。 */
  _wait(ms) {
    return new Promise((res) => {
      const t = setTimeout(() => { this.wake = null; res(); }, ms);
      this.wake = () => { clearTimeout(t); res(); };
    });
  }

  /** 节奏用的等待：不会被新数据提前唤醒（否则打字机会随 token 到达忽快忽慢）。 */
  _sleep(ms) {
    return new Promise((res) => setTimeout(res, ms));
  }

  /** 取下一段：一个表情 / 动作事件，或一句话（句中的动作记在 marks 里）。
   *  流还没结束又没到句末时，最多等 600ms 再先说已有部分。 */
  async _nextSegment(gen) {
    let text = "";
    let waited = 0;
    const marks = [];
    const seg = () => ({ text, marks });
    while (gen === this.gen) {
      const item = this.queue[0];
      if (item) {
        if (item.emotion) {
          if (text) return seg();
          this.queue.shift();
          this._consume(item);
          return { emotion: item.emotion };
        }
        if (item.motion) {
          this.queue.shift();
          this._consume(item);
          if (!text) return { motion: item.motion };
          marks.push({ at: text.length, motion: item.motion });
          continue;
        }
        this.queue.shift();
        this._consume(item);
        text += item.ch;
        if (SENTENCE_END.includes(item.ch)) {
          // 把紧跟的标点（"！！""？！"）也收进这一句
          while (this.queue[0] && this.queue[0].ch && SENTENCE_END.includes(this.queue[0].ch)) {
            const extra = this.queue.shift();
            this._consume(extra);
            text += extra.ch;
          }
          return seg();
        }
        continue;
      }
      if (this.ended) return text ? seg() : null;
      if (text && waited >= 600) return seg();
      const t0 = performance.now();
      await this._wait(text ? 600 - waited : 60000);
      waited += performance.now() - t0;
    }
    return null;
  }

  async _run(gen) {
    this.running = true;
    let spoke = false;
    while (gen === this.gen) {
      const seg = await this._nextSegment(gen);
      if (gen !== this.gen) return;
      if (!seg) break;
      if (seg.emotion) { this.avatar.setEmotion(seg.emotion); continue; }
      if (seg.motion) { this.avatar.motion(seg.motion); continue; }
      const text = seg.text.replace(/\s+/g, " ").trim();
      if (!text) { seg.marks.forEach((m) => this.avatar.motion(m.motion)); continue; }
      if (/^[\p{P}\s]+$/u.test(text)) {   // 被流式切开的尾部标点（"…"、"！"）接回上一句，不单独成屏
        if (this.sub.textContent && !this.sub.querySelector(".dots")) this.sub.textContent += text;
        continue;
      }
      if (!spoke) { spoke = true; this.avatar.setState("speaking", this.channel); }
      await this._speak(text, gen, seg.marks);
    }
    if (gen !== this.gen) return;
    this.avatar.setTalking(false, this.channel);
    if (spoke) await this._wait(2600);        // 最后一句字幕多停留一会儿；期间有新内容会立即接上
    if (gen !== this.gen) return;
    if (this.queue.length || !this.ended) return this._run(gen);   // 停留期间又排进了新内容
    this.running = false;
    this.sub.textContent = "";
    this.avatar.setState("idle", this.channel);
    // 整条都播完了、字幕也留够了，这时才 ack —— 服务端据此放下一条
    this._checkFinished();
    this.onDone();
  }

  /** 口型开合：语音流水线/全双工接管时（mute）由真实音频驱动，这里不再让打字机张嘴。 */
  _talk(on) {
    if (this.mute && on) return;
    this.avatar.setTalking(on, this.channel);
  }

  async _speak(text, gen, marks = []) {
    const voiceDone = this.voice
      ? this.voice.speak(text, this.channel).catch(() => {})
      : Promise.resolve();
    this.sub.textContent = "";
    this._talk(true);
    let line = "";
    let i = 0;
    for (const ch of text) {
      if (gen !== this.gen) return;
      while (marks.length && marks[0].at <= i) this.avatar.motion(marks.shift().motion);
      i += ch.length;
      if (line.length >= this.maxLine && !SENTENCE_END.includes(ch) && !SOFT_PAUSE.includes(ch)) line = "";
      line += ch;
      this.sub.textContent = line;
      let delay = 1000 / this.cps;
      if (SOFT_PAUSE.includes(ch)) {
        delay = 220;
        this._talk(false);
        await this._sleep(delay);
        if (line.length >= this.maxLine * 0.6) line = "";   // 逗号处换屏，读起来更像字幕
        this._talk(true);
        continue;
      }
      if (SENTENCE_END.includes(ch)) delay = 0;
      else if (!this.mute && !/[\s\p{P}]/u.test(ch)) this.avatar.syllable();
      await this._sleep(delay);
    }
    marks.forEach((m) => this.avatar.motion(m.motion));
    this._talk(false);
    this.avatar.beat();
    await voiceDone;
    if (gen !== this.gen) return;
    await this._sleep(Math.min(400 + text.length * 40, 1400));   // 句间停顿，句子越长停越久
  }
}

/** 浏览器自带 TTS（?tts=browser 开启），用于验证 voice 接口；正式 TTS 按同样接口替换即可。 */
class BrowserVoice {
  constructor(avatar) {
    this.avatar = avatar;
    this.voice = null;
    const pick = () => {
      const vs = speechSynthesis.getVoices();
      this.voice = vs.find((v) => /zh[-_]CN/i.test(v.lang) && /xiaoxiao|xiaoyi|female|女/i.test(v.name))
        || vs.find((v) => /zh/i.test(v.lang)) || null;
    };
    pick();
    speechSynthesis.onvoiceschanged = pick;
  }
  speak(text) {
    return new Promise((res) => {
      this.pending = res;
      const u = new SpeechSynthesisUtterance(text);
      if (this.voice) u.voice = this.voice;
      u.lang = "zh-CN";
      u.pitch = 1.5;
      u.rate = 1.05;
      u.onend = u.onerror = () => {
        if (this.pending === res) this.pending = null;
        res();
      };
      speechSynthesis.speak(u);
    });
  }
  cancel() {
    speechSynthesis.cancel();
    if (this.pending) {
      const done = this.pending;
      this.pending = null;
      done();          // 被打断时也要让 speak() 的 Promise 落地
    }
  }
}

/** 本机 SAPI TTS：音频从服务端取回，AnalyserNode 直接驱动当前通道口型。 */
class ServerVoice {
  constructor(avatar, conf = {}) {
    this.avatar = avatar;
    this.conf = conf;
    this.ctx = null;
    this.source = null;
    this.mouthTimer = null;
    this.channel = "main";
    this.playGen = 0;
    this.fallback = "speechSynthesis" in window ? new BrowserVoice(avatar) : null;
    const unlock = () => this.ensureContext().catch(() => {});
    document.addEventListener("pointerdown", unlock, { once: true });
  }

  async ensureContext() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state !== "running") await this.ctx.resume();
    return this.ctx;
  }

  async speak(text, ch = "main") {
    const gen = ++this.playGen;       // 一开始就占据这一代，打断即失效
    const ac = new AbortController();
    this.abort = ac;
    this.channel = ch;
    try {
      const ctx = await this.ensureContext();
      if (ctx.state !== "running") throw new Error("audio context is locked");
      if (gen !== this.playGen) return;
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          voice: this.conf.voice,
          rate: this.conf.rate || 0,
          volume: this.conf.volume ?? 100,
        }),
        signal: ac.signal,
      });
      if (!res.ok) throw new Error(`TTS HTTP ${res.status}`);
      const audio = await ctx.decodeAudioData(await res.arrayBuffer());
      if (gen !== this.playGen) return;       // fetch/decode 期间被打断
      await new Promise((resolve) => {
        if (gen !== this.playGen) { resolve(); return; }
        this._stopSourceOnly();
        const src = ctx.createBufferSource();
        const analyser = ctx.createAnalyser();
        const gain = ctx.createGain();
        analyser.fftSize = 256;
        gain.gain.value = Math.max(0, Math.min(1, (this.conf.volume ?? 100) / 100));
        src.buffer = audio;
        src.connect(analyser).connect(gain).connect(ctx.destination);
        this.source = src;
        this._mouth(analyser, ch, gen);
        src.onended = () => {
          if (this.source === src) this.source = null;
          resolve();                            // 被打断也要落地，别让 _speak 卡住
        };
        src.start();
      });
    } catch (e) {
      if (gen !== this.playGen) return;         // AbortError：打断导致的，静默
      console.warn("[tts]", e.message, "falling back to browser voice");
      if (!this.fallback) throw e;
      await this.fallback.speak(text);
    } finally {
      if (this.abort === ac) this.abort = null;
    }
  }

  _mouth(analyser, ch, playGen) {
    this._stopMouth(ch);
    const data = new Uint8Array(analyser.fftSize);
    const tick = () => {
      if (playGen !== this.playGen) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) {
        const x = (v - 128) / 128;
        sum += x * x;
      }
      const rms = Math.sqrt(sum / data.length);
      this.avatar.setMouthLevel(Math.min(1, rms * 4.5), ch);
    };
    tick();
    this.mouthTimer = setInterval(tick, 50);
  }

  _stopMouth(ch = this.channel) {
    if (this.mouthTimer) clearInterval(this.mouthTimer);
    this.mouthTimer = null;
    this.avatar.setMouthLevel(0, ch);
  }

  _stopSourceOnly() {
    if (this.source) {
      try { this.source.stop(); } catch (_) {}
      this.source = null;
    }
    this._stopMouth();
  }

  cancel() {
    this.playGen++;
    if (this.abort) {
      try { this.abort.abort(); } catch (_) {}
      this.abort = null;
    }
    this._stopSourceOnly();
    if (this.fallback) this.fallback.cancel();
  }
}
