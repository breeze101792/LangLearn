// Floating dictionary card. Reuses the same renderer as the Dictionary page
// so a right-click "Lookup" looks identical to a search-box lookup, but
// appears as a popup instead of a route change.

import { api } from "../api.js";
import { store } from "../state.js";
import { cache } from "../cache.js";
import { renderWordCard } from "./word-card.js";

const POPUP_ID = "dict-popup";
const ESC_KEY = "Escape";

let popupEl = null;
let popupBody = null;
let lookupToken = 0;
let activeWord = "";
let activeLang = "";
let activeSource = "";
let activeProviders = [];
let onDismissCb = null;

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function canonicalWord(raw) {
  if (!raw) return "";
  const parts = String(raw).trim().split(/\s+/).filter(Boolean);
  return parts.join("_").slice(0, 200);
}

function providerChain(lang) {
  const settings = store.get().settings || {};
  const chain = settings.dict_chain_json || {};
  return (chain[lang] || []).filter((e) => e && e.name);
}

function providerList(lang) {
  const chain = providerChain(lang);
  const items = chain.map((e) => ({
    name: e.name,
    display_name: e.name === "llm" ? "AI" : e.name,
    description: "",
    enabled: e.enabled !== false,
    is_llm: e.name === "llm",
  }));
  if (!items.find((p) => p.name === "llm")) {
    items.push({ name: "llm", display_name: "AI", description: "", enabled: true, is_llm: true });
  }
  return items;
}

function activeLanguage() {
  const s = store.get().settings || {};
  return s.active_language || "en";
}

/**
 * Open (or refresh) the popup with a lookup for `word`. Idempotent: opening
 * again while one is showing replaces the contents in-place rather than
 * stacking two popups.
 *
 * @param {object} opts
 * @param {string} opts.word     - the word/phrase to look up.
 * @param {string} [opts.lang]   - override the active language.
 * @param {Function} [opts.onDismiss] - called when the popup is closed.
 */
export async function openDictPopup({ word, lang, onDismiss } = {}) {
  if (!word) return;
  ensurePopup();
  onDismissCb = onDismiss || null;
  const w = canonicalWord(word);
  const lg = lang || activeLanguage();
  activeWord = w;
  activeLang = lg;
  activeSource = "";
  activeProviders = providerList(lg);
  showPopup();
  renderLoading(w);
  await runLookup(w, lg, null);
}

/**
 * Close the popup if it's open. No-op when not open.
 */
export function closeDictPopup() {
  if (!popupEl) return;
  dismiss();
}

function ensurePopup() {
  if (popupEl && document.body.contains(popupEl)) return;
  popupEl = document.createElement("div");
  popupEl.id = POPUP_ID;
  popupEl.className = "dict-popup";
  popupEl.setAttribute("role", "dialog");
  popupEl.setAttribute("aria-modal", "false");
  popupEl.setAttribute("aria-label", "Dictionary lookup");
  popupEl.innerHTML = `
    <div class="dict-popup__chrome" data-popup-handle>
      <span class="dict-popup__title">Dictionary</span>
      <button type="button" class="dict-popup__close" aria-label="Close">×</button>
    </div>
    <div class="dict-popup__body" data-popup-body></div>
  `;
  document.body.appendChild(popupEl);
  popupBody = popupEl.querySelector("[data-popup-body]");
  popupEl.querySelector(".dict-popup__close").addEventListener("click", dismiss);
  bindDrag(popupEl.querySelector("[data-popup-handle]"));
  popupEl.addEventListener("mousedown", (e) => {
    // Click on the chrome/body is fine; a click on the dimmed scrim below
    // dismisses. We listen on the outer container so we can detect the
    // scrim via event.target.
    if (e.target === popupEl) dismiss();
  });
}

function bindDrag(handle) {
  if (!handle) return;
  handle.addEventListener("mousedown", (e) => {
    // Don't start a drag when the user grabs the close button.
    if (e.target.closest(".dict-popup__close")) return;
    if (e.button !== 0) return;
    e.preventDefault();
    startDrag(e.clientX, e.clientY);
  });
}

