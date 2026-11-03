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
  constructor({ avatar, subtitle, voice = null, cps = 9, maxLine = 26, onDone = () => {} }) {
    this.avatar = avatar;
    this.sub = subtitle;
    this.voice = voice;
    this.cps = cps;              // 每秒显示的字数
    this.maxLine = maxLine;      // 字幕单行最多字数，超出换屏
    this.onDone = onDone;
    this.gen = 0;
    this.queue = [];
    this.open = new Set();       // 已 begin 但还没 end 的回复 id
    this.accepted = new Set();   // 当前这一轮接受的回复 id（排队的 say 会加进来）
    this.running = false;
    this.wake = null;
  }

  get ended() { return this.open.size === 0; }

  /** queue=true：排在当前内容后面播放；否则打断当前内容重新开始。 */
  begin(id, { queue = false } = {}) {
    if (!queue || !this.running) this._reset();
    this.accepted.add(id);
    this.open.add(id);
    if (!this.running) this._run(this.gen);
  }

  push(ev) {
    if (!this.accepted.has(ev.id)) return;  // 已被打断的旧回复的残余事件
    if (ev.op === "text") for (const ch of ev.text) this.queue.push({ ch });
    else if (ev.op === "emotion") this.queue.push({ emotion: ev.name });
    else if (ev.op === "motion") this.queue.push({ motion: ev.name });
    else if (ev.op === "end") this.open.delete(ev.id);
    if (this.wake) { this.wake(); this.wake = null; }
  }

  interrupt() {
    this._reset();
    this.sub.textContent = "";
    this.avatar.setTalking(false);
  }

  _reset() {
    this.gen++;
    this.queue = [];
    this.open.clear();
    this.accepted.clear();
    this.running = false;
    if (this.voice) this.voice.cancel();
    if (this.wake) { this.wake(); this.wake = null; }
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
          return { emotion: item.emotion };
        }
        if (item.motion) {
          this.queue.shift();
          if (!text) return { motion: item.motion };
          marks.push({ at: text.length, motion: item.motion });
          continue;
        }
        this.queue.shift();
        text += item.ch;
        if (SENTENCE_END.includes(item.ch)) {
          // 把紧跟的标点（"！！""？！"）也收进这一句
          while (this.queue[0] && this.queue[0].ch && SENTENCE_END.includes(this.queue[0].ch)) text += this.queue.shift().ch;
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
      if (!spoke) { spoke = true; this.avatar.setState("speaking"); }
      await this._speak(text, gen, seg.marks);
    }
    if (gen !== this.gen) return;
    this.avatar.setTalking(false);
    if (spoke) await this._wait(2600);        // 最后一句字幕多停留一会儿；期间有新内容会立即接上
    if (gen !== this.gen) return;
    if (this.queue.length || !this.ended) return this._run(gen);   // 停留期间又排进了新内容
    this.running = false;
    this.sub.textContent = "";
    this.avatar.setState("idle");
    this.onDone();
  }

  async _speak(text, gen, marks = []) {
    const voiceDone = this.voice ? this.voice.speak(text).catch(() => {}) : Promise.resolve();
    this.sub.textContent = "";
    this.avatar.setTalking(true);
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
        this.avatar.setTalking(false);
        await this._sleep(delay);
        if (line.length >= this.maxLine * 0.6) line = "";   // 逗号处换屏，读起来更像字幕
        this.avatar.setTalking(true);
        continue;
      }
      if (SENTENCE_END.includes(ch)) delay = 0;
      else if (!/[\s\p{P}]/u.test(ch)) this.avatar.syllable();
      await this._sleep(delay);
    }
    marks.forEach((m) => this.avatar.motion(m.motion));
    this.avatar.setTalking(false);
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
      const u = new SpeechSynthesisUtterance(text);
      if (this.voice) u.voice = this.voice;
      u.lang = "zh-CN";
      u.pitch = 1.5;
      u.rate = 1.05;
      u.onend = u.onerror = () => res();
      speechSynthesis.speak(u);
    });
  }
  cancel() { speechSynthesis.cancel(); }
}
