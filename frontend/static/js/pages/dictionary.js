// Dictionary page: search box + result card with provider switcher.
//
// The switcher mirrors the user's chain order from Settings. LLM is always
// present in every chain, so the user can fall back to AI when WordNet (or
// any other provider) doesn't have the word.

import { api } from "../api.js";
import { cache } from "../cache.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { consumeRestoredState } from "../components/page-state.js";
import {
  renderDictCard,
  renderNoResultCard,
  switcherProvidersFor,
  cycleSwitcher,
} from "../components/dict-card.js";

// Metadata fetched from /api/dictionary/providers keyed by name.
let providerMeta = {};
let llmStatus = { configured: true, provider_kind: "openai-compat" };
let providerMetaLoaded = false;
let lastLang = null;

const lastLookup = { word: "", lang: "", source: "" };
// Monotonic token so a slow in-flight response can't clobber the result of a
// newer lookup (e.g. an AI request for the previous word landing late).
let lookupToken = 0;

export function renderDictionary(host) {
  const state = store.get();
  const settings = state.settings || {};
  const lang = settings.active_language || "en";
  const restored = consumeRestoredState();

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
          <div class="dict-search__input-wrap" style="flex: 1; position: relative">
            <input id="dict-search" class="input" type="text" autocomplete="off"
                   placeholder="Type a word to look up…"
                   aria-label="Dictionary search"
                   role="combobox" aria-autocomplete="list" aria-expanded="false"
                   aria-controls="dict-suggest">
            <button id="dict-clear" class="dict-search__clear" type="button"
                    aria-label="Clear search" hidden>&times;</button>
          </div>
          <button id="dict-paste-btn" class="btn btn--icon" type="button"
                  title="Paste from clipboard and look up"
                  aria-label="Paste from clipboard and look up">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                 stroke="currentColor" stroke-width="2" stroke-linecap="round"
                 stroke-linejoin="round" aria-hidden="true">
              <path d="M9 2h6a1 1 0 0 1 1 1v1H8V3a1 1 0 0 1 1-1z"/>
              <rect x="6" y="4" width="12" height="18" rx="2"/>
              <path d="M9 12l2 2 4-4"/>
            </svg>
          </button>
          <button id="dict-search-btn" class="btn btn--primary btn--icon"
                  title="Look up" aria-label="Look up">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
                 stroke="currentColor" stroke-width="2" stroke-linecap="round"
                 stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7"/>
              <path d="M21 21l-4.3-4.3"/>
            </svg>
          </button>
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
  const clearBtn = host.querySelector("#dict-clear");
  const pasteBtn = host.querySelector("#dict-paste-btn");
  const result = host.querySelector("#dict-result");
  const suggestEl = host.querySelector("#dict-suggest");

  function syncClearButton() {
    if (clearBtn) clearBtn.hidden = !input.value;
  }
  syncClearButton();

  btn.addEventListener("click", () => doLookup(canonical(input.value), lang));
  clearBtn.addEventListener("click", () => {
    input.value = "";
    syncClearButton();
    hideSuggest(suggestEl);
    input.focus();
  });
  pasteBtn.addEventListener("click", () => pasteAndLookup(lang));
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
    syncClearButton();
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

  // Consume any pending word the right-click "Look up word" menu set
  // before navigating here. Prefill the input and run the lookup so
  // the user lands on a real result, not an empty search box. The
  // pending word is the user's most recent explicit intent, so it
  // takes priority over the sessionStorage-restored previous view
  // (otherwise the restored lastLookup would bump lookupToken and
  // drop the new-word response, leaving the old word on screen).
  const pending = store.get().pendingDictionaryWord;
  let consumeRestored = true;
  if (pending) {
    store.set({ pendingDictionaryWord: null });
    const word = canonical(pending);
    if (word) {
      input.value = word.replace(/_/g, " ");
      doLookup(word, lang);
      consumeRestored = false;
    }
  }

  // Page-state restoration: bring back the search input and the last
  // lookup so the user lands on the same view they left. Skipped when
  // a right-click handoff already pre-filled this visit.
  if (consumeRestored && restored && typeof restored === "object") {
    if (typeof restored.searchInput === "string") {
      input.value = restored.searchInput;
    }
    if (restored.lastLookup && restored.lastLookup.word && restored.lastLookup.lang === lang) {
      doLookup(restored.lastLookup.word, lang, restored.lastLookup.source || undefined);
    } else if (restored.searchInput) {
      // Only the input was preserved (no result). Nothing to do — the
      // input is already restored.
    }
  }
}

// When true, the in-flight lookup bypasses the local cache so a fresh
// request is sent to the server. Reset by `doLookup` after the call lands.
let skipNextCache = false;

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
    provider_kind: res.data.llm_provider_kind || "openai-compat",
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
      Set <code>OPENAI_API_KEY</code> (and optionally <code>OPENAI_BASE_URL</code>,
      <code>OPENAI_MODEL</code>) before launching the app. See the README.
    </p>
  `;
  result.appendChild(banner);
}

function switcherProviders(lang) {
  return switcherProvidersFor(lang, providerMeta);
}

function switchToProvider(name) {
  const word = lastLookup.word;
  if (!word) return;
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
      <div class="empty-state__icon">🔍</div>
      <div class="empty-state__title">No word looked up yet</div>
      <div class="empty-state__msg">Type a word above to look it up. Looked-up words are added to your vocab list automatically.</div>
    </div>
  `;
}

