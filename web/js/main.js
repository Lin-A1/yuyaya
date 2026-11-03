/* 入口：布局、事件总线、HUD、控制台。 */
const $ = (s) => document.querySelector(s);
const params = new URLSearchParams(location.search);
if (params.has("obs")) document.body.classList.add("obs");

const api = (path, body) => fetch(path, {
  method: body === undefined ? "GET" : "POST",
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
}).then((r) => r.json());

async function main() {
  const manifest = await fetch("assets/manifest.json").then((r) => r.json());
  const stage = $("#stage");
  const s = manifest.scene.screen;
  stage.style.setProperty("--scene-aspect", manifest.scene.width / manifest.scene.height);
  stage.style.setProperty("--sx", s.x * 100 + "%");
  stage.style.setProperty("--sy", s.y * 100 + "%");
  stage.style.setProperty("--sw", s.w * 100 + "%");
  stage.style.setProperty("--sh", s.h * 100 + "%");
  $("#scene").src = manifest.scene.src;
  document.querySelectorAll(".room").forEach((img) => (img.src = manifest.room.src));
  stage.style.setProperty("--desk-clip", manifest.room.deskTop * 100 + "%");
  stage.style.setProperty("--char-aspect", manifest.sprites.width / manifest.sprites.height);

  const avatar = (window.avatar = new Avatar($("#char"), manifest));   // 挂到 window 上方便在控制台调试
  await avatar.load();
  if (params.get("render") === "tha3") avatar.remote = new Tha3View($("#char"), manifest.sprites, params.get("tha3") || undefined);
  layout(manifest);
  new ResizeObserver(() => layout(manifest)).observe($("#feed"));

  const voice = await createVoice(avatar, params);
  const player = new SpeechPlayer({ avatar, subtitle: $("#subtitle"), voice });
  const hud = new Hud(avatar);
  connect({ avatar, player, hud });
  const voiceChat = setupVoice({ avatar, player });
  setupConsole({ avatar, player, voiceChat });
}

async function createVoice(avatar, params) {
  const mode = (params.get("tts") || "auto").toLowerCase();
  if (mode === "none" || mode === "off") return null;
  const browser = "speechSynthesis" in window ? new BrowserVoice(avatar) : null;
  if (mode === "browser") return browser;
  try {
    const cfg = await fetch("/api/voice/config").then((r) => r.json());
    if (cfg.tts?.enabled) return new ServerVoice(avatar, cfg.tts);
  } catch (e) {
    console.warn("[tts]", e.message);
  }
  return browser;
}

function setupVoice({ avatar, player }) {
  const btn = $("#mic-btn");
  if (!btn) return null;
  const labels = { off: "语音", listening: "聆听中", speech: "在听", heard: "处理中", denied: "无权限" };
  const session = new VoiceSession({
    onText: (text) => {
      api("/api/interrupt", {});
      player.interrupt();
      avatar.setState("idle");
      showUserLine(text);
      api("/api/chat", { text, wait: false, voice: true });
    },
    onState: (state) => {
      const on = state === "listening" || state === "speech" || state === "heard";
      btn.textContent = labels[state] || "语音";
      btn.setAttribute("aria-pressed", String(on));
      btn.dataset.state = state;
    },
  });
  if (!session.supported) {
    btn.disabled = true;
    btn.title = "当前浏览器不支持语音识别";
    return session;
  }
  btn.addEventListener("click", async () => {
    try {
      if (session.active) session.stop();
      else await session.start();
    } catch (e) {
      btn.title = e.message;
      btn.dataset.state = "denied";
    }
  });
  addEventListener("beforeunload", () => session.stop());
  return session;
}

