// Dictionary page: search box + result card with provider switcher.
//
// The switcher mirrors the user's chain order from Settings. LLM is always
// present in every chain, so the user can fall back to AI when WordNet (or
// any other provider) doesn't have the word.

import { api } from "../api.js";
import { cache } from "../cache.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { renderWordCard } from "../components/word-card.js";

// Metadata fetched from /api/dictionary/providers keyed by name.
let providerMeta = {};
let llmStatus = { configured: true, provider_kind: "openai" };
let providerMetaLoaded = false;
let lastLang = null;

const lastLookup = { word: "", lang: "" };

export function renderDictionary(host) {
  const state = store.get();
  const settings = state.settings || {};
  const lang = settings.active_language || "en";

  if (lastLang !== null && lastLang !== lang) {
    // Language switched — drop metadata cache so we re-fetch with new lang.
    providerMeta = {};
    providerMetaLoaded = false;
  }
  lastLang = lang;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Dictionary</h1>
      <p class="page-head__subtitle">Look up a word in your active language.</p>
    </header>
    <section class="card">
      <div class="autocomplete">
        <div class="row" style="gap: var(--sp-2)">
          <label for="dict-search" class="field__label" style="margin: 0; position: absolute; left: -9999px">Word</label>
          <input id="dict-search" class="input" type="text" autocomplete="off"
                 placeholder="Type a word to look up…"
                 aria-label="Dictionary search"
                 role="combobox" aria-autocomplete="list" aria-expanded="false"
                 aria-controls="dict-suggest"
                 style="flex: 1">
          <button id="dict-search-btn" class="btn btn--primary">Look up</button>
        </div>
        <ul id="dict-suggest" class="autocomplete__list" role="listbox"
            hidden aria-label="Word suggestions"></ul>
      </div>
      <p class="field__hint" style="margin-top: var(--sp-2)">Press <kbd>/</kbd> from anywhere to focus the search box. <kbd>←</kbd>/<kbd>→</kbd> to switch provider when a result is shown.</p>
    </section>
    <section id="dict-result" style="margin-top: var(--sp-4)"></section>
  `;

  const input = host.querySelector("#dict-search");
  const btn = host.querySelector("#dict-search-btn");
  const result = host.querySelector("#dict-result");
  const suggestEl = host.querySelector("#dict-suggest");

  btn.addEventListener("click", () => doLookup(canonical(input.value), lang));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      if (moveSuggestSelection(suggestEl, 1)) e.preventDefault();
    } else if (e.key === "ArrowUp") {
      if (moveSuggestSelection(suggestEl, -1)) e.preventDefault();
    } else if (e.key === "Enter") {
      const picked = pickSelectedSuggestion(suggestEl);
      const word = picked ? picked : canonical(input.value);
      if (word) {
        e.preventDefault();
        input.value = word;
        hideSuggest(suggestEl);
        doLookup(word, lang);
      }
    } else if (e.key === "Escape") {
      hideSuggest(suggestEl);
    }
  });
  input.addEventListener("input", () => {
    scheduleSuggest(input.value.trim(), lang, suggestEl);
  });
  input.addEventListener("focus", () => {
    if (input.value.trim()) scheduleSuggest(input.value.trim(), lang, suggestEl);
  });
  input.addEventListener("blur", () => {
    setTimeout(() => hideSuggest(suggestEl), 120);
  });
  suggestEl.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-word]");
    if (!li) return;
    e.preventDefault();
    const word = li.dataset.word;
    input.value = word;
    hideSuggest(suggestEl);
    doLookup(word, lang);
  });

  renderEmptyState(result);

  // Eagerly load provider metadata so the switcher renders instantly when
  // a result lands. Don't block the page on it.
  loadProviderMeta(lang);
}

async function loadProviderMeta(lang) {
  if (providerMetaLoaded) return providerMeta;
  const res = await api.get(`/api/dictionary/providers?lang=${encodeURIComponent(lang)}`);
  if (!res.ok) return providerMeta;
  providerMeta = {};
  for (const p of res.data.providers || []) {
    providerMeta[p.name] = p;
  }
  llmStatus = {
    configured: res.data.llm_configured !== false,
    provider_kind: res.data.llm_provider_kind || "openai",
  };
  providerMetaLoaded = true;
  maybeShowLLMBanner();
  return providerMeta;
}

function maybeShowLLMBanner() {
  if (llmStatus.configured) return;
  const result = document.getElementById("dict-result");
  if (!result) return;
  // Only show the banner when there's no result yet, so it doesn't fight
  // with the empty-state card or pop up over a real entry.
  if (result.querySelector(".card, .empty-state")) return;
  const banner = document.createElement("div");
  banner.className = "card";
  banner.style.borderLeft = "4px solid var(--warning)";
  banner.innerHTML = `
    <strong>AI dictionary isn't configured.</strong>
    <p class="field__hint" style="margin: var(--sp-1) 0 0">
      Set <code>LLM_PROVIDER=${escapeHtml(llmStatus.provider_kind)}</code>
      ${llmStatus.provider_kind === "ollama"
        ? "and start <code>ollama serve</code> with a model pulled."
        : "and <code>OPENAI_API_KEY</code> before launching the app. See the README."}
    </p>
  `;
  result.appendChild(banner);
}

function chainFor(lang) {
  const settings = store.get().settings || {};
  const chain = settings.dict_chain_json || {};
  const entries = chain[lang] || [];
  return entries.filter((e) => e && e.name);
}

// Build the list of switcher items in chain order, enriched with metadata.
// LLM is always included as the AI fallback even if the chain is empty.
function switcherProviders(lang) {
  const meta = providerMeta || {};
  const chain = chainFor(lang);
  const items = [];
  const seen = new Set();
  for (const e of chain) {
    const m = meta[e.name];
    items.push({
      name: e.name,
      display_name: (m && m.display_name) || (e.name === "llm" ? "AI" : e.name),
      description: (m && m.description) || "",
      kind: (m && m.kind) || (e.name === "llm" ? "ai" : "builtin"),
      supports: m ? m.supports !== false : true,
      enabled: e.enabled !== false,
      is_llm: e.name === "llm",
    });
    seen.add(e.name);
  }
  // Defensive: if the chain is somehow empty, surface LLM as a single item
  // so the user can still try AI. The server invariant guarantees LLM is in
  // every chain, but this protects against malformed user state.
  if (!seen.has("llm")) {
    items.push({
      name: "llm",
      display_name: "AI",
      description: "",
      kind: "ai",
      supports: true,
      enabled: true,
      is_llm: true,
    });
  }
  return items;
}

function renderSwitcherInto(card, lang, activeName) {
  const list = switcherProviders(lang);
  // Remove any existing switcher before inserting a fresh one.
  const existing = card.querySelector(".result-provider-switcher");
  if (existing) existing.remove();
  const bar = document.createElement("div");
  bar.className = "result-provider-switcher";
  bar.innerHTML = `
    <span class="result-provider-switcher__label">Source:</span>
    <button class="provider-switcher__nav" type="button" data-action="prev" aria-label="Previous provider" ${list.length < 2 ? "disabled" : ""}>‹</button>
    <div class="segmented" role="radiogroup" aria-label="Dictionary provider"></div>
    <button class="provider-switcher__nav" type="button" data-action="next" aria-label="Next provider" ${list.length < 2 ? "disabled" : ""}>›</button>
  `;
  card.insertBefore(bar, card.firstChild);
  const segments = bar.querySelector(".segmented");
  paintSegments(segments, list, activeName);
  bar.querySelector("[data-action='prev']").addEventListener("click", () => cycleProvider(list, lang, -1));
  bar.querySelector("[data-action='next']").addEventListener("click", () => cycleProvider(list, lang, 1));
}

function paintSegments(segments, list, selectedName) {
  segments.innerHTML = list.map((p) => {
    const active = (selectedName || "") === p.name;
    const ai = p.kind === "ai";
    const disabled = p.enabled === false;
    const cls = ["segmented__item"];
    if (active) cls.push("segmented__item--active");
    if (ai) cls.push("segmented__item--ai");
    if (disabled) cls.push("segmented__item--disabled");
    return `<button type="button" class="${cls.join(" ")}"
              data-provider="${escapeHtml(p.name)}"
              role="radio"
              aria-checked="${active}"
              ${disabled ? "aria-disabled=\"true\"" : ""}
              title="${escapeHtml(p.description || "")}">${escapeHtml(p.display_name || p.name)}</button>`;
  }).join("");
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    el.addEventListener("click", () => {
      const name = el.dataset.provider;
      const p = list.find((x) => x.name === name);
      if (!p || p.enabled === false) return;
      switchToProvider(name, list);
    });
  });
}

function currentSelectedName() {
  const seg = document.querySelector(".result-provider-switcher .segmented");
  if (!seg) return null;
  const active = seg.querySelector(".segmented__item--active");
  return active ? active.dataset.provider : null;
}

function setSelectedName(name) {
  const seg = document.querySelector(".result-provider-switcher .segmented");
  if (!seg) return;
  seg.querySelectorAll(".segmented__item").forEach((el) => {
    const isActive = el.dataset.provider === name;
    el.classList.toggle("segmented__item--active", isActive);
    el.setAttribute("aria-checked", isActive ? "true" : "false");
  });
}

function cycleProvider(list, lang, delta) {
  if (!list.length) return;
  const cur = currentSelectedName() || "";
  let idx = list.findIndex((p) => p.name === cur);
  if (idx === -1) idx = delta > 0 ? -1 : 0;
  let next = (idx + delta + list.length) % list.length;
  const target = list[next];
  if (!target || target.enabled === false) return;
  switchToProvider(target.name, list);
}

function switchToProvider(name, list) {
  const word = lastLookup.word;
  if (!word) return;
  setSelectedName(name);
  doLookup(word, lastLookup.lang, name);
}

let suggestTimer = null;
let suggestToken = 0;
function scheduleSuggest(query, lang, suggestEl) {
  clearTimeout(suggestTimer);
  if (!query || query.length < 1) {
    hideSuggest(suggestEl);
    return;
  }
  suggestTimer = setTimeout(() => fetchSuggest(query, lang, suggestEl), 120);
}

async function fetchSuggest(query, lang, suggestEl) {
  const myToken = ++suggestToken;
  const res = await api.get(
    `/api/dictionary/suggest?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(lang)}&limit=8`
  );
  if (myToken !== suggestToken) return;
  if (!res.ok || !res.data) {
    hideSuggest(suggestEl);
    return;
  }
  renderSuggest(suggestEl, res.data.suggestions || [], query);
}

function renderSuggest(suggestEl, items, query) {
  if (!items.length) {
    hideSuggest(suggestEl);
    return;
  }
  const q = query.toLowerCase();
  suggestEl.innerHTML = items.map((w, i) => {
    const idx = w.indexOf(q);
    let label;
    if (idx === 0) {
      label = `<strong>${escapeHtml(w.slice(0, q.length))}</strong>${escapeHtml(w.slice(q.length))}`;
    } else {
      label = escapeHtml(w);
    }
    return `<li class="autocomplete__item${i === 0 ? " is-active" : ""}" role="option" data-word="${escapeHtml(w)}">${label}</li>`;
  }).join("");
  suggestEl.hidden = false;
  const input = document.getElementById("dict-search");
  if (input) input.setAttribute("aria-expanded", "true");
}

function hideSuggest(suggestEl) {
  suggestEl.hidden = true;
  suggestEl.innerHTML = "";
  const input = document.getElementById("dict-search");
  if (input) input.setAttribute("aria-expanded", "false");
}

function moveSuggestSelection(suggestEl, delta) {
  const items = Array.from(suggestEl.querySelectorAll("li.autocomplete__item"));
  if (!items.length) return false;
  const cur = items.findIndex((el) => el.classList.contains("is-active"));
  let next = cur + delta;
  if (next < 0) next = items.length - 1;
  if (next >= items.length) next = 0;
  items.forEach((el, i) => el.classList.toggle("is-active", i === next));
  items[next].scrollIntoView({ block: "nearest" });
  return true;
}

function pickSelectedSuggestion(suggestEl) {
  const sel = suggestEl.querySelector("li.autocomplete__item.is-active");
  return sel ? sel.dataset.word : null;
}

function renderEmptyState(host) {
  host.innerHTML = `
    <div class="empty-state">
      <div class="empty-state__icon">�</div>
      <div class="empty-state__title">No word looked up yet</div>
      <div class="empty-state__msg">Type a word above to look it up. Looked-up words are added to your vocab list automatically.</div>
    </div>
  `;
}

async function doLookup(word, lang, providerOverride) {
  const resultHost = document.getElementById("dict-result");
  if (!word) {
    resultHost.innerHTML = `<div class="empty-state"><div class="empty-state__msg">Type a word to look up.</div></div>`;
    return;
  }
  resultHost.innerHTML = `
    <div class="card">
      <div class="row" style="gap: var(--sp-3); align-items: center">
        <span class="spinner"></span>
        <span>Looking up "${escapeHtml(word)}"…</span>
      </div>
    </div>
  `;

  // 1) local cache hit. Only honor the cache when no provider is forced —
  // a forced lookup means the user explicitly wants a specific source.
  if (!providerOverride) {
    const cached = cache.get(lang, word);
    if (cached) {
      lastLookup.word = word;
      lastLookup.lang = lang;
      renderEntry(resultHost, cached.entry, cached.source, cached.word, lang);
      maybeShowUndoToast(cached.word, lang);
      return;
    }
  }

  lastLookup.word = word;
  lastLookup.lang = lang;

  // 2) server chain (or override)
  const body = { lang, word };
  if (providerOverride) body.provider = providerOverride;
  const res = await api.post("/api/dictionary/lookup", body);
  if (!res.ok) {
    resultHost.innerHTML = `
      <div class="card" style="border-left: 4px solid var(--danger)">
        <strong>Couldn't look up "${escapeHtml(word)}"</strong>
        <p class="field__hint">${escapeHtml(res.error || "unknown error")}</p>
      </div>`;
    return;
  }
  const data = res.data || {};
  if (!data.entry || !data.entry.senses || data.entry.senses.length === 0) {
    renderNoResult(resultHost, word, lang, data.suggestions || []);
    return;
  }
  renderEntry(resultHost, data.entry, data.source, word, lang);
  if (data.source === "llm" && !providerOverride) {
    cache.set(lang, word, { entry: data.entry, source: data.source, word });
  }
  maybeShowUndoToast(word, lang, data.auto_added);
}

function renderEntry(host, entry, source, word, lang) {
  const settings = store.get().settings || {};
  const html = renderWordCard(entry, {
    source,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
  });
  host.innerHTML = `
    <div class="card">
      ${html}
    </div>
  `;
  const card = host.querySelector(".card");
  renderSwitcherInto(card, lang, source || null);
}

function renderNoResult(host, word, lang, suggestions) {
  const sugHtml = renderSuggestions(suggestions);
  host.innerHTML = `
    <div class="card">
      <p>No senses found for "${escapeHtml(word)}" in this provider.</p>
      <p class="field__hint">Switch to another provider below to try again.</p>
      ${sugHtml}
    </div>
  `;
  bindSuggestionChips(host, lang);
  const card = host.querySelector(".card");
  // Highlight the LLM chip by default since AI has the broadest coverage.
  renderSwitcherInto(card, lang, "llm");
}

function renderSuggestions(suggestions) {
  if (!suggestions || !suggestions.length) return "";
  const items = suggestions.map((w) =>
    `<button type="button" class="chip" data-suggest="${escapeHtml(w)}">${escapeHtml(w)}</button>`
  ).join("");
  return `
    <div class="did-you-mean" style="margin-top: var(--sp-3)">
      <p class="field__hint" style="margin: 0 0 var(--sp-2)">Did you mean:</p>
      <div class="row chip-row">${items}</div>
    </div>
  `;
}

function bindSuggestionChips(host, lang) {
  host.querySelectorAll("button.chip[data-suggest]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const word = btn.dataset.suggest;
      const input = document.getElementById("dict-search");
      if (input) input.value = word;
      doLookup(word, lang);
    });
  });
}

let lastUndoToken = null;
function maybeShowUndoToast(word, lang, autoAdded) {
  if (!autoAdded) return;
  lastUndoToken = word;
  toast({
    title: `Added "${word}" to vocab`,
    message: "Source: dictionary lookup",
    variant: "success",
    actions: [{
      label: "Undo",
      onClick: () => {
        toast({ title: `Removed "${word}"`, message: "Restore it from the vocab list", variant: "info", ttl: 2000 });
      },
    }],
  });
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Normalize a user-typed word the same way the backend does: trim, collapse
// whitespace, and join tokens with `_` so phrases like "snap at" hit the
// same entry as "snap_at". Hyphens/apostrophes/dots pass through.
function canonical(raw) {
  if (!raw) return "";
  const parts = String(raw).trim().split(/\s+/).filter(Boolean);
  return parts.join("_");
}

// Global / shortcut + arrow-key cycling of the result-area switcher when the
// search input has no suggestion dropdown open.
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !isTyping(e.target)) {
    e.preventDefault();
    const input = document.getElementById("dict-search");
    if (input) input.focus();
    return;
  }
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight") &&
      e.target && e.target.id === "dict-search") {
    const suggest = document.getElementById("dict-suggest");
    if (suggest && suggest.hidden && lastLookup.word) {
      e.preventDefault();
      const lang = (store.get().settings || {}).active_language || "en";
      const list = switcherProviders(lang);
      cycleProvider(list, lang, e.key === "ArrowRight" ? 1 : -1);
    }
  }
});

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}
