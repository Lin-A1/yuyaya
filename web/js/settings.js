/* 设置面板：对话与语音（LLM/TTS/ASR）、全双工、人设、Agent。
 *
 * 每个通道长得一样：厂商 + Base URL + API 格式（LLM 才有）+ API Key + 模型列表。
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

  /** 一个通道的编辑区：厂商 / Base URL / API 格式 / Key / 模型列表 */
  renderChannel(name, mount, title, hint) {
    const c = this.edit(name);
    const vendors = c.vendors || [];
    const models = c.models || [];
    const typeRow = (c.types || []).find((t) => t.id === c.type) || {};
    const isDemo = c.type === "demo" || c.vendor === "demo";
    const keyed = c.keyed !== false;

    mount.innerHTML = `
      <section class="cfg-chan" data-chan="${name}">
        <h4>${title}${hint ? `<small>${hint}</small>` : ""}</h4>
        <label class="cfg-field"><span>厂商</span>
          <select class="c-vendor">${vendors.map((v) =>
            `<option value="${v.id}"${v.id === c.vendor ? " selected" : ""}>${v.label}</option>`).join("")}
          </select>
        </label>
        ${c.types ? `<label class="cfg-field"><span>API 格式</span>
          <select class="c-type">${c.types.map((t) =>
            `<option value="${t.id}"${t.id === c.type ? " selected" : ""}>${t.label}</option>`).join("")}
          </select></label>` : ""}
        <label class="cfg-field c-base" ${isDemo ? "hidden" : ""}><span>Base URL</span>
          <input class="c-base-input" value="${esc(c.base_url || "")}" placeholder="https://…/v1">
        </label>
        ${keyed ? `<label class="cfg-field c-key" ${isDemo ? "hidden" : ""}><span>API Key</span>
          <input class="c-key-input" type="password" autocomplete="off" spellcheck="false"
                 placeholder="${c.has_key ? "已设置 " + c.key_hint + "，留空不改" : "未设置"}">
        </label>` : ""}
        <div class="cfg-field cfg-field--top c-models" ${isDemo ? "hidden" : ""}>
          <span>模型列表${models.length > 12 ? `（共 ${models.length} 个，可搜索）` : ""}</span>
          ${models.length > 12 ? `<input class="c-filter" placeholder="搜模型名…">` : ""}
          <div class="c-model-list">
            ${models.map((m) => this.modelRow(m, c.model)).join("")
              || '<div class="c-model-empty">还没有模型，点下面添加，或先填好 Base URL + Key 让它自动拉</div>'}
          </div>
          <div class="c-model-add">
            <input class="c-new-model" placeholder="输入模型名，回车添加">
            <button type="button" class="cfg-ghost c-add">+ 添加</button>
          </div>
        </div>
        ${c.voices ? `<label class="cfg-field"><span>音色</span>
          <select class="c-voice">${c.voices.map((v) =>
            `<option value="${v.id}"${v.id === c.voice ? " selected" : ""}>${v.label}</option>`).join("")}
          </select></label>` : ""}
        <small class="cfg-policy">${typeRow.note || (c.vendors || []).find(
          (v) => v.id === c.vendor)?.note || ""}</small>
      </section>`;

    // 厂商：换厂商时把 base / 模型表换成该厂商的默认
    this.on(mount, ".c-vendor", "change", (el) => {
      const v = vendors.find((x) => x.id === el.value) || {};
      c.vendor = el.value;
      if (!c.keep_base) c.base_url = v.default_base || "";
      if (v.default_models && v.default_models.length) {
        c.models = v.default_models.slice();
        c.model = c.models[0].id;
      }
      if (c.types && v.id === "demo") c.type = "demo";
      this.renderChannel(name, mount, title, hint);
    });
    // API 格式
    this.on(mount, ".c-type", "change", (el) => {
      c.type = el.value;
      const t = (c.types || []).find((x) => x.id === el.value) || {};
      if (!c.base_url && t.default_base) c.base_url = t.default_base;
      this.renderChannel(name, mount, title, hint);
    });
    this.on(mount, ".c-base-input", "input", (el) => { c.base_url = el.value.trim(); });
    this.on(mount, ".c-key-input", "input", (el) => {
      c._keyTouched = true;
      c._key = el.value;
    });
    this.on(mount, ".c-voice", "change", (el) => { c.voice = el.value; });

    // 模型：选中 / 改名 / 删除 / 添加
    mount.querySelectorAll(".c-model").forEach((row) => {
      const id = row.dataset.id;
      const pick = row.querySelector('input[type="radio"]');
      pick.addEventListener("change", async () => {
        c.model = id;
        mount.querySelectorAll(".c-model").forEach((r) =>
          r.classList.toggle("on", r.dataset.id === id));
        // 音色跟着模型走：换模型后要重新问服务端这个模型的音色表
        if (name === "tts") await this.refreshVoices(name, mount, title, hint);
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
      const val = (input.value || "").trim();
      if (!val) return;
      if (c.models.some((m) => m.id === val)) { this.setStatus("已经有这个模型了"); return; }
      c.models.push({ id: val, label: val });
      c.model = c.model || val;
      input.value = "";
      this.renderChannel(name, mount, title, hint);
    };
    mount.querySelector(".c-add").addEventListener("click", add);
    mount.querySelector(".c-new-model").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); add(); }
    });
  },

  /** 换模型后重新取该模型的音色表（音色写在模型上，不是厂商上） */
  async refreshVoices(name, mount, title, hint) {
    const c = this.edit(name);
    try {
      const d = await api("/api/settings");
      const fresh = d[name] || {};
      c.voices = fresh.voices || [];
      c.voice = fresh.voice || (c.voices[0] && c.voices[0].id) || "";
      this.chan[name] = c;
      this.renderChannel(name, mount, title, hint);
    } catch (_) { /* 取不到就保持原样 */ }
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
    this.renderAgents();
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
        ? "⚠ 当前是「全双工」模式，这里的 LLM / TTS / ASR 不会生效。点顶部「半双工」切换。"
        : "LLM 出字 → TTS 念出来 → ASR 收你的话。三件各自独立，可以分别换。";
      lead.classList.toggle("cfg-lead--warn", onFull);
    }
    const flead = $("#cfg-full-lead");
    if (flead) {
      flead.textContent = onFull
        ? "音频进、音频出，随时能听、随时打断。"
        : "⚠ 当前是「半双工」模式，全双工不会生效。点顶部「全双工」切换。";
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

  renderAgents() {
    const a = this.data.agents || {};
    $("#cfg-agents-on").checked = !!a.enabled;
    $("#cfg-desktop-on").checked = !!a.desktop;
    const clis = a.cli || [];
    const usable = clis.filter((c) => c.installed && c.enabled);
    $("#cfg-agents-state").textContent = a.enabled ? `${usable.length} 个可用` : "已停用";

    $("#cfg-agent-modes").innerHTML = [
      { id: "auto", label: "自动", desc: "按任务类型挑一个" },
      { id: "manual", label: "只用一个", desc: "全用指定的 CLI" },
    ].map((m) => `
      <label class="cfg-seg-item ${(a.mode || "auto") === m.id ? "on" : ""}" data-id="${m.id}">
        <input type="radio" name="agent-mode" value="${m.id}" ${(a.mode || "auto") === m.id ? "checked" : ""}>
        <b>${m.label}</b><small>${m.desc}</small>
      </label>`).join("");
    $("#cfg-agent-modes").querySelectorAll("input").forEach((i) => {
      i.addEventListener("change", () => {
        this.data.agents.mode = i.value;
        $("#cfg-agent-modes").querySelectorAll(".cfg-seg-item").forEach((x) =>
          x.classList.toggle("on", x.dataset.id === i.value));
        $("#cfg-agent-active-row").hidden = i.value !== "manual";
      });
    });
    this.fillSelect($("#cfg-agent-active"),
      usable.map((c) => ({ id: c.id, label: c.label })),
      a.active || (usable[0] || {}).id || "");
    $("#cfg-agent-active-row").hidden = (a.mode || "auto") !== "manual";

    $("#cfg-clis").innerHTML = clis.map((c) => {
      const models = c.models || [];
      const opts = `<option value=""${c.model ? "" : " selected"}>用 CLI 自己的默认${
        c.auto_model ? "（" + c.auto_model + "）" : ""}</option>`
        + models.map((m) => `<option value="${m.id}"${m.id === c.model ? " selected" : ""}>${
          m.label || m.id}</option>`).join("");
      const meta = c.installed ? `${c.version || "已安装"} · ${models.length} 个模型`
                               : (c.reason || "未安装");
      return `
      <div class="cfg-cli ${c.installed ? "" : "off"}" data-id="${c.id}">
        <label class="cfg-switch">
          <input type="checkbox" class="cli-on" ${c.enabled ? "checked" : ""}
                 ${c.installed ? "" : "disabled"}>
          <span>${c.label}</span><small>${meta}</small>
        </label>
        <label class="cfg-field cfg-field--sub"><span>模型</span>
          <select class="cli-model" ${models.length ? "" : "disabled"}>${opts}</select></label>
        ${c.note ? `<small class="cfg-policy">${c.note}</small>` : ""}
      </div>`;
    }).join("");
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
      const patch = { vendor: c.vendor, base_url: c.base_url, model: c.model,
                      models: c.models || [] };
      if (c.types) patch.type = c.type;
      if (c.voice) patch.voice = c.voice;
      if (c._keyTouched) patch.api_key = c._key || "";
      out[name] = patch;
    }
    const cli = {};
    document.querySelectorAll(".cfg-cli").forEach((row) => {
      cli[row.dataset.id] = {
        enabled: row.querySelector(".cli-on").checked,
        model: (row.querySelector(".cli-model") || {}).value || "",
      };
    });
    out.agents = {
      enabled: $("#cfg-agents-on").checked,
      mode: (document.querySelector('#cfg-agent-modes input:checked') || {}).value || "auto",
      active: $("#cfg-agent-active").value,
      desktop: $("#cfg-desktop-on").checked,
      cli,
    };
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

  async refresh() {
    const btn = $("#cfg-refresh");
    btn.disabled = true;
    this.setStatus("重新探测命令行 agent…");
    try {
      this.data = await fetch("/api/settings?refresh=1").then((r) => r.json());
      this.chan = {};
      this.render();
      const n = (this.data.agents?.cli || []).filter((c) => c.installed).length;
      this.setStatus(`探测完成 · ${n} 个 agent 可用`);
    } catch (e) {
      this.setStatus("探测失败：" + e.message);
    } finally {
      btn.disabled = false;
    }
  },

  applyVoice() {
    const voice = this.player?.base?.voice || window.__voice;
    if (!voice || !voice.conf) return;
    const c = this.data.tts || {};
    if (c.voice) voice.conf.voice = c.voice;
    if (c.vendor) voice.conf.provider = c.vendor;
  },

  broadcast() {
    const d = this.data || {};
    const btn = $("#mic-btn");
    if (!btn) return;
    if (d.mode === "full") {
      btn.disabled = false;
      btn.title = "全双工语音（点一下连实时模型）";
    } else if (d.asr?.enabled) {
      btn.disabled = false;
      btn.title = "语音输入";
    } else {
      btn.disabled = true;
      btn.textContent = "语音";
      btn.title = "半双工没启用语音识别，去「设置 → 对话与语音」选一个，或直接打字";
    }
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
  $("#cfg-refresh").addEventListener("click", () => SettingsPanel.refresh());
  document.querySelectorAll(".cfg-tabs button").forEach((b) => {
    b.addEventListener("click", () => SettingsPanel.showTab(b.dataset.tab));
  });
  document.querySelectorAll("#cfg-modebar .cfg-mode-opt").forEach((el) => {
    const pick = () => {
      SettingsPanel.data.mode = el.dataset.mode;
      SettingsPanel.renderMode();
      SettingsPanel.showTab(el.dataset.mode === "full" ? "full" : "half");
      SettingsPanel.setStatus("已切到" + (el.dataset.mode === "full" ? "全双工" : "半双工")
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