/** 让立绘"坐"在桌子后面：立绘下沿藏到桌面上沿以下一点点。 */
function layout(manifest) {
  const feed = $("#feed");
  const room = feed.querySelector(".room");
  const W = feed.clientWidth, H = feed.clientHeight;
  if (!W || !room.naturalWidth) return requestAnimationFrame(() => layout(manifest));
  const aspect = room.naturalWidth / room.naturalHeight;
  // 房间图铺满（cover），底部对齐
  let rw = W, rh = W / aspect;
  if (rh < H) { rh = H; rw = H * aspect; }
  feed.querySelectorAll(".room").forEach((img) => {
    img.style.width = rw + "px";
    img.style.left = (W - rw) / 2 + "px";
  });
  const deskY = H - (1 - manifest.room.deskTop) * rh;
  const charH = H * 0.76;
  $("#char").style.height = charH + "px";
  $("#char").style.bottom = H - deskY - H * 0.035 + "px";
}

// ---------------- 事件总线 ----------------
function connect({ avatar, player, hud }) {
  const es = new EventSource("/api/events");
  es.onopen = () => hud.set("link", "ONLINE");
  es.onerror = () => hud.set("link", "RECONNECTING");
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    switch (e.type) {
      case "hello":
        // 刷新/重连恢复持续情绪；思考、说话属于上一次连接的瞬态，不重新播放
        avatar.setEmotion(e.emotion, { react: false, instant: true });
        avatar.setState(e.state === "listening" ? "listening" : "idle");
        hud.brains(e);
        hud.set("facts", String(e.facts || 0));   // 刷新后恢复记忆条数
        break;
      case "brain":
        hud.brains(e);
        break;
      case "user":
        showUserLine(e.text);
        break;
      case "speech":
        if (e.op === "begin") player.begin(e.id, { queue: !!e.queue });
        else player.push(e);
        break;
      case "state":
        if (e.state === "thinking" && !$("#subtitle").textContent) $("#subtitle").innerHTML = '<span class="dots"></span>';
        if (e.state !== "thinking") $("#subtitle .dots")?.remove();
        avatar.setState(e.state);
        break;
      case "emotion":
        avatar.setEmotion(e.name, { hold: e.hold || 0, instant: !!e.instant });
        break;
      case "memory":
        hud.remember(e.text);
        break;
      case "motion":
        avatar.motion(e.name);
        break;
      case "mouth":
        if (Array.isArray(e.levels)) avatar.playMouthLevels(e.levels, e.fps || 60);
        else avatar.setMouthLevel(+e.level || 0);
        break;
      case "interrupt":
        player.interrupt();
        avatar.setState("idle");
        break;
      case "action_log":
        console.log("[action]", e.text);
        break;
      case "action":
        if (e.state === "started") $("#hud-state").textContent = "ACTION";
        if (e.state === "finished" || e.state === "failed" || e.state === "timeout") {
          $("#hud-state").textContent = "IDLE";
        }
        break;
      case "stats":
        if (e.first_token !== undefined) hud.set("latency", e.first_token.toFixed(2) + "s");
        if (e.total !== undefined) hud.turn();
        break;
      case "error":
        console.warn("[brain error]", e.brain, e.message);
        break;
    }
  };
}

let userLineTimer;
function showUserLine(text) {
  const el = $("#user-line");
  el.textContent = "— " + (text.length > 40 ? text.slice(0, 40) + "…" : text);
  el.classList.add("on");
  clearTimeout(userLineTimer);
  userLineTimer = setTimeout(() => el.classList.remove("on"), 3500);
}

// ---------------- HUD ----------------
const EMO_LEVEL = { neutral: 30, happy: 78, sad: 62, surprised: 88, thinking: 45, shy: 55, angry: 92 };

