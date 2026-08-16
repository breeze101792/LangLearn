// Learn page: recall session with Leitner grading, plus two list subpages
// ("New words" = added today, "Reviewed words" = reviewed today).
//
// Routes:
//   #/learn            — the recall session (with a box scope selector)
//   #/learn/new        — list of words added today
//   #/learn/reviewed   — list of words reviewed today

import { api } from "../api.js";
import { cache } from "../cache.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { bindSpeakButtons } from "../components/speak.js";
import { entryFromVocabRow } from "../components/word-card.js";
import { findCachedRecord } from "../components/review-cache.js";
import { consumeRestoredState } from "../components/page-state.js";
import {
  renderDictCard,
  switcherProvidersFor,
} from "../components/dict-card.js";

const BOX_LABELS = {
  1: "Box 1 (new)",
  2: "Box 2",
  3: "Box 3",
  4: "Box 4",
  5: "Box 5 (mastered)",
};

// Review scope for the session. "due" = only items whose next_due passed
// (the original behaviour). "all" = every box regardless of due date.
// A number 1-5 = that single box.
// Order matters — kept as an array because JS object property iteration puts
// integer-like keys (1..5) before string keys, breaking the intended order.
const SCOPE_OPTIONS = [
  { key: "due", label: "Due" },
  { key: "all", label: "All boxes" },
  { key: 1, label: "Box 1" },
  { key: 2, label: "Box 2" },
  { key: 3, label: "Box 3" },
  { key: 4, label: "Box 4" },
  { key: 5, label: "Box 5" },
];

let session = null; // { items, idx, lang, scope }
// Provider metadata for the active language, loaded once per session so the
// review reveal can offer the same provider switcher as the dictionary page.
let providerMeta = {};
let providerMetaLoaded = false;
let providerMetaLang = null;

// Live state for the list subpages, read by saveState() on navigation away.
const moduleState = { activeBox: null, offset: null };

export function renderLearn(host) {
  const hash = window.location.hash || "#/learn";
  if (hash === "#/learn/new") {
    renderListPage(host, "new");
    return;
  }
  if (hash === "#/learn/reviewed") {
    renderListPage(host, "reviewed");
    return;
  }
  renderSessionPage(host);
}

// ---------------------------------------------------------------------------
// Sub-nav shared by all three review subpages.
// ---------------------------------------------------------------------------

function renderSubNav(host, active) {
  const items = [
    { key: "review", hash: "#/learn", label: "Review" },
    { key: "new", hash: "#/learn/new", label: "New words" },
    { key: "reviewed", hash: "#/learn/reviewed", label: "Reviewed words" },
  ];
  host.insertAdjacentHTML("afterbegin", `
    <nav class="transfer-tabs" aria-label="Review subpages">
      ${items.map((i) => `
        <a class="transfer-tabs__item ${i.key === active ? "is-active" : ""}"
           href="${i.hash}" aria-current="${i.key === active ? "page" : "false"}">${escapeHtml(i.label)}</a>
      `).join("")}
    </nav>
  `);
}

// ---------------------------------------------------------------------------
// Main review session page (#/learn)
// ---------------------------------------------------------------------------

