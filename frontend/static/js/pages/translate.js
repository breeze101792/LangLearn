// Translate page: write a sentence in any language, the AI translates
// it into the target (active) language and breaks it down so the page
// doubles as a study aid — alternative phrasings, a word-by-word gloss,
// and a short grammar note. The source language is auto-detected by
// the model; the target language is the user's active language.
//
// Nothing is saved: this is a quick utility. The result is rebuilt on
// every click.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { consumeRestoredState } from "../components/page-state.js";

export function renderTranslate(host) {
  const state = store.get();
  const targetLang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();
  const targetName = langDisplayName(targetLang, languages);

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Translate</h1>
      <p class="page-head__subtitle">Write a sentence in any language. The AI translates it into ${escapeHtml(targetName)} (the language you're learning), then breaks it down word by word with a short grammar note.</p>
    </header>
    <section class="card">
      <div class="field">
        <label class="field__label" for="translate-text">Text (any language)</label>
        <textarea id="translate-text" class="input" rows="5" maxlength="4000"
                  placeholder="e.g. I'd like a coffee, but not too hot."></textarea>
        <p class="field__hint">Max 4000 characters. The source language is detected automatically. Cmd/Ctrl + Enter to translate.</p>
      </div>
      <div class="row" style="margin-top: var(--sp-3); align-items: center">
        <button id="translate-btn" class="btn btn--primary" type="button">✨ Translate</button>
        <span class="spacer"></span>
        <span class="badge badge--ai">Into ${escapeHtml(targetName)}</span>
        ${primary ? `<span class="badge">${escapeHtml(langDisplayName(primary, languages))} notes</span>` : ""}
        ${secondary ? `<span class="badge">${escapeHtml(langDisplayName(secondary, languages))} notes</span>` : ""}
      </div>
    </section>
    <section id="translate-result" style="margin-top: var(--sp-4)"></section>
  `;

  const btn = host.querySelector("#translate-btn");
  const textarea = host.querySelector("#translate-text");
  const result = host.querySelector("#translate-result");

  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runTranslate();
    }
  });
  btn.addEventListener("click", runTranslate);

  if (restored && typeof restored === "object" && typeof restored.text === "string") {
    textarea.value = restored.text;
  }

  async function runTranslate() {
    const text = textarea.value.trim();
    if (!text) {
      toast({ title: "Add some text first", message: "Write a sentence to translate.", variant: "error" });
      textarea.focus();
      return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Translating…";
    result.innerHTML = `
      <div class="card">
        <div class="row" style="gap: var(--sp-3); align-items: center">
          <span class="spinner"></span>
          <span>Translating…</span>
        </div>
      </div>
    `;
    try {
      const res = await api.post("/api/translate", { text });
      if (!res.ok) {
        result.innerHTML = `
          <div class="card" style="border-left: 4px solid var(--danger)">
            <strong>Couldn't translate the text</strong>
            <p class="field__hint">${escapeHtml(res.error || "unknown error")}</p>
          </div>`;
        return;
      }
      renderResult(result, res.data, targetName);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  function renderResult(host, data, targetName) {
    const sentences = (data && data.sentences) || [];
    const notes = (data && data.notes) || "";
    const notesPrimary = (data && data.notes_primary) || null;
    const notesSecondary = (data && data.notes_secondary) || null;

    if (!sentences.length) {
      host.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">🪶</div>
          <div class="empty-state__title">Nothing to translate</div>
          <div class="empty-state__msg">The input was empty or too short.</div>
        </div>
      `;
      return;
    }

    host.innerHTML = `
      <div class="list">
        ${sentences.map((s, i) => renderSentence(s, i, targetName)).join("")}
        ${notes ? `
          <section class="analyze-section">
            <h2 class="analyze-section__title">Overall notes</h2>
            <div class="card refine-card">
              <p class="refine-card__text">${escapeHtml(notes)}</p>
              ${notesPrimary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(primary, languages))}:</span> ${escapeHtml(notesPrimary)}</p>` : ""}
              ${notesSecondary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(secondary, languages))}:</span> ${escapeHtml(notesSecondary)}</p>` : ""}
            </div>
          </section>
        ` : ""}
      </div>
    `;
  }

  function renderSentence(s, i, targetName) {
    const source = s.source || "";
    const translation = s.translation || "";
    const alternatives = s.alternatives || [];
    const breakdown = s.breakdown || [];
    const notes = s.notes || "";
    return `
      <section class="analyze-section translate-sentence">
        <h2 class="analyze-section__title">Sentence ${i + 1}</h2>
        <div class="card refine-card">
          <p class="refine-card__text translate-sentence__source">${escapeHtml(source)}</p>
          <p class="refine-card__text translate-sentence__target" title="${escapeHtml(targetName)}">${escapeHtml(translation)}</p>
        </div>
        ${alternatives.length ? `
          <div class="translate-alts-wrap">
            <h3 class="translate-subtitle">Other ways to say it</h3>
            <ul class="translate-alts">
              ${alternatives.map((a) => `
                <li>
                  <span class="translate-alts__text">${escapeHtml(a.text || "")}</span>
                  ${a.nuance ? `<span class="translate-alts__nuance field__hint">${escapeHtml(a.nuance)}</span>` : ""}
                </li>
              `).join("")}
            </ul>
          </div>
        ` : ""}
        ${breakdown.length ? `
          <div class="translate-breakdown-wrap">
            <h3 class="translate-subtitle">Word by word</h3>
            <table class="translate-breakdown__table">
              <thead>
                <tr><th>${escapeHtml(targetName)}</th><th>Original</th><th>Note</th></tr>
              </thead>
              <tbody>
                ${breakdown.map((b) => `
                  <tr>
                    <td><code>${escapeHtml(b.target || "")}</code></td>
                    <td>${escapeHtml(b.source || "")}</td>
                    <td class="field__hint">${b.note ? escapeHtml(b.note) : ""}</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        ` : ""}
        ${notes ? `
          <div class="translate-sentence-notes">
            <h3 class="translate-subtitle">Note</h3>
            <p class="refine-card__text">${escapeHtml(notes)}</p>
          </div>
        ` : ""}
      </section>
    `;
  }
}

function langDisplayName(code, languages) {
  if (!code) return "—";
  const lang = (languages || []).find((l) => l.code === code);
  if (lang) return lang.display_name;
  return code;
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

// Persist the textarea so a quick detour to another page doesn't lose
// work in progress.
export function saveState() {
  const textarea = document.getElementById("translate-text");
  const text = textarea ? textarea.value : "";
  if (!text) return null;
  return { text };
}