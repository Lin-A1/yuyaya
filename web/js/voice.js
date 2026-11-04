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