async function pasteAndLookup(lang) {
  let text = "";
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      text = await navigator.clipboard.readText();
    }
  } catch (err) {
    toast({
      title: "Couldn't read clipboard",
      message: "Paste a word into the box and press Enter instead.",
      variant: "info",
    });
    const input = document.getElementById("dict-search");
    if (input) input.focus();
    return;
  }
  const word = canonical(text);
  const input = document.getElementById("dict-search");
  if (input) {
    input.value = word.replace(/_/g, " ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (word) {
    doLookup(word, lang);
  } else {
    toast({
      title: "Clipboard is empty",
      message: "Nothing to look up.",
      variant: "info",
    });
    if (input) input.focus();
  }
}

async function doLookup(word, lang, providerOverride) {
  const resultHost = document.getElementById("dict-result");
  const myToken = ++lookupToken;
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

  // 1) local cache hit. A forced lookup asks for a specific source, so only
  // accept an entry produced by that provider; otherwise walk the chain in
  // order and use the first provider that has the word cached — this is what
  // resets the dictionary back to the leading provider on a fresh search.
  // A regenerate call forces a fresh fetch and skips the cache entirely.
  const chainOrder = switcherProviders(lang).map((p) => p.name);
  const cached = skipNextCache
    ? null
    : (providerOverride
        ? cache.get(lang, word, providerOverride)
        : cache.getInChain(lang, word, chainOrder));
  if (cached) {
    if (myToken !== lookupToken) return;
    lastLookup.word = word;
    lastLookup.lang = lang;
    lastLookup.source = providerOverride || cached.source || "";
    renderEntry(resultHost, cached.entry, cached.source, cached.word || word, lang, {
      inVocab: cached.inVocab === true,
      leitnerBox: cached.leitnerBox ?? null,
    });
    maybeShowUndoToast(cached.word || word, lang, cached.autoAdded);
    return;
  }

  lastLookup.word = word;
  lastLookup.lang = lang;
  lastLookup.source = providerOverride || "";

  // 2) server chain (or override)
  const body = { lang, word };
  if (providerOverride) body.provider = providerOverride;
  skipNextCache = false;
  const res = await api.post("/api/dictionary/lookup", body);
  if (myToken !== lookupToken) return; // a newer search started meanwhile
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
    renderNoResult(resultHost, word, lang, data.suggestions || [], data.provider_errors || []);
    return;
  }
  renderEntry(resultHost, data.entry, data.source, word, lang, {
    inVocab: data.in_vocab === true,
    leitnerBox: data.leitner_box ?? null,
  });
  lastLookup.source = data.source || "";
  cache.set(lang, word, data.source, {
    entry: data.entry,
    word,
    autoAdded: !!data.auto_added,
    inVocab: data.in_vocab === true,
    leitnerBox: data.leitner_box ?? null,
  });
  maybeShowUndoToast(word, lang, data.auto_added);
}

function renderEntry(host, entry, source, word, lang, vocabState) {
  const providers = switcherProviders(lang);
  renderDictCard(host, entry, source, word, lang, vocabState, providers,
    (w, name) => switchToProvider(name),
    (w) => doLookup(w, lastLookup.lang, "llm"));
}

function renderNoResult(host, word, lang, suggestions, providerErrors) {
  const providers = switcherProviders(lang);
  renderNoResultCard(host, word, lang, providers, "llm", suggestions, providerErrors,
    (w, name) => switchToProvider(name),
    (w) => {
      const input = document.getElementById("dict-search");
      if (input) input.value = w.replace(/_/g, " ");
      doLookup(w, lang);
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
// search input has no suggestion dropdown open. Named so dispose() can
// detach the listener when the page unmounts.
function onGlobalKeydown(e) {
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
      const card = document.querySelector("#dict-result .word-card");
      cycleSwitcher(card, list, e.key === "ArrowRight" ? 1 : -1, (name) => switchToProvider(name));
    }
  }
}
document.addEventListener("keydown", onGlobalKeydown);

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

// Save the current user-visible state so navigating away and back
// returns to the same view. The router calls this on every hash
// change; pages without saveState contribute nothing.
export function saveState() {
  const input = document.getElementById("dict-search");
  const searchInput = input ? input.value : "";
  // Only persist if there is something worth restoring. An empty
  // search input with no last lookup means the page was a fresh
  // blank, which the next mount would render anyway.
  if (!searchInput && !lastLookup.word) return null;
  return {
    searchInput,
    lastLookup: lastLookup.word
      ? { word: lastLookup.word, lang: lastLookup.lang, source: lastLookup.source }
      : null,
  };
}

// Drop the global "/" shortcut listener the page attaches at module
// load. The router calls this right before mounting the next page so
// the listener doesn't fire while a different page is on screen.
// (Per-input listeners are bound to elements that get destroyed when
// the host is re-rendered, so they don't pile up.)
export function dispose() {
  document.removeEventListener("keydown", onGlobalKeydown);
  if (suggestTimer) {
    clearTimeout(suggestTimer);
    suggestTimer = null;
  }
}
