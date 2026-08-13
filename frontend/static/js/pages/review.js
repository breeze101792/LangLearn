// Review page: recall session with Leitner grading.

import { api } from "../api.js";
import { cache } from "../cache.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { renderWordCard, entryFromVocabRow } from "../components/word-card.js";
import { findCachedRecord } from "../components/review-cache.js";
import { bindSpeakButtons } from "../components/speak.js";
import { consumeRestoredState } from "../components/page-state.js";

let session = null; // { items, idx, sessionSize }
// Provider metadata for the active language, loaded once per session so the
// review reveal can offer the same provider switcher as the dictionary page.
let providerMeta = {};
let providerMetaLoaded = false;
let providerMetaLang = null;

export function renderReview(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const restored = consumeRestoredState();
  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Review</h1>
      <p class="page-head__subtitle">Recall each word's meaning, then reveal and grade yourself.</p>
    </header>
    <section id="review-body"></section>
  `;
  const body = host.querySelector("#review-body");
  // If we navigated away mid-session, restore it directly. The items
  // list and current index are persisted; we replay the session at the
  // saved point instead of going through the pre-session "Ready to
  // review" screen.
  if (restored && Array.isArray(restored.items) && restored.items.length &&
      typeof restored.idx === "number" && restored.lang === lang) {
    session = {
      items: restored.items,
      idx: Math.max(0, Math.min(restored.idx, restored.items.length - 1)),
      lang,
    };
    renderSession(body);
    return;
  }
  renderPreSession(body, lang);
}

async function renderPreSession(host, lang) {
  const status = await api.get(`/api/vocab/review/status?lang=${encodeURIComponent(lang)}`);
  const settings = store.get().settings || {};
  const sessionSize = settings.page_size || 20;
  if (!status.ok) {
    host.innerHTML = `<div class="card" style="border-left: 4px solid var(--danger)">${escapeHtml(status.error)}</div>`;
    return;
  }
  const data = status.data || {};
  const due = data.due || 0;
  const counts = data.by_box || {};
  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Ready to review</h2>
      <p style="margin: var(--sp-2) 0"><strong>${due}</strong> word${due === 1 ? "" : "s"} due · session size: ${sessionSize}</p>
      <p class="field__hint">Box 1 (new): ${counts[1] || 0} · Box 2: ${counts[2] || 0} · Box 3: ${counts[3] || 0} · Box 4: ${counts[4] || 0} · Box 5: ${counts[5] || 0}</p>
      <div class="row" style="margin-top: var(--sp-4)">
        <button id="start-review" class="btn btn--primary" ${due === 0 ? "disabled" : ""}>Start review</button>
        ${due === 0 ? `<span class="field__hint">Nothing to review right now. Look up some words in the dictionary to add them to your queue.</span>` : ""}
      </div>
    </div>
  `;
  host.querySelector("#start-review")?.addEventListener("click", () => startSession(host, lang, sessionSize));
}

async function startSession(host, lang, n) {
  const res = await api.get(`/api/vocab/review/next?lang=${encodeURIComponent(lang)}&n=${n}`);
  if (!res.ok) {
    toast({ title: "Could not start review", message: res.error, variant: "error" });
    return;
  }
  const items = res.data.items || [];
  if (!items.length) {
    toast({ title: "Nothing to review", message: "Add words via the dictionary first.", variant: "info" });
    return;
  }
  session = { items, idx: 0, lang };
  renderSession(host);
}

function renderSession(host) {
  if (!session || session.idx >= session.items.length) {
    renderFinished(host, session ? session.items : []);
    return;
  }
  const item = session.items[session.idx];
  const total = session.items.length;
  const pct = ((session.idx) / total) * 100;
  host.innerHTML = `
    <div class="row" style="margin-bottom: var(--sp-3)">
      <span>${session.idx + 1} / ${total}</span>
      <div class="progress" aria-label="Progress" style="flex: 1"><div class="progress__bar" style="width: ${pct.toFixed(1)}%"></div></div>
      <button id="end-session" class="btn btn--ghost btn--sm">End session</button>
    </div>
    <div class="review-card" id="review-card">
      <div class="review-card__prompt">${escapeHtml(item.word)}</div>
      <div class="review-card__reading">${escapeHtml(item.pos || "")}</div>
      <p class="field__hint">Recall the meaning, then reveal. Or skip if you already know it.</p>
      <div class="review-card__actions" id="review-actions">
        <button id="reveal" class="btn btn--primary btn--lg">Reveal (Space)</button>
        <button id="know-it" class="btn btn--ghost btn--lg" title="Grade as easy and move on">I know this (3)</button>
      </div>
    </div>
  `;
  host.querySelector("#end-session").addEventListener("click", () => {
    session = null;
    renderReview(document.getElementById("app-main"));
  });
  host.querySelector("#reveal").addEventListener("click", () => reveal(host, item));
  host.querySelector("#know-it").addEventListener("click", () => grade(item, "easy", host));
  document.removeEventListener("keydown", sessionKeyHandler);
  document.addEventListener("keydown", sessionKeyHandler);
}

