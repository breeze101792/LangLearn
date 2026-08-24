// Settings page.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { renderLangSwitcher } from "../components/lang-switcher.js";
import { renderTransfer } from "./transfer.js";

let activeSection = "general";
let dirty = {};
let ttsProviders = [];  // populated from /api/tts/providers

async function loadTtsProviders() {
  const res = await api.get("/api/tts/providers");
  if (res && res.ok && res.data) {
    ttsProviders = res.data.providers || [];
  } else {
    ttsProviders = [];
  }
}

export function renderSettings(host) {
  const state = store.get();
  const settings = state.settings || {};
  const languages = state.languages || [];
  const selectableLanguages = languages.filter((l) => l.seeded || l.code === settings.active_language);
  // Fetch TTS provider metadata once when the page mounts. We don't block
  // initial render on it; the section will repopulate as soon as it lands.
  loadTtsProviders().then(() => {
    const main = host.querySelector("#settings-main");
    if (main) renderMain();
  });

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Settings</h1>
    </header>
    <section class="settings">
      <nav class="settings__nav" aria-label="Settings sections">
        <button class="settings__nav-item settings__nav-item--active" data-section="general">General</button>
        <button class="settings__nav-item" data-section="levels">Language levels</button>
        <button class="settings__nav-item" data-section="dict-chain">Dictionary chain</button>
        <button class="settings__nav-item" data-section="dictionaries">Offline dictionaries</button>
        <button class="settings__nav-item" data-section="init">Initialize data</button>
        <button class="settings__nav-item" data-section="backup">Backup &amp; restore</button>
      </nav>
      <div id="settings-main" class="settings__main"></div>
    </section>
    <div class="settings__actions" id="settings-actions" style="margin-top: var(--sp-4)"></div>
  `;
  host.querySelectorAll(".settings__nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeSection = btn.dataset.section;
      host.querySelectorAll(".settings__nav-item").forEach((b) => b.classList.toggle("settings__nav-item--active", b === btn));
      renderMain();
    });
  });
  renderMain();

  function renderMain() {
    const main = host.querySelector("#settings-main");
    if (activeSection === "general") renderGeneral(main);
    else if (activeSection === "levels") renderLevels(main);
    else if (activeSection === "dict-chain") renderDictChain(main);
    else if (activeSection === "dictionaries") renderDictionaries(main);
    else if (activeSection === "backup") renderTransfer(main);
    else renderInit(main);
    renderActions();
  }

  function renderActions() {
    // The backup section owns its own actions; skip the global save bar.
    if (activeSection === "backup") {
      const bar = host.querySelector("#settings-actions");
      if (bar) bar.innerHTML = "";
      return;
    }
    const bar = host.querySelector("#settings-actions");
    const isDirty = Object.keys(dirty).length > 0;
    if (!isDirty) {
      bar.innerHTML = `<span class="field__hint">All changes saved.</span>`;
      return;
    }
    bar.innerHTML = `
      <button id="save-settings" class="btn btn--primary">Save changes</button>
      <button id="revert-settings" class="btn btn--ghost">Revert</button>
    `;
    bar.querySelector("#save-settings").addEventListener("click", saveAll);
    bar.querySelector("#revert-settings").addEventListener("click", () => {
      dirty = {};
      // Restore the committed active language in case the top bar was
      // live-updated while the picker was dirty.
      store.set({ activeLanguage: settings.active_language });
      renderLangSwitcher();
      renderSettings(host);
    });
  }

  async function saveAll() {
    const res = await api.put("/api/settings", dirty);
    if (!res.ok) {
      toast({ title: "Couldn't save", message: res.error, variant: "error" });
      return;
    }
    store.set({ settings: res.data, activeLanguage: res.data.active_language });
    renderLangSwitcher();
    dirty = {};
    renderActions();
    toast({ title: "Settings saved", variant: "success", ttl: 2500 });
  }

  function renderGeneral(main) {
    const s = settings;
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">General</h2>
        <div class="card">
          <div class="settings__row">
            <div class="settings__row__label">Theme</div>
            <div class="segmented" role="radiogroup" aria-label="Theme">
              ${["auto","light","dark"].map((t) => `
                <button class="segmented__item ${s.theme === t ? "segmented__item--active" : ""}" data-theme="${t}" role="radio" aria-checked="${s.theme === t}">${t[0].toUpperCase() + t.slice(1)}</button>
              `).join("")}
            </div>
          </div>
          <div class="settings__row">
            <div class="settings__row__label">Review session size</div>
            <input id="r-review-size" type="number" min="5" max="50" step="5" class="input" style="max-width: 120px" value="${s.review_session_size || 30}">
          </div>
          <div class="settings__row">
            <div class="settings__row__label">Page size (vocabulary / structures / phrases)</div>
            <input id="r-size" type="number" min="5" max="50" step="5" class="input" style="max-width: 120px" value="${s.page_size || 20}">
          </div>
          <div class="settings__row">
            <div class="settings__row__label">Auto-add looked-up words to vocab</div>
            <label class="toggle">
              <input id="auto-add" type="checkbox" class="toggle__input" ${s.auto_add_vocab ? "checked" : ""}>
              <span class="toggle__track"><span class="toggle__thumb"></span></span>
            </label>
          </div>
          <div class="settings__row">
            <div class="settings__row__label">Show readings on review cards (CJK)</div>
            <label class="toggle">
              <input id="show-readings" type="checkbox" class="toggle__input" ${s.show_readings ? "checked" : ""}>
              <span class="toggle__track"><span class="toggle__thumb"></span></span>
            </label>
          </div>
        </div>

        <h2 class="card__title" style="margin-top: var(--sp-6)">Native languages</h2>
        <div class="card">
          <div class="settings__row">
            <div class="settings__row__label">Primary explanation language</div>
            <select id="primary-lang" class="select" style="max-width: 240px">
              ${languages.map((l) => `<option value="${l.code}" ${l.code === s.explanation_primary ? "selected" : ""}>${l.display_name} (${l.code})</option>`).join("")}
            </select>
          </div>
          <div class="settings__row">
            <div class="settings__row__label">Secondary explanation language (optional)</div>
            <select id="secondary-lang" class="select" style="max-width: 240px">
              <option value="">(none)</option>
              ${languages.map((l) => `<option value="${l.code}" ${l.code === s.explanation_secondary ? "selected" : ""}>${l.display_name} (${l.code})</option>`).join("")}
            </select>
          </div>
        </div>

        <h2 class="card__title" style="margin-top: var(--sp-6)">Active learning language</h2>
        <div class="card">
          <div class="settings__row">
            <div class="settings__row__label">Active language</div>
            <select id="active-lang" class="select" style="max-width: 240px">
              ${selectableLanguages.map((l) => `<option value="${l.code}" ${l.code === s.active_language ? "selected" : ""}>${escapeHtml(l.display_name)} (${l.code})</option>`).join("")}
            </select>
          </div>
        </div>

        <h2 class="card__title" style="margin-top: var(--sp-6)">Pronunciation</h2>
        <div class="card">
          <div class="settings__row">
            <div class="settings__row__label">TTS provider</div>
            ${ttsProviders.length
              ? `<select id="tts-provider" class="select" style="max-width: 280px" title="Backend text-to-speech module. The speaker button on word cards uses this provider.">
                  ${ttsProviders.map((p) => `<option value="${escapeHtml(p.name)}" ${s.tts_provider === p.name ? "selected" : ""}>${escapeHtml(p.display_name || p.name)}</option>`).join("")}
                </select>`
              : `<span class="field__hint">No TTS providers available.</span>`}
          </div>
          <p class="field__hint" style="margin: 0">The speaker button on every word card uses this provider. Audio is cached so repeat lookups don't re-hit the network.</p>
        </div>
      </div>
    `;
    main.querySelectorAll("[data-theme]").forEach((btn) => {
      btn.addEventListener("click", () => {
        main.querySelectorAll("[data-theme]").forEach((b) => {
          b.classList.toggle("segmented__item--active", b === btn);
          b.setAttribute("aria-checked", b === btn ? "true" : "false");
        });
        dirty.theme = btn.dataset.theme;
        applyTheme(btn.dataset.theme);
        renderActions();
      });
    });
    main.querySelector("#r-review-size").addEventListener("change", (e) => {
      const v = parseInt(e.target.value, 10);
      if (v >= 5 && v <= 50) { dirty.review_session_size = v; renderActions(); }
    });
    main.querySelector("#r-size").addEventListener("change", (e) => {
      const v = parseInt(e.target.value, 10);
      if (v >= 5 && v <= 50) { dirty.page_size = v; renderActions(); }
    });
    main.querySelector("#auto-add").addEventListener("change", (e) => {
      dirty.auto_add_vocab = e.target.checked;
      renderActions();
    });
    main.querySelector("#show-readings").addEventListener("change", (e) => {
      dirty.show_readings = e.target.checked;
      renderActions();
    });
    main.querySelector("#primary-lang").addEventListener("change", (e) => {
      dirty.explanation_primary = e.target.value;
      renderActions();
    });
    main.querySelector("#secondary-lang").addEventListener("change", (e) => {
      dirty.explanation_secondary = e.target.value || null;
      renderActions();
    });
    main.querySelector("#active-lang").addEventListener("change", (e) => {
      dirty.active_language = e.target.value;
      // Reflect the pending active language in the top bar immediately so
      // the user sees what will apply on save. Reverted on cancel.
      store.set({ activeLanguage: e.target.value });
      renderLangSwitcher();
      renderActions();
    });
    const ttsSelect = main.querySelector("#tts-provider");
    if (ttsSelect) {
      ttsSelect.addEventListener("change", (e) => {
        dirty.tts_provider = e.target.value;
        renderActions();
      });
    }
  }

  async function renderDictionaries(main) {
    // First-render skeleton so the layout doesn't jump when data lands.
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">Offline dictionaries</h2>
        <p class="field__hint">Bundled dictionaries run on this device with no network. WordNet for English is installed automatically; everything else you install here on demand.</p>
        <div class="list" id="dict-list" aria-busy="true">
          <div class="field__hint">Loading…</div>
        </div>
      </div>
    `;
    const res = await api.get("/api/dictionary/catalog");
    const list = main.querySelector("#dict-list");
    list.removeAttribute("aria-busy");
    if (!res || !res.ok) {
      list.innerHTML = `<div class="field__hint">Couldn't load the catalog: ${escapeHtml(res?.error || "unknown")}</div>`;
      return;
    }
    const entries = res.data.entries || [];
    if (!entries.length) {
      list.innerHTML = `<div class="field__hint">No offline dictionaries available yet.</div>`;
      return;
    }
    const langByCode = Object.fromEntries(languages.map((l) => [l.code, l.display_name]));
    list.innerHTML = entries.map((e) => renderDictEntry(e, langByCode)).join("");
    bindDictCatalog(list);
  }

  function renderDictEntry(entry, langByCode) {
    const langLabels = (entry.languages || []).map((c) => langByCode[c] || c);
    const installedLangs = new Set(entry.installed_languages || []);
    const isProtected = entry.auto_install && installedLangs.size > 0;
    // ``data-langs`` carries the covered language codes so the click
    // handler knows which pair to (un)install. Catalog entries today
    // cover exactly one language; future multi-language entries will
    // need a per-language picker.
    const langAttr = (entry.languages || []).join(",");
    return `
      <div class="list-item" data-provider="${escapeHtml(entry.provider)}" data-langs="${escapeHtml(langAttr)}">
        <div class="row">
          <div class="list-item__main">
            <strong>${escapeHtml(entry.display_name)}</strong>
            <div class="field__hint" style="margin-top: var(--sp-1)">${escapeHtml(entry.description)}</div>
            <div class="field__hint" style="margin-top: var(--sp-1)">
              Languages: ${langLabels.map(escapeHtml).join(", ") || "—"}
              ${entry.size_hint ? ` · ${escapeHtml(entry.size_hint)}` : ""}
              ${entry.source ? ` · ${escapeHtml(entry.source)}` : ""}
            </div>
          </div>
          <div class="list-item__badges">
            ${isProtected ? `<span class="badge badge--builtin">Always on</span>` : ""}
            ${installedLangs.size > 0 ? `<span class="badge badge--ok">Installed</span>` : `<span class="badge badge--muted">Not installed</span>`}
          </div>
          <div class="spacer"></div>
          ${installedLangs.size > 0
            ? (isProtected
                ? `<span class="field__hint">Default for ${langLabels.map(escapeHtml).join(", ")}</span>`
                : `<button class="btn btn--ghost" data-action="uninstall">Uninstall</button>`)
            : `<button class="btn btn--primary" data-action="install">Install</button>`}
        </div>
      </div>
    `;
  }

  function bindDictCatalog(host) {
    host.querySelectorAll("[data-action='install']").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const item = btn.closest("[data-provider]");
        const provider = item.dataset.provider;
        const langs = (item.dataset.langs || "").split(",").filter(Boolean);
        const language = langs[0] || "en";
        btn.disabled = true;
        const orig = btn.textContent;
        btn.innerHTML = `<span class="spinner spinner--sm" aria-hidden="true"></span> Installing…`;
        try {
          const res = await api.post("/api/dictionary/install", { provider, language });
          if (!res.ok) {
            toast({ title: "Install failed", message: res.error, variant: "error" });
            return;
          }
          toast({ title: `${provider} installed`, message: res.data.already ? "Already installed" : "Ready to use", variant: "success", ttl: 2500 });
          await refreshAfterInstall(host);
        } finally {
          if (document.body.contains(item)) {
            btn.disabled = false;
            btn.textContent = orig;
          }
        }
      });
    });
    host.querySelectorAll("[data-action='uninstall']").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const item = btn.closest("[data-provider]");
        const provider = item.dataset.provider;
        const langs = (item.dataset.langs || "").split(",").filter(Boolean);
        const language = langs[0] || "en";
        if (!confirm(`Uninstall ${provider}? Word lookups for ${language} will fall back to the AI.`)) return;
        btn.disabled = true;
        try {
          const res = await api.post("/api/dictionary/uninstall", { provider, language });
          if (!res.ok) {
            toast({ title: "Uninstall failed", message: res.error, variant: "error" });
            return;
          }
          toast({ title: `${provider} uninstalled`, variant: "success", ttl: 2500 });
          await refreshAfterInstall(host);
        } finally {
          if (document.body.contains(btn)) btn.disabled = false;
        }
      });
    });
  }

  async function refreshAfterInstall(list) {
    if (!list) return;
    const res = await api.get("/api/dictionary/catalog");
    if (!res || !res.ok) return;
    const langByCode = Object.fromEntries(languages.map((l) => [l.code, l.display_name]));
    list.innerHTML = (res.data.entries || []).map((e) => renderDictEntry(e, langByCode)).join("");
    bindDictCatalog(list);
    // Refresh store so other views see the new install state when they
    // next read providers / chain settings.
    const sRes = await api.get("/api/settings");
    if (sRes && sRes.ok) store.set({ settings: sRes.data });
  }

  function renderLevels(main) {
    const levels = settings.language_levels_json || {};
    const CEFR = ["A1", "A2", "B1", "B2", "C1", "C2"];
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">Language levels</h2>
        <p class="field__hint">Set your CEFR proficiency level for each language you're learning. The AI uses this to pick vocabulary and grammar suited to your level. Unset means the AI is told nothing and behaves as before.</p>
        <div class="card">
          ${languages.map((l) => {
            const current = levels[l.code] || "";
            return `
              <div class="settings__row">
                <div class="settings__row__label">${escapeHtml(l.display_name)} <span class="field__hint">(${escapeHtml(l.code)})</span></div>
                <select class="select" data-level-lang="${escapeHtml(l.code)}" style="max-width: 240px">
                  <option value="">Unset</option>
                  ${CEFR.map((c) => `<option value="${c}" ${c === current ? "selected" : ""}>${c}</option>`).join("")}
                </select>
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `;
    main.querySelectorAll("select[data-level-lang]").forEach((sel) => {
      sel.addEventListener("change", (e) => {
        const lang = sel.dataset.levelLang;
        const value = e.target.value || null;
        const next = { ...(settings.language_levels_json || {}) };
        if (value) {
          next[lang] = value;
        } else {
          delete next[lang];
        }
        dirty.language_levels_json = next;
        settings.language_levels_json = next;
        renderActions();
      });
    });
  }

  function renderDictChain(main) {
    const chain = settings.dict_chain_json || {};
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">Dictionary chain</h2>
        <p class="field__hint">For each language, providers are tried top-to-bottom. The first that returns a result wins.</p>
        ${selectableLanguages.map((l) => {
          const entries = chain[l.code] || [];
          return `
            <div class="chain__lang" data-lang="${escapeHtml(l.code)}">
              <div class="chain__lang__head">
                <strong>${escapeHtml(l.display_name)}</strong>
                <span class="field__hint">${entries.length} provider${entries.length === 1 ? "" : "s"}</span>
              </div>
              <div class="chain__rows" data-role="rows">
                ${entries.map((e, idx) => renderChainRow(l.code, e, idx, entries.length)).join("")}
              </div>
              ${renderAddProvider(l.code, entries)}
            </div>
          `;
        }).join("")}
      </div>
    `;
    bindDictChain(main);
  }

  function renderChainRow(lang, entry, idx, total) {
    const ai = entry.name === "llm";
    const builtin = entry.name === "wordnet";
    const chipCls = `chain__chip ${ai ? "chain__chip--ai" : ""} ${builtin ? "chain__chip--builtin" : ""}`.trim();
    // LLM is always pinned at the end of the chain (server invariant). The
    // user can't reorder, toggle, or remove it from this UI.
    const pinned = ai;
    const upDisabled = pinned ? true : idx === 0;
    const downDisabled = pinned ? true : idx === total - 1;
    return `
      <div class="chain__row${pinned ? " chain__row--pinned" : ""}" data-idx="${idx}" data-name="${escapeHtml(entry.name)}">
        <span class="chain__handle" title="Reorder">�</span>
        <span class="${chipCls}">${escapeHtml(entry.name)}</span>
        <span class="field__hint chain__row__kind">${escapeHtml(providerKindLabel(entry.name))}</span>
        ${pinned ? `<span class="field__hint chain__row__pinned-tag">always on</span>` : ""}
        <div class="spacer"></div>
        <button class="btn btn--sm btn--ghost" data-action="up" ${upDisabled ? "disabled" : ""} aria-label="Move up">↑</button>
        <button class="btn btn--sm btn--ghost" data-action="down" ${downDisabled ? "disabled" : ""} aria-label="Move down">↓</button>
        <label class="toggle" title="${pinned ? "LLM is always on as a fallback" : "Enabled"}">
          <input type="checkbox" class="toggle__input" data-action="toggle" ${entry.enabled ? "checked" : ""} ${pinned ? "disabled" : ""}>
          <span class="toggle__track"><span class="toggle__thumb"></span></span>
        </label>
        <button class="btn btn--sm btn--ghost" data-action="remove" aria-label="Remove" ${pinned ? "disabled" : ""}>✕</button>
      </div>
    `;
  }

  function renderAddProvider(lang, entries) {
    const available = ["wordnet", "llm"].filter((n) => !entries.some((e) => e.name === n));
    if (!available.length) {
      return `<p class="field__hint" style="margin-top: var(--sp-2)">All providers already in chain.</p>`;
    }
    return `
      <div class="chain__add">
        <select class="select" data-role="add-select" style="max-width: 200px">
          ${available.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(providerDisplayName(n))}</option>`).join("")}
        </select>
        <button class="btn btn--sm btn--ghost" data-action="add">Add provider</button>
      </div>
    `;
  }

  function providerDisplayName(name) {
    if (name === "llm") return "AI";
    if (name === "wordnet") return "WordNet";
    return name;
  }

  function providerKindLabel(name) {
    if (name === "llm") return "AI (any language)";
    if (name === "wordnet") return "English only";
    return "";
  }

  function bindDictChain(main) {
    main.querySelectorAll(".chain__lang").forEach((langEl) => {
      const lang = langEl.dataset.lang;

      langEl.querySelectorAll(".chain__row").forEach((rowEl) => {
        const idx = parseInt(rowEl.dataset.idx, 10);
        rowEl.querySelectorAll("button[data-action]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const action = btn.dataset.action;
            if (action === "up") moveChainRow(lang, idx, -1);
            else if (action === "down") moveChainRow(lang, idx, 1);
            else if (action === "remove") removeChainRow(lang, idx);
          });
        });
        const toggle = rowEl.querySelector("input[data-action='toggle']");
        if (toggle) {
          toggle.addEventListener("change", () => {
            toggleChainRow(lang, idx, toggle.checked);
          });
        }
      });

      const addBtn = langEl.querySelector("button[data-action='add']");
      if (addBtn) {
        addBtn.addEventListener("click", () => {
          const select = langEl.querySelector("select[data-role='add-select']");
          if (!select) return;
          addChainRow(lang, select.value);
        });
      }
    });
  }

  function getChain(lang) {
    const cur = settings.dict_chain_json || {};
    return Array.isArray(cur[lang]) ? [...cur[lang]] : [];
  }

  function setChainDirty(lang, entries) {
    const cur = settings.dict_chain_json || {};
    const next = { ...cur, [lang]: entries };
    dirty.dict_chain_json = next;
    settings.dict_chain_json = next;
    renderMain();
  }

  function moveChainRow(lang, idx, delta) {
    const entries = getChain(lang);
    const target = idx + delta;
    if (target < 0 || target >= entries.length) return;
    const [item] = entries.splice(idx, 1);
    entries.splice(target, 0, item);
    setChainDirty(lang, entries);
  }

  function removeChainRow(lang, idx) {
    const entries = getChain(lang);
    entries.splice(idx, 1);
    setChainDirty(lang, entries);
  }

  function toggleChainRow(lang, idx, enabled) {
    const entries = getChain(lang);
    if (!entries[idx]) return;
    entries[idx] = { ...entries[idx], enabled };
    setChainDirty(lang, entries);
  }

  function addChainRow(lang, name) {
    const entries = getChain(lang);
    if (entries.some((e) => e.name === name)) return;
    entries.push({ name, enabled: true });
    setChainDirty(lang, entries);
  }

  function renderInit(main) {
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">Initialize data</h2>
        <p class="field__hint">Re-seed replaces existing seeded content. This cannot be undone.</p>
        <div class="list">
          ${languages.map((l) => `
            <div class="list-item" data-lang="${l.code}">
              <div class="row">
                <strong>${escapeHtml(l.display_name)}</strong>
                ${l.seeded
                  ? `<span class="badge badge--ok">Seeded</span>`
                  : `<span class="badge badge--muted">Not seeded</span>`}
                ${l.is_built_in ? `<span class="badge badge--builtin">Built-in</span>` : ""}
                <div class="spacer"></div>
                ${l.seeded
                  ? `<button class="btn btn--ghost" data-action="apply-explanations" title="Translate existing rows into the languages set in 'Explanations' above">Apply explanations</button>`
                  : ""}
                <button class="btn ${l.seeded ? "btn--danger" : "btn--primary"}" data-action="seed">
                  ${l.seeded ? "Re-seed" : "Initialize"}
                </button>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;
    main.querySelectorAll("[data-action='seed']").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const item = btn.closest("[data-lang]");
        const lang = item.dataset.lang;
        const seeded = item.querySelector(".badge--ok");
        if (seeded) {
          if (!confirm("Re-seed replaces existing seeded content. Continue?")) return;
        }
        const badge = item.querySelector(".badge--ok, .badge--muted");
        const origText = btn.innerHTML;
        const origBadge = badge ? badge.innerHTML : "";
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner spinner--sm" aria-hidden="true"></span> Seeding…`;
        item.classList.add("is-seeding");
        if (badge) {
          badge.innerHTML = `<span class="spinner spinner--sm" aria-hidden="true"></span> Seeding…`;
          badge.classList.add("badge--warn");
        }
        try {
          const res = await api.post(`/api/languages/${lang}/initialize`, { force: seeded });
          if (!res.ok) {
            toast({ title: "Seeding failed", message: res.error, variant: "error" });
            return;
          }
          const r = await api.get("/api/languages");
          if (r.ok) store.set({ languages: r.data });
          renderSettings(document.getElementById("app-main"));
          toast({
            title: `${lang} re-initialized`,
            message: `${res.data.structures || 0} structures, ${res.data.phrases || 0} phrases`,
            variant: "success",
          });
        } finally {
          if (document.body.contains(item)) {
            btn.disabled = false;
            btn.innerHTML = origText;
            item.classList.remove("is-seeding");
            if (badge) {
              badge.innerHTML = origBadge;
              badge.classList.remove("badge--warn");
            }
          }
        }
      });
    });
    main.querySelectorAll("[data-action='apply-explanations']").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const lang = btn.closest("[data-lang]").dataset.lang;
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = "Translating…";
        const res = await api.post(`/api/languages/${lang}/apply-explanations`);
        btn.disabled = false;
        btn.textContent = orig;
        if (!res.ok) {
          toast({ title: "Apply failed", message: res.error, variant: "error" });
          return;
        }
        toast({
          title: `${lang} explanations updated`,
          message: `${res.data.structures || 0} structures, ${res.data.phrases || 0} phrases`,
          variant: "success",
        });
      });
    });
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function applyTheme(theme) {
  const html = document.documentElement;
  if (theme === "auto") {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    html.setAttribute("data-theme", mql.matches ? "dark" : "light");
  } else {
    html.setAttribute("data-theme", theme);
  }
  try { localStorage.setItem("langlearn:theme", theme); } catch (e) { /* */ }
}