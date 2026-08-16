// Shared dictionary result card. Renders a WordEntry as a single card
// surface (no doubled .card wrappers) and wires up the provider switcher,
// vocab add/box badge, Regenerate (LLM only), and speaker buttons.
//
// Used by:
//   - Dictionary page (full page result)
//   - Review page    (reveal answer)
//   - Dict popup     (floating lookup)
//
// Callers hand in an `onSwitchProvider(word, provider)` callback so each
// page can drive its own lookup flow. The component owns the visual chrome
// and the in-card actions; the page owns the network plumbing.

import { renderWordCard } from "./word-card.js";
import { bindSpeakButtons } from "./speak.js";
import { api } from "../api.js";
import { cache } from "../cache.js";
import { store } from "../state.js";
import { toast } from "./toast.js";

/**
 * Build the list of provider items for a language's chain, enriched with
 * metadata. LLM is always included as the AI fallback. Used by pages that
 * need the switcher list without rendering a full card (e.g. the no-result
 * state on the dictionary page).
 */
export function switcherProvidersFor(lang, providerMeta) {
  const meta = providerMeta || {};
  const settings = store.get().settings || {};
  const chain = (settings.dict_chain_json || {})[lang] || [];
  const items = [];
  const seen = new Set();
  for (const e of chain) {
    if (!e || !e.name) continue;
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

export function providerDisplayName(name) {
  if (name === "llm") return "AI";
  if (name === "wordnet") return "WordNet";
  return name;
}

/**
 * Render a dictionary result card into `host`.
 *
 * @param {HTMLElement} host          - element that will hold the card.
 * @param {object}      entry         - WordEntry from the lookup.
 * @param {string}      source        - provider that produced `entry`.
 * @param {string}      word          - canonical word string.
 * @param {string}      lang          - language code.
 * @param {object}      vocabState    - { inVocab, leitnerBox } for the badge.
 * @param {Array}       providers     - switcher list (from switcherProvidersFor).
 * @param {Function}    onSwitchProvider - called as (word, provider) when the
 *                                         user clicks a provider segment.
 * @param {Function}    [onRegenerate]- called as (word) when the user clicks
 *                                     Regenerate. Only wired when source === "llm".
 * @param {object}      [opts]        - { showSwitcher=true, showVocabControl=true }.
 */
export function renderDictCard(host, entry, source, word, lang, vocabState, providers, onSwitchProvider, onRegenerate, opts = {}) {
  const settings = store.get().settings || {};
  const showSwitcher = opts.showSwitcher !== false;
  const showVocabControl = opts.showVocabControl !== false;
  const isLLM = source === "llm";
  const regenerate = (isLLM && typeof onRegenerate === "function")
    ? `<button type="button" class="btn btn--sm btn--ghost" data-action="regenerate" title="Re-run the AI lookup for this word">↻ Regenerate</button>`
    : "";

  const html = renderWordCard(entry, {
    source,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
    actions: regenerate,
  });
  host.innerHTML = html;
  const card = host.querySelector(".word-card");
  if (!card) return;

  paintSwitcher(card, providers, source, vocabState, {
    showSwitcher,
    showVocabControl,
    onSwitchProvider: (name) => onSwitchProvider(word, name),
  });
  bindVocabActions(card, { word, lang, entry, source, vocabState });
  bindRegenerateAction(card, { word, lang }, onRegenerate);
  bindSpeakButtons(card);
}

/**
 * Render a card body for the "no senses found" state. The host still
 * gets a single .card surface; the switcher is wired so the user can
 * retry with another provider.
 */
export function renderNoResultCard(host, word, lang, providers, activeName, suggestions, providerErrors, onSwitchProvider, onSuggest) {
  const errHtml = renderProviderErrors(providerErrors);
  const sugHtml = renderSuggestions(suggestions);
  host.innerHTML = `
    <div class="card">
      ${errHtml}
      <p>No senses found for "${escapeHtml(word)}" in this provider.</p>
      <p class="field__hint">Switch to another provider below to try again.</p>
      ${sugHtml}
    </div>
  `;
  const card = host.querySelector(".card");
  if (onSuggest) bindSuggestionChips(host, onSuggest);
  paintSwitcher(card, providers, activeName, null, {
    showSwitcher: true,
    showVocabControl: false,
    onSwitchProvider: (name) => onSwitchProvider(word, name),
  });
}

/**
 * Paint (or replace) the provider switcher bar at the top of `card`.
 * `providers` is the list from switcherProvidersFor; `activeName` is the
 * currently-selected provider. `vocabState` controls the vocab slot on the
 * right of the bar.
 */
export function paintSwitcher(card, providers, activeName, vocabState, opts = {}) {
  if (!card) return;
  const existing = card.querySelector(".result-provider-switcher");
  if (existing) existing.remove();
  const showSwitcher = opts.showSwitcher !== false && providers && providers.length >= 2;
  const showVocabControl = opts.showVocabControl !== false;
  const vocabHtml = showVocabControl ? renderVocabControl(vocabState) : "";
  if (!showSwitcher && !vocabHtml) return;

  const bar = document.createElement("div");
  bar.className = "result-provider-switcher";
  if (showSwitcher) {
    bar.innerHTML = `
      <span class="result-provider-switcher__label">Source:</span>
      <button class="provider-switcher__nav" type="button" data-action="prev" aria-label="Previous provider" ${providers.length < 2 ? "disabled" : ""}>‹</button>
      <div class="segmented" role="radiogroup" aria-label="Dictionary provider"></div>
      <button class="provider-switcher__nav" type="button" data-action="next" aria-label="Next provider" ${providers.length < 2 ? "disabled" : ""}>›</button>
      <div class="result-provider-switcher__vocab">${vocabHtml}</div>
    `;
  } else {
    bar.innerHTML = `<div class="result-provider-switcher__vocab">${vocabHtml}</div>`;
  }
  card.insertBefore(bar, card.firstChild);

  if (showSwitcher) {
    const segments = bar.querySelector(".segmented");
    paintSegments(segments, providers, activeName, opts.onSwitchProvider);
    bar.querySelector("[data-action='prev']").addEventListener("click", () => {
      cycleSwitcher(card, providers, -1, opts.onSwitchProvider);
    });
    bar.querySelector("[data-action='next']").addEventListener("click", () => {
      cycleSwitcher(card, providers, 1, opts.onSwitchProvider);
    });
  }
}

/**
 * Update just the active segment of an existing switcher.
 */
export function selectSegment(segments, name) {
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    const isActive = el.dataset.provider === name;
    el.classList.toggle("segmented__item--active", isActive);
    el.setAttribute("aria-checked", isActive ? "true" : "false");
  });
}