function startDrag(startX, startY) {
  if (!popupEl) return;
  const rect = popupEl.getBoundingClientRect();
  const dx = startX - rect.left;
  const dy = startY - rect.top;
  popupEl.classList.add("dict-popup--dragging");
  const onMove = (ev) => {
    const left = clamp(ev.clientX - dx, -rect.width + 80, window.innerWidth - 80);
    const top = clamp(ev.clientY - dy, 0, window.innerHeight - 40);
    popupEl.style.left = `${left}px`;
    popupEl.style.top = `${top}px`;
    popupEl.style.right = "auto";
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    popupEl.classList.remove("dict-popup--dragging");
    persistPosition();
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

const POS_KEY = "langlearn:dict-popup:pos:v1";

function loadPosition() {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (typeof p.left !== "number" || typeof p.top !== "number") return null;
    return p;
  } catch (e) {
    return null;
  }
}

function persistPosition() {
  if (!popupEl) return;
  const rect = popupEl.getBoundingClientRect();
  try {
    localStorage.setItem(POS_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
  } catch (e) { /* ignore */ }
}

function applyPosition() {
  if (!popupEl) return;
  // Reset to default anchor before applying a stored offset.
  popupEl.style.left = "";
  popupEl.style.top = "";
  popupEl.style.right = "var(--sp-4)";
  const saved = loadPosition();
  if (!saved) return;
  // Clamp to current viewport so a position saved on a larger screen doesn't
  // hide the popup on a smaller one.
  const rect = popupEl.getBoundingClientRect();
  const maxLeft = Math.max(0, window.innerWidth - Math.min(rect.width, 120));
  const maxTop = Math.max(0, window.innerHeight - Math.min(rect.height, 40));
  const left = clamp(saved.left, 0, maxLeft);
  const top = clamp(saved.top, 0, maxTop);
  popupEl.style.left = `${left}px`;
  popupEl.style.top = `${top}px`;
  popupEl.style.right = "auto";
}

function showPopup() {
  if (!popupEl) return;
  applyPosition();
  popupEl.hidden = false;
  requestAnimationFrame(() => {
    if (popupEl) popupEl.classList.add("dict-popup--open");
  });
}

function renderLoading(word) {
  if (!popupBody) return;
  popupBody.innerHTML = `
    <div class="dict-popup__row">
      <span class="spinner"></span>
      <span>Looking up "${escapeHtml(word)}"…</span>
    </div>
  `;
}

function renderEmpty() {
  if (!popupBody) return;
  popupBody.innerHTML = `
    <div class="dict-popup__row dict-popup__row--muted">No word provided.</div>
  `;
}

function renderError(message) {
  if (!popupBody) return;
  popupBody.innerHTML = `
    <div class="dict-popup__row dict-popup__row--error">
      <strong>Couldn't look up "${escapeHtml(activeWord)}"</strong>
      <div class="field__hint">${escapeHtml(message || "unknown error")}</div>
    </div>
  `;
}

function renderProviderError(providerName, message) {
  if (!popupBody) return;
  const niceName = providerName === "llm" ? "AI"
    : providerName === "wordnet" ? "WordNet"
    : providerName;
  popupBody.innerHTML = `
    <div class="card">
      <div class="dict-provider-error">
        <strong>${escapeHtml(niceName)} couldn't look up "${escapeHtml(activeWord)}".</strong>
        <p class="field__hint" style="margin: var(--sp-1) 0 0">${escapeHtml(message || "unknown error")}</p>
      </div>
      <p class="field__hint">Try again, or switch to another provider.</p>
    </div>
  `;
  paintSwitcher(popupBody.querySelector(".card"), activeSource);
}

function renderNoResult(suggestions, providerErrors) {
  if (!popupBody) return;
  const sugHtml = (suggestions && suggestions.length)
    ? `<div class="did-you-mean" style="margin-top: var(--sp-3)">
         <p class="field__hint" style="margin: 0 0 var(--sp-2)">Did you mean:</p>
         <div class="row chip-row">${suggestions.map((w) =>
           `<button type="button" class="chip" data-suggest="${escapeHtml(w)}">${escapeHtml(w)}</button>`
         ).join("")}</div>
       </div>`
    : "";
  const errHtml = renderProviderErrors(providerErrors);
  popupBody.innerHTML = `
    <div class="card">
      ${errHtml}
      <p>No senses found for "${escapeHtml(activeWord)}" in this provider.</p>
      <p class="field__hint">Switch to another provider below to try again.</p>
      ${sugHtml}
    </div>
  `;
  popupBody.querySelectorAll("button.chip[data-suggest]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = btn.dataset.suggest;
      if (!next) return;
      openDictPopup({ word: next, lang: activeLang });
    });
  });
  paintSwitcher(popupBody.querySelector(".card"), activeSource || "llm");
}

function renderProviderErrors(errors) {
  if (!errors || !errors.length) return "";
  const items = errors.map((e) => {
    const name = e.provider === "llm" ? "AI" : (e.provider === "wordnet" ? "WordNet" : e.provider);
    const msg = String(e.error || "unknown error");
    return `<li><strong>${escapeHtml(name)}</strong>: ${escapeHtml(msg)}</li>`;
  }).join("");
  return `
    <div class="dict-provider-error">
      <strong>Some providers failed:</strong>
      <ul>${items}</ul>
    </div>
  `;
}

function renderEntry(entry, source) {
  if (!popupBody) return;
  const settings = store.get().settings || {};
  const html = renderWordCard(entry, {
    source,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
  });
  popupBody.innerHTML = `<div class="card">${html}</div>`;
  paintSwitcher(popupBody.querySelector(".card"), source || null);
}

