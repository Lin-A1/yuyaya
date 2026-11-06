/* 语音客户端：浏览器麦克风 PCM16 16k <-> /ws/voice <-> 音频回传 PCM16 24k。
 *
 * 一个类，两种引擎（协议相同，由服务端按模式选）：
 *   pipeline  伪全双工：服务端 VAD 切句 → ASR → 大脑 → TTS（半双工模式）
 *   realtime  真全双工：转给 Qwen-Audio-Realtime（全双工模式）
 *
 * 麦克风一直开着，**不再因为她在说话就停止上传**（那样就没法插话了）。
 * 回声靠三道关：浏览器自带回声消除 → 这里的"闸门" → 服务端把识别结果和她刚说的话比对。
 *
 * 闸门：她正在放音时，麦克风响度必须超过 max(echo_gate, 放音响度 × echo_ratio) 才上传，
 * 否则上传同样长度的静音（不是不传：服务端靠连续的音频时间轴判断停顿）。
 * 回声经过消除后残余很小，而你真的开口插话，响度会明显高于它。
 */
function _downsampleTo16k(input, inRate) {
  if (inRate === 16000) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const v = Math.max(-1, Math.min(1, input[i]));
      out[i] = v * 0x7fff;
    }
    return out;
  }
  const ratio = inRate / 16000;
  const len = Math.floor(input.length / ratio);
  const out = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
    out[i] = v * 0x7fff;
  }
  return out;
}

function _rms(buf) {
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / Math.max(1, buf.length));
}

class DuplexVoice {
  constructor(avatar, cfg = {}) {
    this.avatar = avatar;
    this.cfg = cfg;
    this.kind = cfg.kind || "realtime";
    this.gateMin = cfg.echo_gate ?? 0.03;
    this.gateRatio = cfg.echo_ratio ?? 0.6;
    this.ws = null;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.analyser = null;
    this.nextAt = 0;
    this.sources = new Set();
    this.capTimers = new Set();     // 还没到播放时间的字幕
    this.mouthTimer = null;
    this.endTimer = null;
    this.captionTimer = null;
    this.caption = "";
    this.active = false;
    this.onClose = null;            // 服务端主动断开时通知界面复位
    this.onState = () => {};        // listening / speech / speaking
    this._ready = null;
    this._fail = null;
    this._playBuf = null;
  }