async function reveal(host, item) {
  const card = host.querySelector("#review-card");
  const actions = host.querySelector("#review-actions");

  // Prefer the cached full WordEntry from the Dictionary page so the review
  // reveal shows the same rich layout (all senses, explanations, examples).
  // Fall back to building a one-sense entry from the vocab row.
  // Use cache.get directly so we keep the .source the entry was fetched
  // under — that drives the switcher highlight and the source badge.
  const cached = findCachedRecord(item.language, item.word);
  const entry = (cached && cached.entry) || entryFromVocabRow(item);
  const initialSource = (cached && cached.source) || item.source || "";

  const settings = store.get().settings || {};
  const answer = document.createElement("div");
  answer.className = "review-card__answer";
  answer.innerHTML = renderWordCard(entry, {
    source: initialSource,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
  });
  card.appendChild(answer);
  bindSpeakButtons(answer);

  // Swap the action row contents: remove "I know this" and surface Hard/Easy
  // up here, where the user is already focused, instead of after the answer.
  actions.innerHTML = `
    <button id="grade-hard" class="btn btn--danger">1 · Hard</button>
    <button id="grade-easy" class="btn btn--primary">2 · Easy</button>
  `;
  host.querySelector("#grade-hard").addEventListener("click", () => grade(item, "hard", host));
  host.querySelector("#grade-easy").addEventListener("click", () => grade(item, "easy", host));
  host.querySelector("#grade-easy").focus();

  // Provider switcher. Same shape as the dictionary result so it reuses the
  // existing `.result-provider-switcher` styles. Switching fetches the entry
  // for the chosen provider (cache first, server fallback) and re-renders
  // the answer in place — grading buttons stay where they are.
  await ensureProviderMeta(item.language);
  const switcher = buildSwitcher(item, answer, initialSource);
  card.insertBefore(switcher, answer);
}

async function ensureProviderMeta(lang) {
  if (providerMetaLoaded && providerMetaLang === lang) return providerMeta;
  const res = await api.get(`/api/dictionary/providers?lang=${encodeURIComponent(lang)}`);
  if (!res.ok) return providerMeta;
  providerMeta = {};
  for (const p of res.data.providers || []) {
    providerMeta[p.name] = p;
  }
  providerMetaLoaded = true;
  providerMetaLang = lang;
  return providerMeta;
}

function switcherListFor(lang) {
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
      kind: (m && m.kind) || (e.name === "llm" ? "ai" : "builtin"),
      supports: m ? m.supports !== false : true,
      enabled: e.enabled !== false,
    });
    seen.add(e.name);
  }
  if (!seen.has("llm")) {
    items.push({ name: "llm", display_name: "AI", kind: "ai", supports: true, enabled: true });
  }
  // Filter to providers the active language actually supports. A provider
  // that's missing from the metadata is treated as supported.
  return items.filter((p) => p.supports);
}

function buildSwitcher(item, answer, activeName) {
  const list = switcherListFor(item.language);
  const bar = document.createElement("div");
  bar.className = "result-provider-switcher";
  bar.innerHTML = `
    <span class="result-provider-switcher__label">Source:</span>
    <button class="provider-switcher__nav" type="button" data-action="prev" aria-label="Previous provider" ${list.length < 2 ? "disabled" : ""}>‹</button>
    <div class="segmented" role="radiogroup" aria-label="Dictionary provider"></div>
    <button class="provider-switcher__nav" type="button" data-action="next" aria-label="Next provider" ${list.length < 2 ? "disabled" : ""}>›</button>
  `;
  const segments = bar.querySelector(".segmented");
  paintSegments(segments, list, activeName, item, answer, bar);
  bar.querySelector("[data-action='prev']").addEventListener("click", () => {
    cycleProvider(list, activeName, -1, item, answer, bar, segments);
  });
  bar.querySelector("[data-action='next']").addEventListener("click", () => {
    cycleProvider(list, activeName, 1, item, answer, bar, segments);
  });
  return bar;
}