function renderSessionPage(host) {
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
  renderSubNav(host, "review");
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
      scope: restored.scope || "due",
      revealed: restored.revealed || {},
      graded: restored.graded || {},
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
  const totalCount = Object.values(counts).reduce((acc, n) => acc + (n || 0), 0);
  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Ready to review</h2>
      <p style="margin: var(--sp-2) 0"><strong>${due}</strong> word${due === 1 ? "" : "s"} due · session size: ${sessionSize}</p>
      <p class="field__hint">Due: ${due} · All: ${totalCount} · Box 1 (new): ${counts[1] || 0} · Box 2: ${counts[2] || 0} · Box 3: ${counts[3] || 0} · Box 4: ${counts[4] || 0} · Box 5: ${counts[5] || 0}</p>
      <div class="field" style="margin-top: var(--sp-3)">
        <label class="field__label" for="review-scope">Review scope</label>
        <div class="segmented" id="review-scope" role="tablist" aria-label="Review scope">
          ${SCOPE_OPTIONS.map((opt) => `
            <button class="segmented__item ${opt.key === "due" ? "segmented__item--active" : ""}"
                    data-scope="${opt.key}" role="tab" aria-selected="${opt.key === "due" ? "true" : "false"}">${escapeHtml(opt.label)}</button>
          `).join("")}
        </div>
        <p class="field__hint">"Due" reviews only words whose review date has passed. "All boxes" reviews every word regardless of date.</p>
      </div>
      <div class="row" style="margin-top: var(--sp-4)">
        <button id="start-review" class="btn btn--primary" ${due === 0 ? "disabled" : ""}>Start review</button>
        ${due === 0 ? `<span class="field__hint">Nothing due right now. Pick "All boxes" to review everything, or look up words in the dictionary to add them to your queue.</span>` : ""}
      </div>
    </div>
  `;
  let scope = "due";
  const scopeHost = host.querySelector("#review-scope");
  scopeHost?.addEventListener("click", (e) => {
    const btn = e.target.closest("button.segmented__item");
    if (!btn) return;
    scope = btn.dataset.scope;
    scopeHost.querySelectorAll("button.segmented__item").forEach((b) => {
      const on = (b === btn);
      b.classList.toggle("segmented__item--active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
  });
  host.querySelector("#start-review")?.addEventListener("click", () => startSession(host, lang, sessionSize, scope));
}

async function startSession(host, lang, n, scope) {
  const box = scope === "all" ? 0 : (scope === "due" ? null : Number(scope));
  // Shuffle the pool so a "due" session isn't the same every time.
  const qs = `lang=${encodeURIComponent(lang)}&n=${n}&shuffle=1`
    + (box !== null ? `&box=${box}` : "");
  const res = await api.get(`/api/vocab/review/next?${qs}`);
  if (!res.ok) {
    toast({ title: "Could not start review", message: res.error, variant: "error" });
    return;
  }
  const items = res.data.items || [];
  if (!items.length) {
    toast({ title: "Nothing to review", message: "Add words via the dictionary first.", variant: "info" });
    return;
  }
  session = { items, idx: 0, lang, scope };
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
  const atStart = session.idx === 0;
  const atEnd = session.idx >= total - 1;
  host.innerHTML = `
    <div class="row" style="margin-bottom: var(--sp-3)">
      <button id="nav-prev" class="btn btn--ghost btn--sm" ${atStart ? "disabled" : ""} aria-label="Previous word">← Prev</button>
      <span style="min-width: 3em; text-align: center">${session.idx + 1} / ${total}</span>
      <div class="progress" aria-label="Progress" style="flex: 1"><div class="progress__bar" style="width: ${pct.toFixed(1)}%"></div></div>
      <button id="nav-next" class="btn btn--ghost btn--sm" ${atEnd ? "disabled" : ""} aria-label="Next word">Next →</button>
      <button id="end-session" class="btn btn--ghost btn--sm">End session</button>
    </div>
    <div class="card review-card" id="review-card">
      <div class="review-card__head">
        <span class="review-card__prompt">${escapeHtml(item.word)}</span>
        <button type="button" class="word-card__speak" data-action="speak" data-word="${escapeHtml(item.word)}" data-lang="${escapeHtml(item.language)}" aria-label="Pronounce ${escapeHtml(item.word)}" title="Pronounce ${escapeHtml(item.word)}">🔊</button>
      </div>
      <div class="review-card__sub">
        <span class="word-card__pos">${escapeHtml(item.pos || "—")}</span>
        <span class="review-card__gloss">${escapeHtml(item.glossary || "")}</span>
      </div>
      <p class="field__hint">Recall the meaning, then reveal. Or skip if you already know it.</p>
      <div class="review-card__actions" id="review-actions">
        <button id="reveal" class="btn btn--primary btn--lg">Reveal (Space)</button>
        <button id="know-it" class="btn btn--ghost btn--lg" title="Grade as easy and move on">I know this (3)</button>
      </div>
    </div>
    <div class="review-result" id="review-result" ${revealedFor(session.idx) ? "" : "hidden"}>
      <div id="review-answer"></div>
    </div>
  `;
  host.querySelector("#end-session").addEventListener("click", () => {
    session = null;
    renderLearn(document.getElementById("app-main"));
  });
  host.querySelector("#nav-prev").addEventListener("click", () => go(host, -1));
  host.querySelector("#nav-next").addEventListener("click", () => go(host, +1));
  host.querySelector("#reveal").addEventListener("click", () => reveal(host, item));
  host.querySelector("#know-it").addEventListener("click", () => grade(item, "easy", host));
  bindSpeakButtons(host.querySelector("#review-card"));
  // Re-paint a previously-revealed or graded answer when walking back.
  if (isGraded(session.idx)) {
    renderGradedCard(host, item);
  } else if (revealedFor(session.idx)) {
    reveal(host, item, true);
  }
  document.removeEventListener("keydown", sessionKeyHandler);
  document.addEventListener("keydown", sessionKeyHandler);
}

// Repaint a card that was already graded: show the answer and a "graded"
// note, but no re-grade buttons (it's already counted this session).
function renderGradedCard(host, item) {
  const actions = host.querySelector("#review-actions");
  const resultHost = host.querySelector("#review-result");
  const answerHost = host.querySelector("#review-answer");

  const cached = findCachedRecord(item.language, item.word, undefined,
    switcherListFor(item.language).map((p) => p.name));
  const entry = (cached && cached.entry) || entryFromVocabRow(item);
  const initialSource = (cached && cached.source) || item.source || "";

  ensureProviderMeta(item.language).then(() => {
    const providers = switcherListFor(item.language);
    renderDictCard(answerHost, entry, initialSource, item.word, item.language,
      { inVocab: true, leitnerBox: item.leitner_box ?? null },
      providers,
      (w, name) => switchAnswerProvider(item, answerHost, name),
      null);
  });

  resultHost.hidden = false;
  actions.innerHTML = `<span class="badge badge--ok">Graded this session</span>`;
}

function go(host, delta) {
  const nextIdx = session.idx + delta;
  if (nextIdx < 0 || nextIdx >= session.items.length) return;
  session.idx = nextIdx;
  renderSession(host);
}

function revealedFor(idx) {
  return !!(session.revealed && session.revealed[idx]);
}

function markRevealed(idx) {
  session.revealed = session.revealed || {};
  session.revealed[idx] = true;
}

async function reveal(host, item, replay = false) {
  const actions = host.querySelector("#review-actions");
  const resultHost = host.querySelector("#review-result");
  const answerHost = host.querySelector("#review-answer");

  await ensureProviderMeta(item.language);
  const providers = switcherListFor(item.language);
  const chainOrder = providers.map((p) => p.name);

  // Prefer the cached full WordEntry from the Dictionary page so the review
  // reveal shows the same rich layout (all senses, explanations, examples).
  // Walk the chain in order (like the Dictionary page) so the highlighted
  // source matches the chain's leading provider, not the most recent fetch.
  // Fall back to building a one-sense entry from the vocab row.
  const cached = findCachedRecord(item.language, item.word, undefined, chainOrder);
  const entry = (cached && cached.entry) || entryFromVocabRow(item);
  const initialSource = (cached && cached.source) || item.source || "";

  // Render the dictionary entry as a single card (no doubled .card
  // wrapper). The provider switcher, vocab control, and speak buttons
  // are wired by renderDictCard.
  renderDictCard(answerHost, entry, initialSource, item.word, item.language,
    { inVocab: true, leitnerBox: item.leitner_box ?? null },
    providers,
    (w, name) => switchAnswerProvider(item, answerHost, name),
    null);

  resultHost.hidden = false;
  markRevealed(session.idx);

  // Swap the action row contents: remove "I know this" and surface Hard/Easy
  // up here, where the user is already focused, instead of after the answer.
  actions.innerHTML = `
    <button id="grade-hard" class="btn btn--danger">1 · Hard</button>
    <button id="grade-easy" class="btn btn--primary">2 · Easy</button>
  `;
  host.querySelector("#grade-hard").addEventListener("click", () => grade(item, "hard", host));
  host.querySelector("#grade-easy").addEventListener("click", () => grade(item, "easy", host));
  // Only steal focus when the user actually clicked Reveal, not when we are
  // replaying an already-revealed card on back-navigation.
  if (!replay) host.querySelector("#grade-easy").focus();
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
  return switcherProvidersFor(lang, providerMeta);
}

async function switchAnswerProvider(item, answerHost, provider) {
  const cached = cache.get(item.language, item.word, provider);
  if (cached && cached.entry) {
    repaintAnswer(answerHost, cached.entry, cached.source || provider, item);
    return;
  }
  const res = await api.post("/api/dictionary/lookup", {
    lang: item.language,
    word: item.word,
    provider,
  });
  if (!res.ok) {
    toast({ title: "Couldn't switch provider", message: res.error, variant: "error" });
    return;
  }
  const data = res.data || {};
  if (!data.entry || !data.entry.senses || data.entry.senses.length === 0) {
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
  repaintAnswer(answerHost, data.entry, data.source || provider, item);
}

function repaintAnswer(answerHost, entry, source, item) {
  const providers = switcherListFor(item.language);
  renderDictCard(answerHost, entry, source, item.word, item.language,
    { inVocab: true, leitnerBox: item.leitner_box ?? null },
    providers,
    (w, name) => switchAnswerProvider(item, answerHost, name),
    null);
}

async function grade(item, value, host) {
  const res = await api.post("/api/vocab/review/grade", { vocab_id: item.id, grade: value });
  if (!res.ok) {
    toast({ title: "Couldn't save grade", message: res.error, variant: "error" });
    return;
  }
  // Mark this card as graded so navigating back doesn't offer to re-grade it.
  markGraded(session.idx);
  session.idx++;
  renderSession(host);
}

function markGraded(idx) {
  session.graded = session.graded || {};
  session.graded[idx] = true;
}

function isGraded(idx) {
  return !!(session.graded && session.graded[idx]);
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
    renderLearn(document.getElementById("app-main"));
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
  } else if (e.key === "ArrowLeft") {
    const b = document.getElementById("nav-prev");
    if (b && !b.disabled) b.click();
  } else if (e.key === "ArrowRight") {
    const b = document.getElementById("nav-next");
    if (b && !b.disabled) b.click();
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

// ---------------------------------------------------------------------------
// List subpages (#/learn/new and #/learn/reviewed)
// ---------------------------------------------------------------------------

function renderListPage(host, kind) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const pageSize = (state.settings && state.settings.page_size) || 20;
  const restored = consumeRestoredState();

  const isNew = kind === "new";
  const title = isNew ? "New words" : "Reviewed words";
  const subtitle = isNew
    ? "Words added today (Box 1) that you haven't reviewed yet."
    : "Words you reviewed today, from any box (1-5).";

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">${title}</h1>
      <p class="page-head__subtitle">${subtitle}</p>
    </header>
    <section id="review-list"></section>
  `;
  renderSubNav(host, kind);
  const list = host.querySelector("#review-list");

  let offset = 0;
  if (restored && typeof restored === "object") {
    if (Number.isFinite(restored.offset) && restored.offset >= 0) {
      offset = restored.offset;
    }
  }

  list.addEventListener("click", (e) => {
    const wordLink = e.target.closest("a.vocab-word");
    if (wordLink) {
      store.set({ pendingDictionaryWord: wordLink.dataset.word });
      return;
    }
    const prev = e.target.closest("button[data-pager='prev']");
    if (prev && !prev.disabled) { offset = Math.max(0, offset - pageSize); load(); return; }
    const next = e.target.closest("button[data-pager='next']");
    if (next && !next.disabled) { offset += pageSize; load(); return; }
  });

  async function load() {
    // "Today" in the browser's local timezone. DB timestamps are UTC, so we
    // translate the local midnight window to UTC ISO bounds.
    const { start, end } = todayUtcBounds();
    // New words: added today. Reviewed words: reviewed today, any box.
    const dateFilter = isNew
      ? `added_after=${encodeURIComponent(start)}&added_before=${encodeURIComponent(end)}`
      : `reviewed_after=${encodeURIComponent(start)}&reviewed_before=${encodeURIComponent(end)}`;
    const qs = `lang=${encodeURIComponent(lang)}&${dateFilter}&limit=${pageSize}&offset=${offset}`;
    const res = await api.get(`/api/vocab?${qs}`);
    if (!res.ok) {
      list.innerHTML = `<div class="card" style="border-left: 4px solid var(--danger)">${escapeHtml(res.error || "load failed")}</div>`;
      return;
    }
    const { items, total } = res.data;
    renderList(items || [], Number(total) || 0);
  }

  function renderList(items, total) {
    if (!items.length) {
      const msg = isNew
        ? "No new words added today. Look up a word in Dictionary to add one."
        : "No words reviewed today. Run a review session and grade words.";
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">📚</div>
          <div class="empty-state__title">${msg}</div>
        </div>`;
      return;
    }
    list.innerHTML = `<div class="list">${items.map(renderRow).join("")}</div>` + renderPager(items.length, total);
    bindSpeakButtons(list);
  }

  function renderPager(itemCount, total) {
    if (!total) return "";
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.floor(offset / pageSize) + 1;
    const atStart = offset === 0;
    const atEnd = offset + itemCount >= total;
    return `
      <div class="row" style="margin-top: var(--sp-3); justify-content: center">
        <button class="btn btn--ghost btn--sm" data-pager="prev" ${atStart ? "disabled" : ""}>← Previous</button>
        <span class="field__hint" style="margin: 0 var(--sp-2)">Page ${currentPage} of ${totalPages} · ${total} total</span>
        <button class="btn btn--ghost btn--sm" data-pager="next" ${atEnd ? "disabled" : ""}>Next →</button>
      </div>
    `;
  }

  function renderRow(item) {
    const box = clampBox(item.leitner_box);
    const sourceBadge = item.source === "llm"
      ? `<span class="badge badge--ai">AI</span>`
      : item.source === "user"
        ? `<span class="badge badge--user">You</span>`
        : `<span class="badge badge--builtin">WordNet</span>`;
    const pos = item.pos ? `<span class="badge badge--muted">${escapeHtml(item.pos)}</span>` : "";
    const wordDisplay = item.word.replace(/_/g, " ");
    return `
      <article class="list-item" data-id="${item.id}">
        <div class="list-item__badges">${sourceBadge}${pos}<span class="badge badge--muted">${escapeHtml(BOX_LABELS[box])}</span></div>
        <div class="list-item__main"><strong><a href="#/dictionary" class="vocab-word" data-word="${escapeHtml(wordDisplay)}" data-lang="${escapeHtml(item.language)}" title="Look up in Dictionary">${escapeHtml(wordDisplay)}</a></strong>
          <button type="button" class="word-card__speak" data-action="speak" data-word="${escapeHtml(wordDisplay)}" data-lang="${escapeHtml(item.language)}" aria-label="Pronounce ${escapeHtml(wordDisplay)}" title="Pronounce ${escapeHtml(wordDisplay)}">🔊</button>
        </div>
        ${item.glossary ? `<div class="list-item__meta">${escapeHtml(item.glossary)}</div>` : ""}
        ${item.example ? `<div class="list-item__meta" style="color: var(--text-muted)"><em>${escapeHtml(item.example)}</em></div>` : ""}
      </article>
    `;
  }

  load();

  moduleState.offset = () => offset;
}

// Persist the in-progress session so a quick detour to Settings
// doesn't lose the user's place. Only worth saving when the user
// is mid-session — the pre-session and finished screens contribute
// nothing.
export function saveState() {
  const hash = window.location.hash || "#/learn";
  if (hash === "#/learn/new" || hash === "#/learn/reviewed") {
    const offset = moduleState.offset ? moduleState.offset() : 0;
    if (!offset) return null;
    return { offset };
  }
  if (!session || !session.items || !session.items.length) return null;
  return {
    items: session.items,
    idx: session.idx,
    lang: session.lang,
    scope: session.scope,
    revealed: session.revealed || {},
    graded: session.graded || {},
  };
}

// Detach the keyboard handler so it doesn't fire on a different page.
export function dispose() {
  document.removeEventListener("keydown", sessionKeyHandler);
}

function clampBox(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 1) return 1;
  if (n > 5) return 5;
  return Math.round(n);
}

// Return the [start, end) of "today" in the browser's local timezone,
// expressed as UTC datetime strings matching the DB's stored format
// ("YYYY-MM-DD HH:MM:SS", UTC). End is exclusive so we use start of tomorrow.
function todayUtcBounds() {
  const now = new Date();
  const startLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const endLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const fmt = (d) => {
    const iso = d.toISOString(); // "YYYY-MM-DDTHH:MM:SS.sssZ"
    return iso.replace("T", " ").replace(/\.\d{3}Z$/, "");
  };
  return { start: fmt(startLocal), end: fmt(endLocal) };
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