/**
 * Cycle the active segment of the switcher that lives inside `card` by
 * `delta` (+1 / -1) and invoke `onSwitch(name)` with the new provider.
 * Used by keyboard shortcuts on the dictionary page.
 */
export function cycleSwitcher(card, providers, delta, onSwitch) {
  if (!providers || !providers.length || !onSwitch) return;
  const segments = card.querySelector(".result-provider-switcher .segmented");
  if (!segments) return;
  const cur = currentSelectedName(segments) || "";
  let idx = providers.findIndex((p) => p.name === cur);
  if (idx === -1) idx = delta > 0 ? -1 : 0;
  const next = (idx + delta + providers.length) % providers.length;
  const target = providers[next];
  if (!target || target.enabled === false) return;
  selectSegment(segments, target.name);
  onSwitch(target.name);
}

export function currentSelectedName(segments) {
  if (!segments) return null;
  const active = segments.querySelector(".segmented__item--active");
  return active ? active.dataset.provider : null;
}

function paintSegments(segments, list, selectedName, onSwitch) {
  segments.innerHTML = list.map((p) => {
    const active = (selectedName || "") === p.name;
    const ai = p.kind === "ai" || p.is_llm;
    const disabled = p.enabled === false;
    const cls = ["segmented__item"];
    if (active) cls.push("segmented__item--active");
    if (ai) cls.push("segmented__item--ai");
    if (disabled) cls.push("segmented__item--disabled");
    return `<button type="button" class="${cls.join(" ")}"
              data-provider="${escapeHtml(p.name)}"
              role="radio"
              aria-checked="${active}"
              ${disabled ? 'aria-disabled="true"' : ""}
              title="${escapeHtml(p.description || "")}">${escapeHtml(p.display_name || p.name)}</button>`;
  }).join("");
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    el.addEventListener("click", () => {
      const name = el.dataset.provider;
      const p = list.find((x) => x.name === name);
      if (!p || p.enabled === false) return;
      selectSegment(segments, name);
      if (onSwitch) onSwitch(name);
    });
  });
}

