/* 设置面板：模式（全双工/半双工二选一）、半双工三件套、Agent、密钥。
 *
 * 数据来自 GET /api/settings，保存走 POST /api/settings。普通选项写回 config.json，
 * key 只写 state/secrets.json（不进仓库）；接口只回显掩码，不回传明文。
 */
const SettingsPanel = {
  data: null,
  player: null,
  tab: "mode",

  async open() {
    const box = $("#settings");
    if (!box) return;
    box.hidden = false;
    this.setStatus("读取中…");
    // 等页面初始化完（下拉、播放器都依赖 main() 里的对象）
    for (let i = 0; i < 40 && typeof SettingsPanel.player !== "object"; i++) {
      await new Promise((r) => setTimeout(r, 250));
    }
    try {
      this.data = await api("/api/settings");
    } catch (e) {
      this.setStatus("读取设置失败：" + e.message);
      return;
    }
    this.render();
    this.setStatus("");
    this.showTab(this.data.mode === "full" ? "full" : "half");
  },

  close() {
    const box = $("#settings");
    if (box) box.hidden = true;
    document.querySelectorAll(".cfg-key input").forEach((i) => {
      i.value = "";
      i.dataset.touched = "0";
      i.dataset.clear = "0";
    });
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

  // ------------------------------------------------------------ 渲染
  render() {
    if (!this.data) return;
    this.renderMode();
    this.renderHalf();
    this.renderFull();
    this.renderPersona();
    this.renderAgents();
    this.renderKeys();
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

  /** 单选卡片（模式 / agent 用几个） */
  renderChoices(box, items, value, onPick) {
    box.innerHTML = (items || []).map((m) => `
      <label class="cfg-choice ${m.id === value ? "on" : ""}" data-id="${m.id}">
        <input type="radio" name="${box.id}" value="${m.id}" ${m.id === value ? "checked" : ""}>
        <b>${m.label}</b>
        <small>${m.desc || ""}</small>
      </label>`).join("");
    box.querySelectorAll("input").forEach((i) => {
      i.addEventListener("change", () => {
        box.querySelectorAll(".cfg-choice").forEach((c) => {
          c.classList.toggle("on", c.dataset.id === i.value);
        });
        onPick(i.value);
      });
    });
  },

  renderMode() {
    const d = this.data || {};
    const bar = $("#cfg-modebar");
    if (!bar) return;
    bar.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("on", b.dataset.mode === d.mode);
    });
    // 非当前模式的页面明确标出来（免得以为改了就生效）
    const onFull = d.mode === "full";
    $("#cfg-half-lead").textContent = onFull
      ? "⚠ 当前是「全双工」模式，这一页的 LLM / ASR 不会生效；点顶部「半双工」切换过去才会用。"
      : "LLM 出字 → TTS 念出来 → ASR 收你的话。三件各自独立，可以分别换。";
    $("#cfg-half-lead").classList.toggle("cfg-lead--warn", onFull);
    $("#cfg-full-lead").textContent = onFull
      ? "音频进、音频出，随时能听、随时打断。目前只有 Qwen 一家。"
      : "⚠ 当前是「半双工」模式，全双工不会生效；点顶部「全双工」切换过去才会用。";
    $("#cfg-full-lead").classList.toggle("cfg-lead--warn", !onFull);
  },

  renderHalf() {
    const d = this.data;
    const llm = d.llm || {}, tts = d.tts || {}, asr = d.asr || {};

    // LLM：接口类型 / 预设 / base / model / key 变量
    this.fillSelect($("#cfg-llm-type"), llm.types, llm.type);
    this.fillSelect($("#cfg-llm-preset"),
      (llm.presets || []).map((p) => ({ id: p.id, label: `${p.label} · ${p.type}` })),
      llm.preset);
    const typeRow = (llm.types || []).find((t) => t.id === llm.type) || {};
    const needsBase = !!(typeRow.default_base || llm.type === "completions"
                        || llm.type === "responses");
    $("#cfg-llm-base").value = llm.base_url || "";
    $("#cfg-llm-base").closest(".cfg-row").hidden = !needsBase;
    $("#cfg-llm-model").value = llm.model || "";
    $("#cfg-llm-model").closest(".cfg-row").hidden = llm.type === "demo";
    $("#cfg-llm-keyenv").value = llm.api_key_env || "";
    $("#cfg-llm-keyenv").closest(".cfg-row").hidden = llm.type === "demo";
    const preset = (llm.presets || []).find((p) => p.id === llm.preset) || {};
    $("#cfg-llm-models").innerHTML = (preset.models || [])
      .map((m) => `<option value="${m}">`).join("");
    $("#cfg-llm-note").textContent = [preset.note || "",
      llm.type === "demo" ? "" : (llm.available ? "" : "缺少对应 key")].filter(Boolean).join(" · ");

    // TTS
    this.fillSelect($("#cfg-tts-provider"),
      (tts.providers || []).map((x) => ({ id: x.id, label: x.label + (x.available ? "" : "（缺 key）") })),
      tts.provider);
    const tp = (tts.providers || []).find((x) => x.id === tts.provider) || {};
    this.fillSelect($("#cfg-tts-voice"), tp.voices || tts.voices, tts.voice);
    $("#cfg-tts-base-row").hidden = !tp.base_url_field;
    $("#cfg-tts-base").value = tts.base_url || "";

    // ASR
    this.fillSelect($("#cfg-asr-provider"),
      (asr.providers || []).map((x) => ({ id: x.id, label: x.label + (x.available ? "" : "（缺 key）") })),
      asr.provider);
    const ap = (asr.providers || []).find((x) => x.id === asr.provider) || {};
    const models = ap.models || [];
    $("#cfg-asr-model-row").hidden = models.length === 0;
    this.fillSelect($("#cfg-asr-model"), models, asr.model);
    $("#cfg-asr-base-row").hidden = !ap.base_url_field;
    $("#cfg-asr-base").value = asr.base_url || "";
    $("#cfg-asr-note").textContent = asr.provider === "none"
      ? "麦克风按钮会被禁用，只能在控制台打字。" : (ap.note || "");
  },

  renderFull() {
    const f = this.data.full || {};
    const p = (f.providers || []).find((x) => x.id === f.provider) || {};
    this.fillSelect($("#cfg-full-provider"),
      (f.providers || []).map((x) => ({ id: x.id, label: x.label + (x.available ? "" : "（缺 key）") })),
      f.provider);
    this.fillSelect($("#cfg-full-model"), (p.models || [f.model]), f.model);
    this.fillSelect($("#cfg-full-voice"), f.voices, f.voice);
    $("#cfg-full-note").textContent = [
      p.note || "", `需要 ${f.key_env}`,
      f.ready ? "" : "（还没填，去「密钥」页填）",
    ].filter(Boolean).join(" · ");
  },

  renderAgents() {
    const a = this.data.agents || {};
    $("#cfg-agents-on").checked = !!a.enabled;
    $("#cfg-desktop-on").checked = !!a.desktop;
    const clis = a.cli || [];
    const usable = clis.filter((c) => c.installed && c.enabled);
    $("#cfg-agents-state").textContent = a.enabled ? `${usable.length} 个可用` : "已停用";

    // auto = 按任务类型挑；manual = 只用指定的一个
    this.renderChoices($("#cfg-agent-modes"), [
      { id: "auto", label: "自动", desc: "按任务类型挑一个后端" },
      { id: "manual", label: "只用一个", desc: "所有任务都用指定的 CLI" },
    ], a.mode || "auto", (v) => {
      this.data.agents.mode = v;
      $("#cfg-agent-active-row").hidden = v !== "manual";
    });
    this.fillSelect($("#cfg-agent-active"),
      usable.map((c) => ({ id: c.id, label: c.label })), a.active || (usable[0] || {}).id || "");
    $("#cfg-agent-active-row").hidden = (a.mode || "auto") !== "manual";

    $("#cfg-clis").innerHTML = clis.map((c) => {
      const models = c.models || [];
      const auto = `<option value=""${c.model ? "" : " selected"}>用 CLI 自己的默认${
        c.auto_model ? "（" + c.auto_model + "）" : ""}</option>`;
      const opts = auto + models.map((m) => {
        const id = m.id ?? m, label = m.label ?? m;
        return `<option value="${id}"${id === c.model ? " selected" : ""}>${label}</option>`;
      }).join("");
      const meta = c.installed
        ? `${c.version || "已安装"} · ${models.length} 个模型`
        : (c.reason || "未安装");
      return `
      <div class="cfg-cli ${c.installed ? "" : "off"}" data-id="${c.id}">
        <label class="cfg-switch">
          <input type="checkbox" class="cli-on" ${c.enabled ? "checked" : ""}
                 ${c.installed ? "" : "disabled"}>
          <span>${c.label}</span>
          <small>${meta}</small>
        </label>
        <label class="cfg-row cfg-row--sub">
          <span>模型</span>
          <select class="cli-model" ${models.length ? "" : "disabled"}>${opts}</select>
        </label>
        ${c.note ? `<small class="cfg-note">${c.note}</small>` : ""}
      </div>`;
    }).join("");
  },

  renderKeys() {
    const keys = this.data.keys || [];
    $("#cfg-keys").innerHTML = keys.map((k) => `
      <div class="cfg-key ${k.set ? "ok" : ""}" data-env="${k.env}">
        <i></i>
        <label><b>${k.env}</b><small>${k.label}${k.set ? " · 已设置 " + k.hint : ""}</small></label>
        <input type="password" autocomplete="off" spellcheck="false"
               placeholder="${k.set ? "已设置，留空不改" : "未设置"}" data-env="${k.env}">
        ${k.set ? '<button type="button" class="cfg-clear" title="删掉这个 key">清除</button>' : ""}
      </div>`).join("");
    $("#cfg-keys").querySelectorAll("input").forEach((i) => {
      i.addEventListener("input", () => { i.dataset.touched = "1"; i.dataset.clear = "0"; });
    });
    $("#cfg-keys").querySelectorAll(".cfg-clear").forEach((b) => {
      b.addEventListener("click", () => {
        const wrap = b.closest(".cfg-key");
        const input = wrap.querySelector("input");
        input.value = "";
        input.dataset.touched = "1";
        input.dataset.clear = "1";
        wrap.classList.remove("ok");
        b.remove();
        this.setStatus("保存后清除 " + wrap.dataset.env);
      });
    });
  },

  fillSelect(sel, items, value) {
    const list = items || [];
    if (!list.length) {
      sel.innerHTML = '<option value="">（不需要选）</option>';
      sel.value = "";
      return;
    }
    const has = list.some((i) => (i.id ?? i) === value);
    sel.innerHTML = list
      .map((i) => `<option value="${i.id ?? i}">${i.label ?? i}</option>`)
      .join("") + (value && !has ? `<option value="${value}">${value}（当前）</option>` : "");
    sel.value = value ?? (list[0] && (list[0].id ?? list[0])) ?? "";
  },

  // ------------------------------------------------------------ 联动
  onLLMType() {
    const llm = this.data.llm || {};
    const type = $("#cfg-llm-type").value;
    const row = (llm.types || []).find((t) => t.id === type) || {};
    const demo = type === "demo";
    const needsBase = !demo && !!(row.default_base || type === "completions" || type === "responses");
    $("#cfg-llm-base").closest(".cfg-row").hidden = !needsBase;
    $("#cfg-llm-model").closest(".cfg-row").hidden = demo;
    $("#cfg-llm-keyenv").closest(".cfg-row").hidden = demo;
    if (!$("#cfg-llm-base").value && needsBase) $("#cfg-llm-base").value = row.default_base || "";
    $("#cfg-llm-note").textContent = row.note || "";
  },

  /** 选预设：把 base_url / model / key 变量一次性填好，用户还能再改 */
  onLLMPreset() {
    const llm = this.data.llm || {};
    const p = (llm.presets || []).find((x) => x.id === $("#cfg-llm-preset").value) || {};
    if (p.type) $("#cfg-llm-type").value = p.type;
    this.onLLMType();
    $("#cfg-llm-base").value = p.base_url || "";
    // demo 这类没有模型表的预设，模型字段要清空，别留着上一个预设的值
    $("#cfg-llm-model").value = (p.models || []).length ? (p.model || "") : "";
    $("#cfg-llm-keyenv").value = p.api_key_env || "";
    $("#cfg-llm-models").innerHTML = (p.models || []).map((m) => `<option value="${m}">`).join("");
    $("#cfg-llm-note").textContent = p.note || (p.type === "demo" ? "不联网、不用 key，台词写死。" : "");
  },

  onTTSProvider() {
    const tts = this.data.tts || {};
    const p = (tts.providers || []).find((x) => x.id === $("#cfg-tts-provider").value) || {};
    this.fillSelect($("#cfg-tts-voice"), p.voices || [], ((p.voices || [])[0] || {}).id || "");
    $("#cfg-tts-base-row").hidden = !p.base_url_field;
  },

  onASRProvider() {
    const asr = this.data.asr || {};
    const p = (asr.providers || []).find((x) => x.id === $("#cfg-asr-provider").value) || {};
    const models = p.models || [];
    $("#cfg-asr-model-row").hidden = models.length === 0;
    this.fillSelect($("#cfg-asr-model"), models, models[0] || "");
    $("#cfg-asr-base-row").hidden = !p.base_url_field;
    $("#cfg-asr-note").textContent = $("#cfg-asr-provider").value === "none"
      ? "麦克风按钮会被禁用，只能在控制台打字。" : (p.note || "");
  },

  // ------------------------------------------------------------ 保存
  collect() {
    const keys = {};
    document.querySelectorAll(".cfg-key input").forEach((i) => {
      if (i.dataset.touched === "1" || i.dataset.clear === "1") keys[i.dataset.env] = i.value;
    });
    const cli = {};
    document.querySelectorAll(".cfg-cli").forEach((row) => {
      cli[row.dataset.id] = {
        enabled: row.querySelector(".cli-on").checked,
        model: (row.querySelector(".cli-model") || {}).value || "",
      };
    });
    const mode = this.data.mode || "half";     // 顶部切换条选的
    const agentMode = (document.querySelector('#cfg-agent-modes input:checked') || {}).value || "auto";
    const presetId = $("#cfg-llm-preset").value;
    const p = (this.data.llm.presets || []).find((x) => x.id === presetId) || {};
    // 用户改过预设里的任何一个字段 → 当作自定义，别让预设把值盖回去
    const edited = ($("#cfg-llm-base").value || "") !== (p.base_url || "")
      || $("#cfg-llm-keyenv").value !== (p.api_key_env || "")
      || ((p.models || []).length && $("#cfg-llm-model").value !== (p.model || ""));
    const llm = edited
      ? { preset: "custom", type: $("#cfg-llm-type").value,
          base_url: $("#cfg-llm-base").value, model: $("#cfg-llm-model").value,
          api_key_env: $("#cfg-llm-keyenv").value }
      : { preset: presetId, type: $("#cfg-llm-type").value,
          base_url: $("#cfg-llm-base").value, model: $("#cfg-llm-model").value,
          api_key_env: $("#cfg-llm-keyenv").value };
    const ttsProvider = (this.data.tts.providers || []).find((p) => p.id === $("#cfg-tts-provider").value) || {};
    const asrProvider = (this.data.asr.providers || []).find((p) => p.id === $("#cfg-asr-provider").value) || {};
    return {
      mode,
      full: {
        provider: $("#cfg-full-provider").value,
        model: $("#cfg-full-model").value,
        voice: $("#cfg-full-voice").value,
      },
      llm,
      tts: { provider: $("#cfg-tts-provider").value, voice: $("#cfg-tts-voice").value,
             base_url: ttsProvider.base_url_field ? $("#cfg-tts-base").value : "" },
      asr: { provider: $("#cfg-asr-provider").value, model: $("#cfg-asr-model").value,
             base_url: asrProvider.base_url_field ? $("#cfg-asr-base").value : "" },
      agents: {
        enabled: $("#cfg-agents-on").checked,
        mode: agentMode,
        active: $("#cfg-agent-active").value,
        desktop: $("#cfg-desktop-on").checked,
        cli,
      },
      persona: {
        name: $("#cfg-p-name").value,
        address: $("#cfg-p-address").value,
        identity: $("#cfg-p-identity").value,
        extra: $("#cfg-p-extra").value,
      },
      keys,
    };
  },

  async save() {
    const btn = $("#cfg-save");
    btn.disabled = true;
    this.setStatus("保存中…");
    try {
      const body = this.collect();
      this.data = await api("/api/settings", body);
      this.render();
      if (body.tts) this.applyVoice();
      const changed = this.data.changed || {};
      const parts = Object.entries(changed)
        .filter(([k]) => k !== "keys")
        .map(([k, v]) => `${k}=${v === true ? "开" : v === false ? "关" : v}`);
      if (changed.keys) parts.push("key：" + changed.keys.join(", "));
      const applied = this.data.applied || [];
      this.setStatus(parts.length
        ? "已保存 · " + parts.slice(0, 4).join(" · ") + (parts.length > 4 ? ` 等 ${parts.length} 项` : "")
        : "已保存（无改动）");
      if (applied.length) console.info("[settings] 已生效:", applied);
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
      this.render();
      const n = (this.data.agents?.cli || []).filter((c) => c.installed).length;
      this.setStatus(`探测完成 · ${n} 个 agent 可用`);
    } catch (e) {
      this.setStatus("探测失败：" + e.message);
    } finally {
      btn.disabled = false;
    }
  },

  /** 换了朗读音色/引擎：当前页面的朗读器立刻改用新设置，不用刷新 */
  applyVoice() {
    const voice = this.player?.base?.voice || window.__voice;
    if (!voice || !voice.conf) return;
    voice.conf.voice = $("#cfg-tts-voice").value;
    voice.conf.provider = $("#cfg-tts-provider").value;
  },

  /** 保存后刷新页面上的能力状态（麦克风按钮、模式提示） */
  broadcast() {
    const d = this.data || {};
    const btn = $("#mic-btn");
    const mode = d.mode;
    const asrOn = mode === "half" && d.asr?.enabled;
    if (btn) {
      if (mode === "full") {
        btn.disabled = false;
        btn.title = "全双工语音（点一下连实时模型）";
      } else if (asrOn) {
        btn.disabled = false;
        btn.title = "语音输入";
      } else {
        btn.disabled = true;
        btn.textContent = "语音";
        btn.title = "半双工没启用语音识别，去「设置 → 半双工」选一个，或直接打字";
      }
    }
    // 模式变了要刷新页面才彻底切换（语音按钮的处理器是启动时绑的）
    if (d.applied && d.applied.some((a) => a.startsWith("mode="))) {
      this.setStatus((document.querySelector("#cfg-status").textContent || "") + " · 刷新页面后完全切换");
    }
  },

  /** 用当前页面上选的音色试听一句（不用先保存） */
  async test() {
    const note = $("#cfg-test-note");
    note.textContent = "合成中…";
    try {
      const text = "你好呀，我是鱼丫丫，今天也一起玩吧。";
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: $("#cfg-tts-voice").value,
                               provider: $("#cfg-tts-provider").value }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail || ""; } catch (_) {}
        throw new Error(`HTTP ${res.status}${detail ? " · " + detail : ""}`);
      }
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const buf = await ctx.decodeAudioData(await res.arrayBuffer());
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      src.onended = () => ctx.close();
      src.start();
      note.textContent = `“${text}”`;
    } catch (e) {
      note.textContent = "试听失败：" + e.message;
    }
  },
};

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
      ev.stopImmediatePropagation();     // 别让 Esc 同时触发"打断"
    }
  }, true);
  $("#cfg-save").addEventListener("click", () => SettingsPanel.save());
  $("#cfg-refresh").addEventListener("click", () => SettingsPanel.refresh());
  $("#cfg-test").addEventListener("click", () => SettingsPanel.test());
  document.querySelectorAll(".cfg-tabs button").forEach((b) => {
    b.addEventListener("click", () => SettingsPanel.showTab(b.dataset.tab));
  });
  // 顶部模式切换条：改了立刻切到对应页签，让"当前生效的是哪条"一眼可见
  document.querySelectorAll("#cfg-modebar button").forEach((b) => {
    b.addEventListener("click", () => {
      SettingsPanel.data.mode = b.dataset.mode;
      SettingsPanel.renderMode();
      SettingsPanel.showTab(b.dataset.mode === "full" ? "full" : "half");
      SettingsPanel.setStatus("已切到" + (b.dataset.mode === "full" ? "全双工" : "半双工") + "，记得点保存");
    });
  });
  $("#cfg-llm-type").addEventListener("change", () => SettingsPanel.onLLMType());
  $("#cfg-llm-preset").addEventListener("change", () => SettingsPanel.onLLMPreset());
  $("#cfg-tts-provider").addEventListener("change", () => SettingsPanel.onTTSProvider());
  $("#cfg-asr-provider").addEventListener("change", () => SettingsPanel.onASRProvider());
  $("#cfg-full-provider").addEventListener("change", () => {
    const f = SettingsPanel.data.full || {};
    const p = (f.providers || []).find((x) => x.id === $("#cfg-full-provider").value) || {};
    SettingsPanel.fillSelect($("#cfg-full-model"), p.models || [], (p.models || [])[0] || "");
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