function paintSegments(segments, list, selectedName, item, answer, switcher) {
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
              ${disabled ? 'aria-disabled="true"' : ""}>${escapeHtml(p.display_name || p.name)}</button>`;
  }).join("");
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    el.addEventListener("click", () => {
      const name = el.dataset.provider;
      const p = list.find((x) => x.name === name);
      if (!p || p.enabled === false) return;
      selectProvider(segments, name);
      switchAnswerProvider(item, answer, name, switcher);
    });
  });
}

function selectProvider(segments, name) {
  segments.querySelectorAll(".segmented__item").forEach((el) => {
    const isActive = el.dataset.provider === name;
    el.classList.toggle("segmented__item--active", isActive);
    el.setAttribute("aria-checked", isActive ? "true" : "false");
  });
}

function cycleProvider(list, currentName, delta, item, answer, switcher, segments) {
  if (!list.length) return;
  const cur = currentName || "";
  let idx = list.findIndex((p) => p.name === cur);
  if (idx === -1) idx = delta > 0 ? -1 : 0;
  let next = (idx + delta + list.length) % list.length;
  const target = list[next];
  if (!target || target.enabled === false) return;
  selectProvider(segments, target.name);
  switchAnswerProvider(item, answer, target.name, switcher);
}

async function switchAnswerProvider(item, answer, provider, switcher) {
  // 1) cache hit for this exact provider
  const hit = cache.get(item.language, item.word, provider);
  if (hit && hit.entry) {
    paintAnswer(answer, hit.entry, hit.source, item);
    setSwitcherBusy(switcher, false);
    return;
  }
  // 2) server lookup with explicit provider
  setSwitcherBusy(switcher, true);
  // Stash current content so we can restore it if the lookup fails.
  const previous = answer.innerHTML;
  answer.innerHTML = `
    <div class="review-card__loading">
      <span class="spinner"></span>
      <span>Looking up "${escapeHtml(item.word)}" from ${escapeHtml(providerDisplayName(provider))}…</span>
    </div>
  `;
  const res = await api.post("/api/dictionary/lookup", {
    lang: item.language,
    word: item.word,
    provider,
  });
  setSwitcherBusy(switcher, false);
  if (!res.ok) {
    answer.innerHTML = previous;
    toast({ title: "Couldn't switch provider", message: res.error, variant: "error" });
    return;
  }
  const data = res.data || {};
  if (!data.entry || !data.entry.senses || data.entry.senses.length === 0) {
    answer.innerHTML = previous;
    toast({ title: "No result from this provider", message: "Try a different source.", variant: "info" });
    return;
  }
  cache.set(item.language, item.word, provider, {
    entry: data.entry,
    word: item.word,
    autoAdded: false,
    inVocab: false,
    leitnerBox: null,
  });
  paintAnswer(answer, data.entry, data.source || provider, item);
}

function providerDisplayName(name) {
  if (name === "llm") return "AI";
  if (name === "wordnet") return "WordNet";
  return name;
}

function paintAnswer(answer, entry, source, item) {
  const settings = store.get().settings || {};
  answer.innerHTML = renderWordCard(entry, {
    source,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
  });
  bindSpeakButtons(answer);
}

function setSwitcherBusy(switcher, busy) {
  if (!switcher) return;
  switcher.classList.toggle("is-busy", busy);
  switcher.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
}

async function grade(item, value, host) {
  const res = await api.post("/api/vocab/review/grade", { vocab_id: item.id, grade: value });
  if (!res.ok) {
    toast({ title: "Couldn't save grade", message: res.error, variant: "error" });
    return;
  }
  session.idx++;
  renderSession(host);
}

function renderFinished(host, items) {
  document.removeEventListener("keydown", sessionKeyHandler);
  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Session complete</h2>
      <p>You reviewed ${items.length} word${items.length === 1 ? "" : "s"}.</p>
      <div class="row" style="margin-top: var(--sp-3)">
        <button class="btn btn--primary" id="restart">Review again</button>
        <a href="#/dictionary" class="btn btn--ghost">Back to dictionary</a>
      </div>
    </div>
  `;
  host.querySelector("#restart").addEventListener("click", () => {
    renderReview(document.getElementById("app-main"));
  });
}

function sessionKeyHandler(e) {
  if (!session) return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (e.key === " ") {
    e.preventDefault();
    const reveal = document.getElementById("reveal");
    if (reveal) reveal.click();
  } else if (e.key === "1") {
    const b = document.getElementById("grade-hard");
    if (b) b.click();
  } else if (e.key === "2" || e.key === "Enter") {
    const b = document.getElementById("grade-easy");
    if (b) b.click();
  } else if (e.key === "3") {
    const b = document.getElementById("know-it");
    if (b) b.click();
  }
}

// Persist the in-progress session so a quick detour to Settings
// doesn't lose the user's place. Only worth saving when the user
// is mid-session — the pre-session and finished screens contribute
// nothing.
export function saveState() {
  if (!session || !session.items || !session.items.length) return null;
  return {
    items: session.items,
    idx: session.idx,
    lang: session.lang,
  };
}

// Detach the keyboard handler so it doesn't fire on a different page.
export function dispose() {
  document.removeEventListener("keydown", sessionKeyHandler);
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