function renderVocabControl(vocabState) {
  if (vocabState && vocabState.inVocab === true && Number.isInteger(vocabState.leitnerBox)) {
    const box = clampBox(vocabState.leitnerBox);
    return `<span class="badge badge--ok" data-vocab-badge="in-box" data-box="${box}" title="This word is in box ${box}">Box ${box}</span>`;
  }
  return `<button type="button" class="btn btn--sm btn--ghost" data-action="add-to-vocab" title="Add this word to your vocabulary (box 1)">+ Add</button>`;
}

function clampBox(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 1) return 1;
  if (n > 5) return 5;
  return Math.round(n);
}

function bindVocabActions(card, ctx) {
  const addBtn = card.querySelector('[data-action="add-to-vocab"]');
  if (!addBtn) return;
  addBtn.addEventListener("click", async () => {
    if (!ctx.entry) return;
    addBtn.disabled = true;
    const original = addBtn.textContent;
    addBtn.textContent = "Adding…";
    const res = await api.post("/api/vocab/add-from-entry", {
      lang: ctx.lang,
      word: ctx.word,
      source: ctx.source || "user",
      pos: firstPos(ctx.entry),
      glossary: firstGlossary(ctx.entry),
      example: firstExample(ctx.entry),
      explanation_primary: firstExplanation(ctx.entry, "primary"),
      explanation_secondary: firstExplanation(ctx.entry, "secondary"),
    });
    if (!res.ok) {
      toast({ title: "Couldn't add to vocab",
              message: res.error || "unknown error",
              variant: "error" });
      addBtn.disabled = false;
      addBtn.textContent = original;
      return;
    }
    const box = (res.data && res.data.leitner_box) || 1;
    const slot = card.querySelector(".result-provider-switcher__vocab");
    if (slot) slot.innerHTML = renderVocabControl({ inVocab: true, leitnerBox: box });
    try {
      const all = JSON.parse(localStorage.getItem("langlearn:dict:v1") || "{}");
      for (const key of Object.keys(all)) {
        if (key.endsWith(`:${ctx.lang}:${ctx.word.toLowerCase()}`)) {
          const bySource = all[key].bySource || {};
          for (const s of Object.keys(bySource)) {
            bySource[s].inVocab = true;
            bySource[s].leitnerBox = box;
          }
          all[key].bySource = bySource;
        }
      }
      localStorage.setItem("langlearn:dict:v1", JSON.stringify(all));
    } catch (e) { /* best-effort */ }
    toast({ title: `Added "${ctx.word}" to box ${box}`,
            message: "Source: dictionary lookup",
            variant: "success",
            ttl: 2200 });
  });
}

function bindRegenerateAction(card, ctx, onRegenerate) {
  const btn = card.querySelector('[data-action="regenerate"]');
  if (!btn || typeof onRegenerate !== "function") return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Regenerating…";
    cache.clear(ctx.lang, ctx.word, "llm");
    try {
      await onRegenerate(ctx.word);
    } finally {
      if (btn.isConnected) {
        btn.disabled = false;
        btn.textContent = original;
      }
    }
  });
}

function bindSuggestionChips(host, onSuggest) {
  host.querySelectorAll("button.chip[data-suggest]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const word = btn.dataset.suggest;
      if (word) onSuggest(word);
    });
  });
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

function renderProviderErrors(errors) {
  if (!errors || !errors.length) return "";
  const items = errors.map((e) => {
    const name = providerDisplayName(e.provider);
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

function firstPos(entry) {
  const senses = (entry && entry.senses) || [];
  return (senses[0] && senses[0].pos) || "";
}
function firstGlossary(entry) {
  const senses = (entry && entry.senses) || [];
  const defs = (senses[0] && senses[0].definitions) || [];
  return (defs[0] && defs[0].glossary) || "";
}
function firstExample(entry) {
  const senses = (entry && entry.senses) || [];
  const defs = (senses[0] && senses[0].definitions) || [];
  return (defs[0] && defs[0].example) || null;
}
function firstExplanation(entry, key) {
  const senses = (entry && entry.senses) || [];
  const ex = (senses[0] && senses[0].explanations) || {};
  return ex[key] || null;
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