function paintSwitcher(card, activeName) {
  if (!card) return;
  const existing = card.querySelector(".result-provider-switcher");
  if (existing) existing.remove();
  if (activeProviders.length < 2) return;
  const bar = document.createElement("div");
  bar.className = "result-provider-switcher";
  bar.innerHTML = `
    <span class="result-provider-switcher__label">Source:</span>
    <div class="segmented" role="radiogroup" aria-label="Dictionary provider"></div>
  `;
  card.insertBefore(bar, card.firstChild);
  const segments = bar.querySelector(".segmented");
  segments.innerHTML = activeProviders.map((p) => {
    const active = (activeName || "") === p.name;
    const ai = p.is_llm;
    const cls = ["segmented__item"];
    if (active) cls.push("segmented__item--active");
    if (ai) cls.push("segmented__item--ai");
    return `<button type="button" class="${cls.join(" ")}"
              data-provider="${escapeHtml(p.name)}"
              role="radio"
              aria-checked="${active}">${escapeHtml(p.display_name)}</button>`;
  }).join("");
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    el.addEventListener("click", () => {
      const name = el.dataset.provider;
      if (!name) return;
      activeSource = name;
      segments.querySelectorAll(".segmented__item").forEach((b) => {
        const on = b.dataset.provider === name;
        b.classList.toggle("segmented__item--active", on);
        b.setAttribute("aria-checked", on ? "true" : "false");
      });
      // Loading state is shown by runLookup only when it actually needs to
      // hit the network — cache hits should render the entry immediately
      // without a spinner flash.
      runLookup(activeWord, activeLang, name);
    });
  });
}

function renderLoadingPreservingSwitcher() {
  if (!popupBody) return;
  // Keep the provider switcher visible at the top; swap everything below
  // it for a loading row so the user sees feedback while a forced-provider
  // request is in flight.
  const card = popupBody.querySelector(".card");
  const switcher = card ? card.querySelector(".result-provider-switcher") : null;
  popupBody.innerHTML = `
    <div class="dict-popup__row">
      <span class="spinner"></span>
      <span>Looking up "${escapeHtml(activeWord)}"…</span>
    </div>
  `;
  if (switcher) {
    // Wrap the switcher + loading row in a card so it visually matches the
    // result states. The switcher becomes the first child of the card.
    const wrap = document.createElement("div");
    wrap.className = "card";
    wrap.appendChild(switcher);
    popupBody.insertBefore(wrap, popupBody.firstChild);
    wrap.appendChild(popupBody.querySelector(".dict-popup__row"));
  }
}

async function runLookup(word, lang, providerOverride) {
  const myToken = ++lookupToken;
  const chainOrder = activeProviders.map((p) => p.name);
  const cached = providerOverride
    ? cache.get(lang, word, providerOverride)
    : cache.getInChain(lang, word, chainOrder);
  if (cached) {
    if (myToken !== lookupToken) return;
    activeSource = cached.source || "";
    renderEntry(cached.entry, activeSource);
    return;
  }
  // No cache hit — we're about to make a network call. Show a loading row
  // so the user knows something is happening.
  renderLoadingPreservingSwitcher();
  const body = { lang, word };
  if (providerOverride) body.provider = providerOverride;
  const res = await api.post("/api/dictionary/lookup", body);
  if (myToken !== lookupToken) return;
  if (!res.ok) {
    renderError(res.error || "lookup failed");
    return;
  }
  const data = res.data || {};
  if (!data.entry || !data.entry.senses || data.entry.senses.length === 0) {
    const errs = data.provider_errors || [];
    // When the user explicitly forced a single provider and that provider
    // failed, surface the failure directly instead of the generic 'no
    // senses' card so they don't think AI is misconfigured or the word
    // simply isn't covered.
    if (providerOverride && errs.length === 1 && errs[0].provider === providerOverride) {
      renderProviderError(providerOverride, errs[0].error);
      return;
    }
    renderNoResult(data.suggestions || [], errs);
    return;
  }
  activeSource = data.source || "";
  renderEntry(data.entry, activeSource);
  cache.set(lang, word, activeSource, { entry: data.entry, word });
}

function dismiss() {
  if (!popupEl) return;
  popupEl.classList.remove("dict-popup--open");
  popupEl.hidden = true;
  lookupToken++;
  const cb = onDismissCb;
  onDismissCb = null;
  if (typeof cb === "function") {
    try { cb(); } catch (e) { console.error(e); }
  }
}

// Global Escape closes the popup when no context menu is active. The context
// menu handler binds its own Escape listener which short-circuits to itself
// when shown, so installing this on `keydown` is safe.
document.addEventListener("keydown", (e) => {
  if (!popupEl || popupEl.hidden) return;
  if (e.key !== ESC_KEY) return;
  e.preventDefault();
  dismiss();
});
