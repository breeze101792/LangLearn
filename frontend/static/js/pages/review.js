// Review page: recall session with Leitner grading.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { renderWordCard, entryFromVocabRow, findCachedEntry } from "../components/word-card.js";

let session = null; // { items, idx, sessionSize }

export function renderReview(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Review</h1>
      <p class="page-head__subtitle">Recall each word's meaning, then reveal and grade yourself.</p>
    </header>
    <section id="review-body"></section>
  `;
  const body = host.querySelector("#review-body");
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
      <p class="field__hint">Recall the meaning, then reveal.</p>
      <button id="reveal" class="btn btn--primary btn--lg">Reveal (Space)</button>
    </div>
  `;
  host.querySelector("#end-session").addEventListener("click", () => {
    session = null;
    renderReview(document.getElementById("app-main"));
  });
  host.querySelector("#reveal").addEventListener("click", () => reveal(host, item));
  document.removeEventListener("keydown", sessionKeyHandler);
  document.addEventListener("keydown", sessionKeyHandler);
}

function reveal(host, item) {
  const card = host.querySelector("#review-card");

  // Prefer the cached full WordEntry from the Dictionary page so the review
  // reveal shows the same rich layout (all senses, explanations, examples).
  // Fall back to building a one-sense entry from the vocab row.
  const cached = findCachedEntry(item.language, item.word);
  const entry = cached || entryFromVocabRow(item);

  const settings = store.get().settings || {};
  const answer = document.createElement("div");
  answer.className = "review-card__answer";
  answer.innerHTML = renderWordCard(entry, {
    source: entry.source || item.source,
    languages: store.get().languages || [],
    explanationPrimary: settings.explanation_primary,
    explanationSecondary: settings.explanation_secondary,
  });
  card.appendChild(answer);

  const gradeRow = document.createElement("div");
  gradeRow.className = "review-card__grade-row";
  gradeRow.innerHTML = `
    <button id="grade-hard" class="btn btn--danger">1 · Hard</button>
    <button id="grade-easy" class="btn btn--primary">2 · Easy</button>
  `;
  card.appendChild(gradeRow);

  host.querySelector("#reveal").remove();
  host.querySelector("#grade-hard").addEventListener("click", () => grade(item, "hard", host));
  host.querySelector("#grade-easy").addEventListener("click", () => grade(item, "easy", host));
  host.querySelector("#grade-easy").focus();
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
  }
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