// Settings page.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { renderLangSwitcher } from "../components/lang-switcher.js";

let activeSection = "general";
let dirty = {};

export function renderSettings(host) {
  const state = store.get();
  const settings = state.settings || {};
  const languages = state.languages || [];

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Settings</h1>
    </header>
    <section class="settings">
      <nav class="settings__nav" aria-label="Settings sections">
        <button class="settings__nav-item settings__nav-item--active" data-section="general">General</button>
        <button class="settings__nav-item" data-section="dict-chain">Dictionary chain</button>
        <button class="settings__nav-item" data-section="init">Initialize data</button>
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
    else if (activeSection === "dict-chain") renderDictChain(main);
    else renderInit(main);
    renderActions();
  }

  function renderActions() {
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
            <div class="settings__row__label">Page size (review session / vocabulary list)</div>
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
              ${languages.map((l) => `<option value="${l.code}" ${l.code === s.active_language ? "selected" : ""}>${l.display_name} (${l.code})</option>`).join("")}
            </select>
          </div>
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
      renderActions();
    });
  }

  function renderDictChain(main) {
    const chain = settings.dict_chain_json || {};
    main.innerHTML = `
      <div class="settings__section">
        <h2 class="card__title">Dictionary chain</h2>
        <p class="field__hint">For each language, providers are tried top-to-bottom. The first that returns a result wins.</p>
        ${languages.map((l) => {
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
        const lang = btn.closest("[data-lang]").dataset.lang;
        const seeded = btn.closest("[data-lang]").querySelector(".badge--ok");
        if (seeded) {
          if (!confirm("Re-seed replaces existing seeded content. Continue?")) return;
        }
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