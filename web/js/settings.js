/* 设置面板：对话与语音（LLM/TTS/ASR）、全双工、人设、Agent。
 *
 * 每个通道长得一样：Base URL + API Key + 模型列表；
 * LLM 是通用接口，只选 API 格式（Completions / Responses / Messages），没有"厂商"；
 * TTS / ASR / 全双工按各自适配器选厂商。
 * 数据来自 GET /api/settings，保存走 POST /api/settings；key 只写 state/secrets.json。
 */
const SettingsPanel = {
  data: null,
  player: null,
  tab: "half",
  chan: {},          // 每个通道的内存编辑态（避免每次重渲染丢输入）

  async open() {
    const box = $("#settings");
    if (!box) return;
    box.hidden = false;
    this.setStatus("读取中…");
    for (let i = 0; i < 40 && typeof this.player !== "object"; i++) {
      await new Promise((r) => setTimeout(r, 250));
    }
    try {
      this.data = await api("/api/settings");
    } catch (e) {
      this.setStatus("读取设置失败：" + e.message);
      return;
    }
    this.chan = {};      // 重新从服务端数据初始化
    this.render();
    this.setStatus("");
    this.showTab(this.data.mode === "full" ? "full" : "half");
  },

  close() {
    const box = $("#settings");
    if (box) box.hidden = true;
    this.chan = {};
  },

  setStatus(text) {
    const el = $("#cfg-status");
    if (el) el.textContent = text;
  },

  showTab(name) {
    this.tab = name;
    document.querySelectorAll(".cfg-tabs button").forEach((b) => {
      b.classList.toggle("on", b.dataset.tab === name);
    });
    document.querySelectorAll(".cfg-page").forEach((p) => {
      p.hidden = p.dataset.page !== name;
    });
  },

  // ------------------------------------------------------------ 通道
  edit(name) {
    if (!this.chan[name]) this.chan[name] = JSON.parse(JSON.stringify(this.data[name] || {}));
    return this.chan[name];
  },

  /** 一个通道的编辑区。
   *  LLM / TTS / ASR 都是通用接口：API 格式 + Base URL + API Key + 模型列表，没有"厂商"；
   *  Edge / Windows 本机 / 不启用 这类本地引擎不需要地址和 Key，整块不渲染；
   *  只有全双工还是按厂商选。 */
  renderChannel(name, mount, title, hint) {
    const c = this.edit(name);
    const vendors = c.vendors || [];
    const models = c.models || [];
    const typeRow = (c.types || []).find((t) => t.id === c.type) || {};
    const generic = ["llm", "tts", "asr"].includes(name);
    const isDemo = c.type === "demo" || c.vendor === "demo";
    const noConn = isDemo || !!typeRow.local;      // 不联网的：不需要地址 / Key / 模型表
    const voices = name === "tts" ? this.voicesFor(c) : (c.voices || []);

    mount.innerHTML = `
      <section class="cfg-chan" data-chan="${name}">
        <h4>${title}${hint ? `<small>${hint}</small>` : ""}</h4>
        ${generic ? "" : `<label class="cfg-field"><span>厂商</span>
          <select class="c-vendor">${vendors.map((v) =>
            `<option value="${v.id}"${v.id === c.vendor ? " selected" : ""}>${v.label}</option>`).join("")}
          </select>
        </label>`}
        ${c.types ? `<label class="cfg-field"><span>API 格式</span>
          <select class="c-type">${c.types.map((t) =>
            `<option value="${t.id}"${t.id === c.type ? " selected" : ""}>${t.label}</option>`).join("")}
          </select></label>` : ""}
        ${noConn ? "" : `
        <label class="cfg-field c-base"><span>Base URL</span>
          <input class="c-base-input" value="${esc(c.base_url || "")}"
                 placeholder="${esc(typeRow.default_base || "https://…/v1")}">
        </label>
        <label class="cfg-field c-key"><span>API Key</span>
          <input class="c-key-input" type="password" autocomplete="off" spellcheck="false"
                 placeholder="${c.has_key ? "已设置 " + c.key_hint + "，留空不改" : "未设置"}">
        </label>
        <div class="cfg-field cfg-field--top c-models">
          <span>模型列表${models.length > 12 ? `（共 ${models.length} 个，可搜索）` : ""}</span>
          ${models.length > 12 ? `<input class="c-filter" placeholder="搜模型名…">` : ""}
          <div class="c-model-list">
            ${models.map((m) => this.modelRow(m, c.model)).join("")
              || '<div class="c-model-empty">还没有模型，输入模型名添加，或点「拉取模型」</div>'}
          </div>
          <div class="c-model-add">
            <input class="c-new-model" placeholder="输入模型名，回车添加">
            <button type="button" class="cfg-ghost c-add">+ 添加</button>
            ${generic ? '<button type="button" class="cfg-ghost c-fetch" title="用上面的 Base URL 和 Key 去问服务有哪些模型">拉取模型</button>' : ""}
          </div>
          ${generic ? `<small class="c-fetch-msg cfg-policy">${esc(c._fetchMsg || "")}</small>` : ""}
        </div>`}
        ${name === "llm" && !isDemo ? `<label class="cfg-field"><span>额外请求参数<small>（JSON，原样合并进请求；比如关掉思考）</small></span>
          <input class="c-extra" spellcheck="false" autocomplete="off"
                 value="${esc(c._extraText ?? (Object.keys(c.extra || {}).length ? JSON.stringify(c.extra) : ""))}"
                 placeholder='{"enable_thinking": false}'>
          <small class="c-extra-msg cfg-policy"></small></label>` : ""}
        ${voices.length || name === "tts" ? `<label class="cfg-field"><span>音色${
            name === "tts" ? "<small>（跟着模型走，也可以直接输入音色名）</small>" : ""}</span>
          <input class="c-voice" list="cfg-voices-${name}" value="${esc(c.voice || "")}"
                 placeholder="${esc((voices[0] || {}).id || "音色名")}" autocomplete="off">
          <datalist id="cfg-voices-${name}">${voices.map((v) =>
            `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("")}</datalist>
          </label>` : ""}
        <small class="cfg-policy">${typeRow.note || (generic ? "" : (c.vendors || []).find(
          (v) => v.id === c.vendor)?.note || "")}</small>
      </section>`;

    // 厂商（只有全双工还有）：换厂商时把 base / 模型表换成该厂商的默认
    this.on(mount, ".c-vendor", "change", (el) => {
      const v = vendors.find((x) => x.id === el.value) || {};
      c.vendor = el.value;
      if (!c.keep_base) c.base_url = v.default_base || "";
      if (v.default_models && v.default_models.length) {
        c.models = v.default_models.slice();
        c.model = c.models[0].id;
      }
      this.renderChannel(name, mount, title, hint);
    });
    // API 格式
    this.on(mount, ".c-type", "change", (el) => {
      const old = (c.types || []).find((x) => x.id === c.type) || {};
      const t = (c.types || []).find((x) => x.id === el.value) || {};
      // Base URL 空着、或者还是上一个格式的默认值 → 跟着换；用户自己填的不动
      if (!c.base_url || c.base_url === old.default_base) c.base_url = t.default_base || "";
      c.type = el.value;
      c._fetchMsg = "";
      if (name === "tts" || name === "asr") {
        // 模型表、音色是跟着接口走的：换接口就换成新接口自己的
        c.models = (t.default_models || []).map((m) => ({ ...m }));
        c.model = (c.models[0] || {}).id || "";
        if (name === "tts") {
          const vs = this.voicesFor(c);
          c.voice = (vs[0] || {}).id || "";
        }
      }
      this.renderChannel(name, mount, title, hint);
    });
    // 拉取模型：用表单里还没保存的 Base URL / Key 直接问服务
    const fetchBtn = mount.querySelector(".c-fetch");
    if (fetchBtn) {
      fetchBtn.addEventListener("click", async () => {
        fetchBtn.disabled = true;
        const msg = mount.querySelector(".c-fetch-msg");
        if (msg) msg.textContent = "拉取中…";
        try {
          const body = { channel: name, type: c.type, base_url: c.base_url || "" };
          if (c._keyTouched) body.api_key = c._key || "";
          const r = await api("/api/settings/models", body);
          if (r.ok) {
            c.models = r.models;
            if (!r.models.some((m) => m.id === c.model)) c.model = r.models[0].id;
            c._fetchMsg = `拉到 ${r.models.length} 个模型`;
          } else {
            c._fetchMsg = "拉取失败：" + (r.error || "未知原因") + "（可以手动输入模型名）";
          }
        } catch (e) {
          c._fetchMsg = "拉取失败：" + e.message;
        }
        this.renderChannel(name, mount, title, hint);
      });
    }
    this.on(mount, ".c-base-input", "input", (el) => { c.base_url = el.value.trim(); });
    this.on(mount, ".c-key-input", "input", (el) => {
      c._keyTouched = true;
      c._key = el.value;
    });
    this.on(mount, ".c-voice", "input", (el) => { c.voice = el.value.trim(); });
    this.on(mount, ".c-extra", "input", (el) => {
      c._extraText = el.value;
      const msg = mount.querySelector(".c-extra-msg");
      let ok = true, why = "";
      if (el.value.trim()) {
        try {
          const v = JSON.parse(el.value);
          if (!v || typeof v !== "object" || Array.isArray(v)) { ok = false; why = "必须是 JSON 对象"; }
        } catch (e) { ok = false; why = "JSON 格式不对"; }
      }
      c._extraBad = !ok;
      if (msg) msg.textContent = ok ? "" : "⚠ " + why + "，保存时会报错";
    });

    // 模型：选中 / 改名 / 删除 / 添加
    mount.querySelectorAll(".c-model").forEach((row) => {
      const id = row.dataset.id;
      const pick = row.querySelector('input[type="radio"]');
      pick.addEventListener("change", async () => {
        c.model = id;
        mount.querySelectorAll(".c-model").forEach((r) =>
          r.classList.toggle("on", r.dataset.id === id));
        // 音色跟着模型走：换模型后按这个模型的音色表重算（当前音色不在表里就换成第一个）
        if (name === "tts") {
          const vs = this.voicesFor(c);
          if (vs.length && !vs.some((v) => v.id === c.voice)) c.voice = vs[0].id;
          this.renderChannel(name, mount, title, hint);
        }
      });
      const rename = row.querySelector(".c-edit");
      rename.addEventListener("click", () => {
        const m = c.models.find((x) => x.id === id);
        const val = prompt("模型名", m.id);
        if (!val) return;
        m.id = val.trim();
        m.label = val.trim();
        if (c.model === id) c.model = m.id;
        this.renderChannel(name, mount, title, hint);
      });
      row.querySelector(".c-del").addEventListener("click", () => {
        if (c.models.length <= 1) { this.setStatus("至少留一个模型"); return; }
        c.models = c.models.filter((x) => x.id !== id);
        if (c.model === id) c.model = c.models[0].id;
        this.renderChannel(name, mount, title, hint);
      });
    });
    const box = mount.querySelector(".c-filter");
    if (box) {
      box.addEventListener("input", () => {
        const q = box.value.trim().toLowerCase();
        mount.querySelectorAll(".c-model").forEach((r) => {
          r.style.display = !q || r.dataset.id.toLowerCase().includes(q) ? "" : "none";
        });
      });
    }
    const add = () => {
      const input = mount.querySelector(".c-new-model");
      if (!input) return;
      const val = (input.value || "").trim();
      if (!val) return;
      if (c.models.some((m) => m.id === val)) { this.setStatus("已经有这个模型了"); return; }
      c.models.push({ id: val, label: val });
      c.model = c.model || val;
      input.value = "";
      this.renderChannel(name, mount, title, hint);
    };
    mount.querySelector(".c-add")?.addEventListener("click", add);
    mount.querySelector(".c-new-model")?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); add(); }
    });
  },

  /** 当前接口 + 当前模型能选的音色：先找这个模型自己的表，再退回接口通用表。 */
  voicesFor(c) {
    const t = (c.types || []).find((x) => x.id === c.type) || {};
    const by = t.voices_by_model || {};
    return by[c.model] || by["*"] || t.voices || c.voices || [];
  },

  modelRow(m, current) {
    const on = m.id === current;
    return `
      <div class="c-model ${on ? "on" : ""}" data-id="${esc(m.id)}">
        <input type="radio" name="cfg-model-pick" ${on ? "checked" : ""}>
        <span class="c-model-name">${esc(m.label || m.id)}</span>
        <button type="button" class="c-edit" title="改名">✎</button>
        <button type="button" class="c-del" title="删除">🗑</button>
      </div>`;
  },

  on(mount, sel, evt, fn) {
    const el = mount.querySelector(sel);
    if (el) el.addEventListener(evt, () => fn(el));
  },

  // ------------------------------------------------------------ 渲染
  render() {
    if (!this.data) return;
    this.renderMode();
    this.renderChannel("llm", $("#cfg-chan-llm"), "LLM（对话）",
                       "决定她说什么、怎么想");
    this.renderChannel("tts", $("#cfg-chan-tts"), "TTS（说话）", "把回复念出来");
    this.renderChannel("asr", $("#cfg-chan-asr"), "ASR（听你说话）", "麦克风转文字");
    this.renderChannel("full", $("#cfg-chan-full"), "全双工语音", "音频级实时对话");
    this.renderPersona();
  },

  renderMode() {
    const mode = this.data.mode || "half";
    document.querySelectorAll("#cfg-modebar .cfg-mode-opt").forEach((el) => {
      const on = el.dataset.mode === mode;
      el.classList.toggle("on", on);
      const r = el.querySelector('input[type="radio"]');
      if (r) r.checked = on;
    });
    const onFull = mode === "full";
    const lead = $("#cfg-half-lead");
    if (lead) {
      lead.textContent = onFull
        ? "⚠ 当前是「全双工」模式，这里的 LLM / TTS / ASR 不会生效。点顶部「伪全双工」切换。"
        : "你说话 → ASR 转文字 → LLM 回答 → TTS 念出来。麦克风常开，开口即可打断；三件各自独立，可以分别换。";
      lead.classList.toggle("cfg-lead--warn", onFull);
    }
    const flead = $("#cfg-full-lead");
    if (flead) {
      flead.textContent = onFull
        ? "音频进、音频出，随时能听、随时打断。"
        : "⚠ 当前是「伪全双工」模式，这里的全双工不会生效。点顶部「全双工」切换。";
      flead.classList.toggle("cfg-lead--warn", !onFull);
    }
  },

  renderPersona() {
    const p = this.data.persona || {};
    $("#cfg-p-name").value = p.name || "";
    $("#cfg-p-address").value = p.address || "";
    $("#cfg-p-identity").value = p.identity || "";
    $("#cfg-p-extra").value = p.extra || "";
    $("#cfg-p-note").textContent = p.customized ? "正在使用自定义人设" : "使用内置人设";
    $("#cfg-p-rules").textContent = this.data.persona_rules || "";
  },

  fillSelect(sel, items, value) {
    const list = items || [];
    if (!list.length) { sel.innerHTML = '<option value="">（不需要选）</option>'; return; }
    const has = list.some((i) => i.id === value);
    sel.innerHTML = list.map((i) => `<option value="${i.id}">${i.label}</option>`).join("")
      + (value && !has ? `<option value="${value}">${value}</option>` : "");
    sel.value = value || list[0].id;
  },

  // ------------------------------------------------------------ 保存
  collect() {
    const out = { mode: this.data.mode };
    for (const [name, c] of Object.entries(this.chan)) {
      const patch = { base_url: c.base_url, model: c.model, models: c.models || [] };
      if (!["llm", "tts", "asr"].includes(name)) patch.vendor = c.vendor;
      if (c.types) patch.type = c.type;
      if (name === "llm" && c._extraText !== undefined) patch.extra = c._extraText.trim();
      if (c.voice) patch.voice = c.voice;
      if (c._keyTouched) patch.api_key = c._key || "";
      out[name] = patch;
    }
    out.persona = {
      name: $("#cfg-p-name").value,
      address: $("#cfg-p-address").value,
      identity: $("#cfg-p-identity").value,
      extra: $("#cfg-p-extra").value,
    };
    return out;
  },

  async save() {
    const btn = $("#cfg-save");
    btn.disabled = true;
    this.setStatus("保存中…");
    try {
      const body = this.collect();
      this.data = await api("/api/settings", body);
      this.chan = {};
      this.render();
      this.applyVoice();
      const changed = this.data.changed || {};
      const parts = Object.entries(changed).slice(0, 4)
        .map(([k, v]) => `${k}=${v === true ? "开" : v === false ? "关" : v}`);
      this.setStatus(parts.length ? "已保存 · " + parts.join(" · ")
                                 + (Object.keys(changed).length > 4 ? " 等" : "")
                                 : "已保存（无改动）");
      this.broadcast();
    } catch (e) {
      this.setStatus("保存失败：" + e.message);
    } finally {
      btn.disabled = false;
    }
  },

  applyVoice() {
    const voice = this.player?.base?.voice || window.__voice;
    if (!voice || !voice.conf) return;
    const c = this.data.tts || {};
    if (c.voice) voice.conf.voice = c.voice;
    if (c.type) voice.conf.provider = c.type;
  },

  broadcast() {
    // 模式换了：连着的语音会话引擎就不对了，交给麦克风控制器处理（断开，让用户重新点）
    if (window.__mic) window.__mic.onConfigChanged();
  },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function setupSettings() {
  const btn = $("#cfg-btn");
  if (!btn) return null;
  const box = $("#settings");
  btn.addEventListener("click", () => SettingsPanel.open());
  $("#cfg-close").addEventListener("click", () => SettingsPanel.close());
  box.addEventListener("click", (ev) => { if (ev.target === box) SettingsPanel.close(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !box.hidden) {
      SettingsPanel.close();
      ev.stopImmediatePropagation();
    }
  }, true);
  $("#cfg-save").addEventListener("click", () => SettingsPanel.save());
  document.querySelectorAll(".cfg-tabs button").forEach((b) => {
    b.addEventListener("click", () => SettingsPanel.showTab(b.dataset.tab));
  });
  document.querySelectorAll("#cfg-modebar .cfg-mode-opt").forEach((el) => {
    const pick = () => {
      SettingsPanel.data.mode = el.dataset.mode;
      SettingsPanel.renderMode();
      SettingsPanel.showTab(el.dataset.mode === "full" ? "full" : "half");
      SettingsPanel.setStatus("已切到" + (el.dataset.mode === "full" ? "全双工" : "伪全双工")
                              + "，记得点保存");
    };
    el.addEventListener("click", pick);
    const r = el.querySelector('input[type="radio"]');
    if (r) r.addEventListener("change", pick);
  });
  $("#cfg-p-default").addEventListener("click", () => {
    $("#cfg-p-identity").value = (SettingsPanel.data.persona || {}).default_identity || "";
    $("#cfg-p-note").textContent = "已填入内置人设，可以在这上面改";
  });
  $("#cfg-p-clear").addEventListener("click", () => {
    $("#cfg-p-identity").value = "";
    $("#cfg-p-extra").value = "";
    $("#cfg-p-note").textContent = "保存后回到内置人设";
  });
  return SettingsPanel;
}