  get supported() {
    return "WebSocket" in window && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  async start() {
    if (this.active) return;
    if (!this.supported) throw new Error("当前浏览器不支持语音（需要 WebSocket 和麦克风）");

    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    await this.ctx.resume();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.connect(this.ctx.destination);
    this._playBuf = new Float32Array(this.analyser.fftSize);

    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/voice`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (e) => this._onMessage(e);
    this.ws.onclose = () => {
      if (this.active) { this.stop(); if (this.onClose) this.onClose(); }
    };

    const ready = new Promise((resolve, reject) => {
      this._ready = resolve;
      this._fail = reject;
      setTimeout(() => reject(new Error("语音连接超时")), 12000);
    });
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.addEventListener("error", () => reject(new Error("语音连接失败")), { once: true });
    });
    await ready;                 // 等服务端 ready（缺 key / 模式不对时这里会收到 error）

    await this._openMic();
    this.active = true;
    this.avatar.setState("listening");
    this.onState("listening");
    this._mouthLoop();
  }

  async _openMic() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const src = this.ctx.createMediaStreamSource(this.stream);
    const node = this.ctx.createScriptProcessor(2048, 1, 1);
    const mute = this.ctx.createGain();
    mute.gain.value = 0;                       // 只用来驱动处理，不外放麦克风
    src.connect(node);
    node.connect(mute);
    mute.connect(this.ctx.destination);
    node.onaudioprocess = (e) => {
      if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const input = e.inputBuffer.getChannelData(0);
      const pcm = _downsampleTo16k(input, this.ctx.sampleRate);
      if (!pcm.length) return;
      if (this.playing() && _rms(input) < Math.max(this.gateMin, this.playLevel() * this.gateRatio)) {
        pcm.fill(0);                           // 多半是自己的回声：发静音，保住时间轴
      }
      this.ws.send(pcm.buffer);
    };
    this.node = node;
  }

  /** 她正在放音（含 250ms 的扬声器尾音）。 */
  playing() {
    if (!this.ctx) return false;
    return this.sources.size > 0 || this.ctx.currentTime < this.nextAt + 0.25;
  }

  /** 此刻正在放的声音的响度（0..1）。 */
  playLevel() {
    if (!this.analyser) return 0;
    this.analyser.getFloatTimeDomainData(this._playBuf);
    return _rms(this._playBuf);
  }

  _onMessage(e) {
    if (typeof e.data !== "string") { this._play(e.data); return; }
    let m = null;
    try { m = JSON.parse(e.data); } catch (_) { return; }
    switch (m.type) {
      case "ready":
        if (m.mode) this.kind = m.mode === "pipeline" ? "pipeline" : "realtime";
        if (this._ready) { this._ready(); this._ready = null; this._fail = null; }
        break;
      case "speech_started":                     // 你开口了：停掉她正在放的声音
        this.flush();
        this.avatar.setState("listening");
        this.onState("speech");
        break;
      case "vad":
        if (m.state === "speech") { this.onState("speech"); if (!this.playing()) this.avatar.setState("listening"); }
        else this.onState(this.playing() ? "speaking" : "listening");
        break;
      case "flush":
        this.flush();
        this.clearCaption();
        break;
      case "caption": this._onCaption(m.text || ""); break;
      case "caption_clear":
        // 伪全双工里上一句的字幕可能还没到点显示，别提前抹掉
        if (this.kind !== "pipeline" || !this.playing()) { this.caption = ""; this._paintCaption(); }
        break;
      case "caption_end": this._onCaptionEnd(); break;
      case "error":
        console.warn("[voice]", m.error);
        if (this._fail) { this._fail(new Error(String(m.error))); this._fail = null; }
        break;
      case "transcript":
        console.log("[voice user]", m.text);
        break;
      case "closed":
        console.warn("[voice] 会话被服务端关闭：", m.reason || "");
        this.stop();
        if (this.onClose) this.onClose();
        break;
    }
  }

  /** 字幕。伪全双工：服务端先发字幕再发该句音频，所以按"这句音频开始播放的时刻"显示。 */
  _onCaption(text) {
    clearTimeout(this.captionTimer);
    if (this.kind !== "pipeline" || !this.ctx) {      // 真全双工：服务端发的是累计文本，直接覆盖
      this.caption = text;
      this._paintCaption();
      return;
    }
    const delay = Math.max(0, (Math.max(this.ctx.currentTime, this.nextAt) - this.ctx.currentTime) * 1000);
    const id = setTimeout(() => {
      this.capTimers.delete(id);
      this.caption = text;
      this._paintCaption();
    }, delay);
    this.capTimers.add(id);
  }

  _onCaptionEnd() {
    // 等排进 AudioContext 的音频放完再收字幕
    const left = this.ctx ? Math.max(0, this.nextAt - this.ctx.currentTime) : 0;
    clearTimeout(this.captionTimer);
    this.captionTimer = setTimeout(() => {
      this.caption = "";
      this._paintCaption();
    }, left * 1000 + 900);
  }

  _play(buf) {
    if (!this.ctx) return;
    const i16 = new Int16Array(buf);
    if (!i16.length) return;
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    const audio = this.ctx.createBuffer(1, f32.length, this.cfg.output_sample_rate || 24000);
    audio.copyToChannel(f32, 0);
    const src = this.ctx.createBufferSource();
    src.buffer = audio;
    src.connect(this.analyser);
    const at = Math.max(this.ctx.currentTime + 0.02, this.nextAt);
    this.nextAt = at + audio.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
    src.start(at);
    this.avatar.setTalking(true, "main");
    this.avatar.setState("speaking");
    this.onState("speaking");
    this._armEnd();
  }

  /* 音频队列播完时收口：停下来、回 idle。
   * 没有这一步 talking 会一直为 true，口型会一直开合，state 也停在 speaking。 */
  _armEnd() {
    clearTimeout(this.endTimer);
    const left = this.ctx ? Math.max(0, this.nextAt - this.ctx.currentTime) : 0;
    this.endTimer = setTimeout(() => {
      if (this.sources.size) { this._armEnd(); return; }
      this.avatar.setTalking(false, "main");
      this.avatar.setMouthLevel(0, "main");
      this.avatar.setState("idle");
      this.onState("listening");
    }, left * 1000 + 220);
  }

  _paintCaption() {
    const el = document.querySelector("#subtitle");
    if (el) el.textContent = this.caption;
  }

  clearCaption() {
    clearTimeout(this.captionTimer);
    this.capTimers.forEach((t) => clearTimeout(t));
    this.capTimers.clear();
    this.caption = "";
    this._paintCaption();
  }

  /** 打字交给实时模型回答（同一个声音）。伪全双工不走这里：文字走 /api/chat，由服务端说话器念。 */
  sendText(text) {
    if (this.kind !== "realtime") return false;
    if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.flush();
    this.ws.send(JSON.stringify({ type: "text", text }));
    return true;
  }

  /** 说一段已经生成好的回答：内容先由内嵌 agent 完成，实时模型只负责用同一个声音说出来。 */
  say(text) {
    if (this.kind !== "realtime") return false;
    if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.flush();
    this.ws.send(JSON.stringify({ type: "say", text }));
    return true;
  }

  /** 本地立刻停掉正在放的音频和还没显示的字幕。 */
  flush() {
    clearTimeout(this.endTimer);
    this.capTimers.forEach((t) => clearTimeout(t));
    this.capTimers.clear();
    for (const s of this.sources) {
      try { s.stop(); } catch (_) {}
    }
    this.sources.clear();
    this.nextAt = 0;
    this.avatar.setTalking(false, "main");
    this.avatar.setMouthLevel(0, "main");
  }

  cancel() {
    this.flush();
    this.clearCaption();
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify({ type: "cancel" })); } catch (_) {}
    }
  }

  _mouthLoop() {
    if (!this.analyser) return;
    const tick = () => {
      if (!this.active) return;
      if (this.sources.size) {
        this.avatar.setMouthLevel(Math.min(1, this.playLevel() * 4.5), "main");
      }
      this.mouthTimer = setTimeout(tick, 50);
    };
    tick();
  }

  stop() {
    this.active = false;
    clearTimeout(this.mouthTimer);
    clearTimeout(this.captionTimer);
    clearTimeout(this.endTimer);
    this.flush();
    this.clearCaption();
    if (this.ws) {
      this.ws.onclose = null;
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }
    if (this.node) { try { this.node.disconnect(); } catch (_) {} this.node = null; }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.ctx) { try { this.ctx.close(); } catch (_) {} this.ctx = null; }
    this.avatar.setState("idle");
    this.onState("off");
  }
}

/** 麦克风按钮背后的控制器：每次点击按**当前模式**挑引擎，设置里切了模式不用刷新页面。
 *  对外保持和以前 RealtimeVoice 一样的接口（active / flush / clearCaption / sendText / cancel），
 *  事件处理和控制台里原来的写法不用改。 */
class VoiceController {
  constructor({ avatar, player, button }) {
    this.avatar = avatar;
    this.player = player;
    this.btn = button;
    this.client = null;
    this.paint("off");
    this.btn.addEventListener("click", () => this.toggle());
    addEventListener("beforeunload", () => this.stop());
  }

  get active() { return !!(this.client && this.client.active); }
  flush() { if (this.active) this.client.flush(); }
  clearCaption() { if (this.active) this.client.clearCaption(); }
  sendText(text) { return this.active ? this.client.sendText(text) : false; }
  say(text) { return this.active ? this.client.say(text) : false; }
  cancel() { if (this.active) this.client.cancel(); }

  paint(state, detail = "") {
    const labels = { off: "语音", connecting: "连接中", listening: "语音中", speech: "在听",
                     speaking: "说话中", denied: "不可用", on: "语音中" };
    const titles = {
      off: "点一下开启语音：麦克风常开，随时可以插话",
      connecting: "正在连接语音…",
      listening: "语音中：再点一下关闭", speech: "正在听你说", speaking: "她在说话，开口即可打断",
      denied: detail || "语音不可用", on: "语音中：再点一下关闭",
    };
    this.btn.textContent = labels[state] || "语音";
    this.btn.title = detail && state !== "denied" ? detail : (titles[state] || "");
    const on = ["listening", "speech", "speaking", "on"].includes(state);
    this.btn.setAttribute("aria-pressed", String(on));
    this.btn.dataset.state = on ? "on" : state;
    this.btn.disabled = false;
  }

  async toggle() {
    if (this.active) { this.stop(); return; }
    await this.start();
  }

  async start() {
    this.paint("connecting");
    let cfg = {};
    try { cfg = await fetch("/api/voice/config").then((r) => r.json()); } catch (_) {}
    const full = cfg.mode === "full";
    const spec = (full ? cfg.realtime : cfg.pipeline) || {};
    if (!spec.enabled) {
      const why = spec.reason || (full ? "全双工语音不可用（没有 API Key？）" : "语音不可用");
      this.paint("denied", why + " —— 去「设置」里看看");
      return;
    }
    const client = new DuplexVoice(this.avatar, { ...spec, kind: full ? "realtime" : "pipeline" });
    client.onState = (s) => { if (this.client === client) this.paint(s === "off" ? "off" : s); };
    client.onClose = () => { this.giveBack(); this.client = null; this.paint("off"); };
    try {
      this.client = client;
      await client.start();
      this.takeOver();
      this.paint("listening");
    } catch (e) {
      client.stop();
      this.client = null;
      this.giveBack();
      this.paint("denied", e.message || String(e));
    }
  }

  stop() {
    const c = this.client;
    this.client = null;
    if (c) c.stop();
    this.giveBack();
    this.paint("off");
  }

  /** 语音开着：字幕和声音由服务端按音频节奏给，播放器只负责表情和动作。 */
  takeOver() {
    const p = this.player;
    p.interrupt();
    p.sub = document.createElement("div");
    p.voice = null;
    p.mute = true;
  }

  giveBack() {
    const p = this.player;
    p.sub = p.base.sub;
    p.voice = p.base.voice;
    p.mute = false;
  }

  /** 设置里换了模式（全双工 ↔ 半双工）：连着的会话引擎不对了，断开让用户重新点。 */
  onConfigChanged() {
    if (this.active) {
      const kind = this.client.kind;
      fetch("/api/voice/config").then((r) => r.json()).then((cfg) => {
        const want = cfg.mode === "full" ? "realtime" : "pipeline";
        if (want !== kind) this.stop();
      }).catch(() => {});
    }
  }
}
