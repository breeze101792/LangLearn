// Analyze page: paste a sentence or paragraph, the AI extracts useful
// sentence structures, phrases, and hard words. Each item has a "+ Add"
// button that persists it to the matching table via the existing
// endpoints (POST /api/structures, /api/phrases, /api/vocab/add-from-entry).
//
// Nothing is saved on the Analyze endpoint itself: it's a one-shot LLM
// call, and the page's state is rebuilt on every mount.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";

export function renderAnalyze(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];

  let lastResult = null;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Analyze</h1>
      <p class="page-head__subtitle">Paste a sentence or short paragraph in your active language. The AI will extract useful structures, phrases, and difficult words — each item can be saved to your lists with one click.</p>
    </header>
    <section class="card">
      <div class="field">
        <label class="field__label" for="analyze-text">Text (in ${escapeHtml(langDisplayName(lang, languages))})</label>
        <textarea id="analyze-text" class="input" rows="6" maxlength="4000"
                  placeholder="e.g. She would rather stay home than go out in the rain."></textarea>
        <p class="field__hint">Max 4000 characters. Up to 5 structures, 5 phrases, and 8 hard words per analysis.</p>
      </div>
      <div class="row" style="margin-top: var(--sp-3); align-items: center">
        <button id="analyze-btn" class="btn btn--primary" type="button">✨ Analyze</button>
        <span class="spacer"></span>
        ${primary ? `<span class="badge badge--ai">${escapeHtml(langDisplayName(primary, languages))} explanations</span>` : ""}
        ${secondary ? `<span class="badge">${escapeHtml(langDisplayName(secondary, languages))} explanations</span>` : ""}
      </div>
    </section>
    <section id="analyze-result" style="margin-top: var(--sp-4)"></section>
  `;

  const btn = host.querySelector("#analyze-btn");
  const textarea = host.querySelector("#analyze-text");
  const result = host.querySelector("#analyze-result");

  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runAnalyze();
    }
  });
  btn.addEventListener("click", runAnalyze);

  async function runAnalyze() {
    const text = textarea.value.trim();
    if (!text) {
      toast({ title: "Add some text first", message: "Paste a sentence or paragraph to analyze.", variant: "error" });
      textarea.focus();
      return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Analyzing…";
    result.innerHTML = `
      <div class="card">
        <div class="row" style="gap: var(--sp-3); align-items: center">
          <span class="spinner"></span>
          <span>Analyzing the text…</span>
        </div>
      </div>
    `;
    try {
      const res = await api.post("/api/analyze", { language: lang, text });
      if (!res.ok) {
        result.innerHTML = `
          <div class="card" style="border-left: 4px solid var(--danger)">
            <strong>Couldn't analyze the text</strong>
            <p class="field__hint">${escapeHtml(res.error || "unknown error")}</p>
          </div>`;
        return;
      }
      lastResult = res.data || { structures: [], phrases: [], words: [] };
      renderResult(result, lastResult);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  function renderResult(host, data) {
    const structures = data.structures || [];
    const phrases = data.phrases || [];
    const words = data.words || [];
    const total = structures.length + phrases.length + words.length;
    if (total === 0) {
      host.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">🪶</div>
          <div class="empty-state__title">Nothing useful to extract</div>
          <div class="empty-state__msg">The text was too short or too simple. Try a longer paragraph or one with more varied vocabulary.</div>
        </div>
      `;
      return;
    }
    host.innerHTML = `
      <div class="list">
        ${structures.length ? section("Sentence structures", structures.map((s, i) => renderStructure(s, i)).join("")) : ""}
        ${phrases.length ? section("Phrases & expressions", phrases.map((p, i) => renderPhrase(p, i)).join("")) : ""}
        ${words.length ? section("Difficult words", words.map((w, i) => renderWord(w, i)).join("")) : ""}
      </div>
    `;
    host.querySelectorAll("button[data-save]").forEach((b) => {
      b.addEventListener("click", () => onSave(b));
    });
  }

  function renderStructure(s, i) {
    return `
      <article class="list-item" data-kind="structure" data-idx="${i}">
        <div class="list-item__main"><strong>${escapeHtml(s.pattern || "")}</strong></div>
        <div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Example:</span>
          <code>${escapeHtml(s.example_sentence || "")}</code>
        </div>
        ${s.explanation ? `<div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Explanation:</span>
          ${escapeHtml(s.explanation)}
        </div>` : ""}
        ${explainLines(s)}
        <div class="list-item__actions">
          <button type="button" class="btn btn--sm btn--primary" data-save="structure" data-idx="${i}">+ Add to Structures</button>
        </div>
      </article>
    `;
  }

  function renderPhrase(p, i) {
    return `
      <article class="list-item" data-kind="phrase" data-idx="${i}">
        <div class="list-item__main"><strong>${escapeHtml(p.phrase || "")}</strong></div>
        <div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Example:</span>
          <code>${escapeHtml(p.example_sentence || "")}</code>
        </div>
        ${p.explanation ? `<div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Explanation:</span>
          ${escapeHtml(p.explanation)}
        </div>` : ""}
        ${explainLines(p)}
        <div class="list-item__actions">
          <button type="button" class="btn btn--sm btn--primary" data-save="phrase" data-idx="${i}">+ Add to Phrases</button>
        </div>
      </article>
    `;
  }

  function renderWord(w, i) {
    return `
      <article class="list-item" data-kind="word" data-idx="${i}">
        <div class="list-item__main"><strong>${escapeHtml(w.word || "")}</strong> <span class="word-card__pos">${escapeHtml(w.pos || "")}</span></div>
        <div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Meaning:</span>
          ${escapeHtml(w.glossary || "")}
        </div>
        ${w.example ? `<div class="list-item__meta list-item__meta--target" title="In the target language">
          <span class="list-item__meta-label">Example:</span>
          <code>${escapeHtml(w.example)}</code>
        </div>` : ""}
        ${explainLines(w)}
        <div class="list-item__actions">
          <button type="button" class="btn btn--sm btn--primary" data-save="word" data-idx="${i}">+ Add to Vocab</button>
        </div>
      </article>
    `;
  }

  function explainLines(item) {
    const lines = [];
    if (primary && item.explanation_primary) {
      lines.push(`<div class="list-item__meta list-item__meta--native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(primary, languages))}:</span> ${escapeHtml(item.explanation_primary)}</div>`);
    }
    if (secondary && item.explanation_secondary) {
      lines.push(`<div class="list-item__meta list-item__meta--native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(secondary, languages))}:</span> ${escapeHtml(item.explanation_secondary)}</div>`);
    }
    return lines.join("");
  }

  async function onSave(btn) {
    if (!lastResult) return;
    const kind = btn.dataset.save;
    const idx = Number(btn.dataset.idx);
    const list = (kind === "structure" ? lastResult.structures
                : kind === "phrase"   ? lastResult.phrases
                :                       lastResult.words) || [];
    const item = list[idx];
    if (!item) return;
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Saving…";
    let res;
    if (kind === "structure") {
      res = await api.post("/api/structures", {
        language: lang,
        pattern: item.pattern,
        example_sentence: item.example_sentence,
        explanation: item.explanation,
        explanation_primary: item.explanation_primary,
        explanation_secondary: item.explanation_secondary,
        source: "llm",
      });
    } else if (kind === "phrase") {
      res = await api.post("/api/phrases", {
        language: lang,
        phrase: item.phrase,
        example_sentence: item.example_sentence,
        explanation: item.explanation,
        explanation_primary: item.explanation_primary,
        explanation_secondary: item.explanation_secondary,
        source: "llm",
      });
    } else {
      res = await api.post("/api/vocab/add-from-entry", {
        lang,
        word: item.word,
        source: "llm",
        pos: item.pos,
        glossary: item.glossary,
        example: item.example,
        explanation_primary: item.explanation_primary,
        explanation_secondary: item.explanation_secondary,
      });
    }
    if (!res.ok) {
      toast({ title: "Couldn't save", message: res.error || "unknown error", variant: "error" });
      btn.disabled = false;
      btn.textContent = original;
      return;
    }
    btn.textContent = "✓ Saved";
    btn.classList.remove("btn--primary");
    btn.classList.add("btn--ghost");
    toast({ title: "Saved", variant: "success", ttl: 1800 });
  }
}

function langDisplayName(code, languages) {
  if (!code) return "—";
  const lang = (languages || []).find((l) => l.code === code);
  if (lang) return lang.display_name;
  return code;
}

function section(title, body) {
  return `
    <section class="analyze-section">
      <h2 class="analyze-section__title">${escapeHtml(title)}</h2>
      <div class="analyze-section__items">${body}</div>
    </section>
  `;
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