class Hud {
  constructor(avatar) {
    this.turns = 0;
    this.brainList = [];
    this.t0 = Date.now();
    $("#hud-session").textContent = String(Math.floor(1000 + Math.random() * 9000));
    avatar.onChange = ({ emotion, target, state }) => {
      // 过渡中显示 "SAD → HAPPY"
      this.set("emotion", emotion.toUpperCase() + (target && target !== emotion ? " → " + target.toUpperCase() : ""));
      this.set("state", state.toUpperCase());
      $("#screen").dataset.state = state;
      $("#hud-meter").style.width = (EMO_LEVEL[emotion] || 30) + "%";
      document.querySelector(".rec").classList.toggle("live", state === "speaking" || state === "listening");
    };
    setInterval(() => this.tick(), 250);
  }
  set(key, val) { const el = $("#hud-" + key); if (el && el.textContent !== val) el.textContent = val; }
  /** 记住了一件事：HUD 计数 +1，字幕上方飘一行提示 */
  remember(text) {
    this.facts = (this.facts || 0) + 1;
    this.set("facts", String(this.facts));
    const el = $("#memory-note");
    if (!el) return;
    el.textContent = "记住：" + text;
    el.classList.add("on");
    clearTimeout(this._noteTimer);
    this._noteTimer = setTimeout(() => el.classList.remove("on"), 3200);
  }
  turn() { this.set("turns", String(++this.turns)); }
  brains(info) {
    this.brainList = info.brains;
    const chatBrains = info.brains.filter((x) => x.role !== "agent" && x.available !== false);
    const b = chatBrains.find((x) => x.name === info.active) || chatBrains[0] || {};
    this.set("brain", (b.label || info.active || "—").toUpperCase());
    this.set("model", b.model || "—");
    const sel = $("#brain-select");
    sel.innerHTML = chatBrains.length
      ? chatBrains.map((x) => `<option value="${x.name}">${x.label}</option>`).join("")
      : '<option value="" disabled>暂无可用聊天模型</option>';
    sel.value = b.name || "";
    sel.hidden = chatBrains.length <= 1;
    sel.disabled = chatBrains.length <= 1;
    sel.title = chatBrains.length > 1 ? "切换聊天大脑" : "当前只有一个聊天模型";
  }
  tick() {
    const sec = Math.floor((Date.now() - this.t0) / 1000);
    const p = (n) => String(n).padStart(2, "0");
    $("#timer").textContent = `${p(Math.floor(sec / 3600))}:${p(Math.floor(sec / 60) % 60)}:${p(sec % 60)}`;
    $("#progress").style.width = ((sec % 600) / 6).toFixed(2) + "%";
  }
}

// ---------------- 控制台 ----------------
function setupConsole({ voiceChat = null } = {}) {
  const form = $("#console"), input = $("#input"), sel = $("#brain-select");
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    api("/api/chat", { text, wait: false, voice: false });
  });
  sel.addEventListener("change", () => api("/api/brain", { name: sel.value }));

  const EMO_KEYS = ["neutral", "happy", "sad", "surprised", "thinking", "shy", "angry"];
  const MOTION_KEYS = { n: "nod", s: "shake", h: "hop", j: "jump", d: "droop", t: "tremble", g: "tilt", w: "wiggle", f: "sniff", x: "sigh" };
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { api("/api/interrupt", {}); input.blur(); return; }
    if (document.activeElement === input) return;
    if (ev.key === "Enter") { ev.preventDefault(); showConsole(); input.focus(); return; }
    const i = Number(ev.key) - 1;
    if (EMO_KEYS[i]) api("/api/emotion", { name: EMO_KEYS[i] });   // 1~7 调试表情
    else if (MOTION_KEYS[ev.key]) api("/api/motion", { name: MOTION_KEYS[ev.key] });   // 字母键调试动作
  });

  // 鼠标不动 4 秒后自动隐藏，保持画面干净
  let hideTimer;
  const showConsole = () => {
    form.classList.remove("hidden");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (document.activeElement !== input && !input.value) form.classList.add("hidden");
    }, 4000);
  };
  document.addEventListener("mousemove", showConsole);
  input.addEventListener("blur", showConsole);
  showConsole();
}

main().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<pre style="position:fixed;top:8px;left:8px;color:#f88">${e}</pre>`);
});
