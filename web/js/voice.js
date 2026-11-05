/* Continuous browser speech input with barge-in. Server TTS handles replies. */
class VoiceSession {
  constructor({ onText, onState = () => {}, onSpeechStart = () => {} } = {}) {
    this.onText = onText;
    this.onState = onState;
    this.onSpeechStart = onSpeechStart;   // 一开口就通知（全双工打断）
    this.active = false;
    this.recognition = null;
    this.stream = null;
    this.restartTimer = null;
    this.lastFinal = "";
    this.lastFinalAt = 0;
    this.SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  get supported() { return !!this.SR; }

  async start() {
    if (!this.SR) throw new Error("当前浏览器不支持语音识别");
    if (this.active) return;
    if (navigator.mediaDevices?.getUserMedia) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    }
    this.active = true;
    this._connect();
    this.onState("listening");
  }

  stop() {
    this.active = false;
    clearTimeout(this.restartTimer);
    if (this.recognition) {
      try { this.recognition.abort(); } catch (_) {}
    }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.onState("off");
  }

  _connect() {
    if (!this.active) return;
    const rec = new this.SR();
    this.recognition = rec;
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "zh-CN";

    rec.onstart = () => this.active && this.onState("listening");
    rec.onspeechstart = () => {
      if (!this.active) return;
      this.onState("speech");
      this.onSpeechStart();
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        this.stop();
        this.onState("denied");
      }
    };
    rec.onend = () => {
      if (!this.active) return;
      clearTimeout(this.restartTimer);
      this.restartTimer = setTimeout(() => this._connect(), 250);
    };
    rec.onresult = (e) => {
      if (!this.active) return;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (!e.results[i].isFinal) continue;
        const text = [...e.results[i]].map((x) => x.transcript).join("").trim();
        this._accept(text);
      }
    };
    try {
      rec.start();
    } catch (_) {
      // onend will retry after the previous recognizer has fully stopped
    }
  }

  _accept(text) {
    if (!text || text.length < 2 || this._isEcho(text)) return;
    const now = performance.now();
    if (text === this.lastFinal && now - this.lastFinalAt < 1600) return;
    this.lastFinal = text;
    this.lastFinalAt = now;
    this.onState("heard");
    this.onText(text);
  }

  _isEcho(text) {
    const current = (document.querySelector("#subtitle")?.textContent || "").replace(/\s+/g, "");
    const heard = text.replace(/\s+/g, "");
    if (current.length < 2 || heard.length < 2) return false;
    let hit = 0;
    for (const ch of heard) if (current.includes(ch)) hit++;
    return hit / heard.length > 0.72;
  }
}

/** 服务端语音识别：录一段话 → POST /api/asr → 拿到文字当一次输入。
 *
 * 和上面的 VoiceSession 接口一样（start/stop/active/onText/onState），
 * 区别是识别在服务端做，浏览器只需要 record + 上传，不依赖浏览器自带的
 * SpeechRecognition（Chrome 之外基本没有）。
 */
class ServerASRSession {
  constructor({ onText, onState = () => {}, onSpeechStart = () => {} } = {}) {
    this.onText = onText;
    this.onState = onState;
    this.onSpeechStart = onSpeechStart;
    this.active = false;
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
    this.busy = false;
    this.SR = null;
  }

  get supported() {
    return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  }

  async start() {
    if (!this.supported) throw new Error("当前浏览器不支持录音（需要 mediaDevices + MediaRecorder）");
    if (this.active) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.active = true;
    this._loop();
  }

  stop() {
    this.active = false;
    this._stopRecorder();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.onState("off");
  }

  _stopRecorder() {
    if (this.recorder && this.recorder.state !== "inactive") {
      try { this.recorder.stop(); } catch (_) {}
    }
    this.recorder = null;
  }

  /** 按静音切句：一直录，检测到一段安静就把这一句发去识别，然后接着录。 */
  _loop() {
    if (!this.active) return;
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", ""]
      .find((m) => !m || MediaRecorder.isTypeSupported(m));
    this.chunks = [];
    const rec = mime ? new MediaRecorder(this.stream, { mimeType: mime }) : new MediaRecorder(this.stream);
    this.recorder = rec;
    rec.ondataavailable = (e) => { if (e.data && e.data.size) this.chunks.push(e.data); };
    rec.onstop = async () => {
      const blob = new Blob(this.chunks, { type: mime || "audio/webm" });
      this.chunks = [];
      if (this.active && !this.busy && blob.size > 2000) await this._send(blob);
      if (this.active) setTimeout(() => this._loop(), 120);
    };
    rec.start();
    this.onState("listening");
    this._watch(rec);
  }

  /** 简易 VAD：音量低于阈值持续 900ms 就收尾当前这句 */
  _watch(rec) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(this.stream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    const buf = new Float32Array(an.fftSize);
    let quietSince = 0, spoke = false, raf = 0;
    const tick = () => {
      if (!this.active || rec.state === "inactive") {
        cancelAnimationFrame(raf);
        try { ctx.close(); } catch (_) {}
        return;
      }
      an.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      const rms = Math.sqrt(sum / buf.length);
      const now = performance.now();
      if (rms > 0.012) {
        if (!spoke) { spoke = true; this.onState("speech"); this.onSpeechStart(); }
        quietSince = 0;
      } else if (spoke) {
        if (!quietSince) quietSince = now;
        else if (now - quietSince > 900) {
          spoke = false;
          this.onState("listening");
          try { rec.stop(); } catch (_) {}
          return;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    this._raf = raf;
    this._ctx = ctx;
  }

  async _send(blob) {
    this.busy = true;
    this.onState("heard");
    try {
      const res = await fetch("/api/asr", { method: "POST", body: blob });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(`HTTP ${res.status}${detail ? " · " + detail : ""}`);
      }
      const { text } = await res.json();
      if (text) this.onText(text);
    } catch (e) {
      console.warn("[asr]", e.message);
      this.onState("error");
    } finally {
      this.busy = false;
    }
  }
}
