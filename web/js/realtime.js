/* 全双工语音：浏览器 PCM16 16k <-> /ws/voice <-> Qwen-Audio-Realtime。
 *
 * 麦克风常开；一开口由服务端 VAD 判定并打断当前回答（本地也会立刻停音频）。
 * 字幕走服务端 SSE 的 speech 事件，口型由播放中的真实音频驱动。
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

class RealtimeVoice {
  constructor(avatar, cfg = {}) {
    this.avatar = avatar;
    this.cfg = cfg;
    this.ws = null;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.analyser = null;
    this.nextAt = 0;
    this.sources = new Set();
    this.mouthTimer = null;
    this.endTimer = null;
    this.caption = "";
    this.speaking = false;        // 模型正在回答：暂停上传麦克风，避免把自己说的话收回去
    this.echoGuardUntil = 0;      // 回答结束后再静音一小段，躲开扬声器尾音
    this.captionTimer = null;
    this.active = false;
    this.onClose = null;      // 服务端主动断开时通知界面复位
    this._ready = null;
    this._fail = null;
  }

  get supported() {
    return "WebSocket" in window && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  async start() {
    if (this.active) return;
    if (!this.supported) throw new Error("当前浏览器不支持全双工语音");

    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    await this.ctx.resume();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(this.ctx.destination);

    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/voice`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (e) => this._onMessage(e);

    const ready = new Promise((resolve, reject) => {
      this._ready = resolve;
      this._fail = reject;
      setTimeout(() => reject(new Error("实时语音连接超时")), 12000);
    });
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.addEventListener("error", () => reject(new Error("实时语音连接失败")), { once: true });
    });
    await ready;                 // 等 session.updated

    await this._openMic();
    this.active = true;
    this.avatar.setState("listening");
    this._mouthLoop();
  }

  async _openMic() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const src = this.ctx.createMediaStreamSource(this.stream);
    const node = this.ctx.createScriptProcessor(4096, 1, 1);
    const mute = this.ctx.createGain();
    mute.gain.value = 0;                       // 只用来驱动处理，不外放麦克风
    src.connect(node);
    node.connect(mute);
    mute.connect(this.ctx.destination);
    node.onaudioprocess = (e) => {
      if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      // 她在说话（或刚说完的尾音期）就不上传：扬声器的声音会被麦克风收回去，
      // 模型把自己的话当成用户输入，就会一直自言自语。
      if (this.speaking || performance.now() < this.echoGuardUntil) return;
      const pcm = _downsampleTo16k(e.inputBuffer.getChannelData(0), this.ctx.sampleRate);
      if (pcm.length) this.ws.send(pcm.buffer);
    };
    this.node = node;
  }

  _onMessage(e) {
    if (typeof e.data === "string") {
      let m = null;
      try { m = JSON.parse(e.data); } catch (_) { return; }
      const type = m.type;
      if (type === "ready") {
        if (this._ready) { this._ready(); this._ready = null; this._fail = null; }
      } else if (type === "speech_started") {
        this.speaking = false;                  // 用户开口 = 她不再说话
        this.flush();                           // 一开口就停掉正在放的模型音频
        this.avatar.setState("listening");
      } else if (type === "caption") {
        // 服务端发的是"去掉标签后的累计文本"，直接覆盖
        this.caption = m.text || "";
        this._paintCaption();
      } else if (type === "caption_clear") {
        this.speaking = true;                   // 新回答开始，麦克风先让开
        this.caption = "";
        this._paintCaption();
      } else if (type === "caption_end") {
        // 回答结束：等扬声器里排队的音频放完再恢复麦克风
        const left0 = this.ctx ? Math.max(0, this.nextAt - this.ctx.currentTime) : 0;
        this.speaking = false;
        this.echoGuardUntil = performance.now() + left0 * 1000 + 800;
        clearTimeout(this.captionTimer);
        // 等已经排进 AudioContext 的音频播完，再把最后一句话收掉
        const left = this.ctx ? Math.max(0, this.nextAt - this.ctx.currentTime) : 0;
        this.captionTimer = setTimeout(() => {
          this.caption = "";
          this._paintCaption();
        }, left * 1000 + 800);
      } else if (type === "error") {
        console.warn("[realtime]", m.error);
        if (this._fail) { this._fail(new Error(String(m.error))); this._fail = null; }
      } else if (type === "transcript") {
        console.log("[realtime user]", m.text);
      } else if (type === "closed") {
        console.warn("[realtime] 会话被服务端关闭（长时间无输入）");
        this.stop();
        if (this.onClose) this.onClose();
      }
      return;
    }
    this._play(e.data);
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
    this._armEnd();
  }

  /* 音频队列播完时收口：停下来、回 idle/listening。
   * 没有这一步的话 talking 会一直为 true，avatar 的兜底口型会一直开合，
   * state 也会停在 speaking。 */
  _armEnd() {
    clearTimeout(this.endTimer);
    const left = this.ctx ? Math.max(0, this.nextAt - this.ctx.currentTime) : 0;
    this.endTimer = setTimeout(() => {
      if (this.sources.size) { this._armEnd(); return; }   // 还在放，往后推
      this.avatar.setTalking(false, "main");
      this.avatar.setMouthLevel(0, "main");
      // 麦克风还开着，但没有在说话：回到 idle，强情绪才会按 EMO_LINGER 自然回落
      this.avatar.setState("idle");
    }, left * 1000 + 220);
  }

  _paintCaption() {
    const el = document.querySelector("#subtitle");
    if (el) el.textContent = this.caption;
  }

  clearCaption() {
    clearTimeout(this.captionTimer);
    this.caption = "";
    this._paintCaption();
  }

  /** 打字也交给实时模型回答；连接不可用时返回 false，让调用方走别的路。 */
  sendText(text) {
    if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.flush();
    this.ws.send(JSON.stringify({ type: "text", text }));
    return true;
  }

  flush() {
    clearTimeout(this.endTimer);
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
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify({ type: "cancel" })); } catch (_) {}
    }
  }

  _mouthLoop() {
    if (!this.analyser) return;
    const data = new Uint8Array(this.analyser.fftSize);
    const tick = () => {
      if (!this.active) return;
      this.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) {
        const x = (v - 128) / 128;
        sum += x * x;
      }
      this.avatar.setMouthLevel(Math.min(1, Math.sqrt(sum / data.length) * 4.5), "main");
      this.mouthTimer = setTimeout(tick, 50);
    };
    tick();
  }

  stop() {
    this.active = false;
    clearTimeout(this.mouthTimer);
    clearTimeout(this.captionTimer);
    clearTimeout(this.endTimer);
    this.caption = "";
    this._paintCaption();
    this.flush();
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }
    if (this.node) { try { this.node.disconnect(); } catch (_) {} this.node = null; }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.ctx) { try { this.ctx.close(); } catch (_) {} this.ctx = null; }
    this.avatar.setState("idle");
  }
}
