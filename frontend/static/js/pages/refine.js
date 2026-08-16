// Refine page: paste a sentence or short paragraph, the AI returns a
// grammar-corrected version, a more idiomatic native-speaker version, a
// list of small in-place edits with reasons, and a short explanation.
//
// The textarea text and the last refine result are persisted via
// page-state.js so switching tabs and returning keeps everything.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { consumeRestoredState } from "../components/page-state.js";

export function renderRefine(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();

  let lastResult = null;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Refine</h1>
      <p class="page-head__subtitle">Paste a sentence or short paragraph in your active language. The AI will fix grammar, suggest a more native way to say it, and explain each change.</p>
    </header>
    <section class="card">
      <div class="field">
        <label class="field__label" for="refine-text">Text (in ${escapeHtml(langDisplayName(lang, languages))})</label>
        <textarea id="refine-text" class="input" rows="6" maxlength="4000"
                  placeholder="e.g. I am go to the store yesterday because I want buy some apple."></textarea>
        <p class="field__hint">Max 4000 characters. Cmd/Ctrl + Enter to refine.</p>
      </div>
      <div class="row" style="margin-top: var(--sp-3); align-items: center">
        <button id="refine-btn" class="btn btn--primary" type="button">✨ Refine</button>
        <span class="spacer"></span>
        ${primary ? `<span class="badge badge--ai">${escapeHtml(langDisplayName(primary, languages))} explanation</span>` : ""}
        ${secondary ? `<span class="badge">${escapeHtml(langDisplayName(secondary, languages))} explanation</span>` : ""}
      </div>
    </section>
    <section id="refine-result" style="margin-top: var(--sp-4)"></section>
  `;

  const btn = host.querySelector("#refine-btn");
  const textarea = host.querySelector("#refine-text");
  const result = host.querySelector("#refine-result");

  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      runRefine();
    }
  });
  btn.addEventListener("click", runRefine);

  // Restore the user's text and the previous refine result so a
  // quick detour doesn't lose work.
  if (restored && typeof restored === "object" && typeof restored.text === "string") {
    textarea.value = restored.text;
    if (restored.lastResult && typeof restored.lastResult === "object") {
      lastResult = restored.lastResult;
      renderResult(result, lastResult);
    }
  }

  async function runRefine() {
    const text = textarea.value.trim();
    if (!text) {
      toast({ title: "Add some text first", message: "Paste a sentence or paragraph to refine.", variant: "error" });
      textarea.focus();
      return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Refining…";
    result.innerHTML = `
      <div class="card">
        <div class="row" style="gap: var(--sp-3); align-items: center">
          <span class="spinner"></span>
          <span>Refining the text…</span>
        </div>
      </div>
    `;
    try {
      const res = await api.post("/api/refine", { language: lang, text });
      if (!res.ok) {
        result.innerHTML = `
          <div class="card" style="border-left: 4px solid var(--danger)">
            <strong>Couldn't refine the text</strong>
            <p class="field__hint">${escapeHtml(res.error || "unknown error")}</p>
          </div>`;
        return;
      }
      lastResult = res.data || null;
      renderResult(result, lastResult);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  function renderResult(host, data) {
    const corrected = (data && data.corrected) || "";
    const native = (data && data.native) || "";
    const edits = (data && data.edits) || [];
    const explanation = (data && data.explanation) || "";
    const explPrimary = (data && data.explanation_primary) || null;
    const explSecondary = (data && data.explanation_secondary) || null;

    const originalText = textarea.value.trim();
    const highlightedOriginal = highlightSpans(originalText, edits.map((e) => ({ ...e, which: "original" })));
    const highlightedCorrected = highlightSpans(corrected, edits.map((e) => ({ ...e, which: "suggested" })));

    host.innerHTML = `
      <div class="list">
        <section class="analyze-section">
          <h2 class="analyze-section__title">Corrected</h2>
          <div class="card refine-card">
            <p class="refine-card__text">${highlightedCorrected || `<span class="field__hint">${escapeHtml(corrected)}</span>`}</p>
          </div>
        </section>
        <section class="analyze-section">
          <h2 class="analyze-section__title">How a native would say it</h2>
          <div class="card refine-card">
            <p class="refine-card__text">${escapeHtml(native) || '<span class="field__hint">—</span>'}</p>
          </div>
        </section>
        ${edits.length ? `
          <section class="analyze-section">
            <h2 class="analyze-section__title">Changes (${edits.length})</h2>
            <div class="card refine-card">
              <ol class="refine-edits">
                ${edits.map((e, i) => renderEdit(e, i)).join("")}
              </ol>
            </div>
          </section>
        ` : ""}
        <section class="analyze-section">
          <h2 class="analyze-section__title">Your original with notes</h2>
          <div class="card refine-card">
            <p class="refine-card__text">${highlightedOriginal || `<span class="field__hint">${escapeHtml(originalText)}</span>`}</p>
            ${edits.length ? `
              <ul class="refine-edit-legend">
                ${edits.map((e, i) => `
                  <li><span class="refine-edit-marker" data-idx="${i}">${i + 1}</span>
                    <span><code>${escapeHtml(e.original || "")}</code> → <code>${escapeHtml(e.suggested || "")}</code>
                    <span class="field__hint"> — ${escapeHtml(e.reason || "")}</span>
                  </span></li>
                `).join("")}
              </ul>
            ` : `<p class="field__hint">No changes needed — the input already looks correct.</p>`}
          </div>
        </section>
        ${explanation ? `
          <section class="analyze-section">
            <h2 class="analyze-section__title">Explanation</h2>
            <div class="card refine-card">
              <p class="refine-card__text">${escapeHtml(explanation)}</p>
              ${explPrimary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(primary, languages))}:</span> ${escapeHtml(explPrimary)}</p>` : ""}
              ${explSecondary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(secondary, languages))}:</span> ${escapeHtml(explSecondary)}</p>` : ""}
            </div>
          </section>
        ` : ""}
      </div>
    `;
  }

  function renderEdit(e, i) {
    return `
      <li>
        <span class="refine-edit-marker" data-idx="${i}">${i + 1}</span>
        <div>
          <code>${escapeHtml(e.original || "")}</code> → <code>${escapeHtml(e.suggested || "")}</code>
          <div class="field__hint">${escapeHtml(e.reason || "")}</div>
        </div>
      </li>
    `;
  }
}

// Wrap each occurrence of every `original` (or `suggested`) span from
// the edits list with <mark data-idx="N">…</mark>. Case-sensitive,
// first-occurrence-only within each edit so a span that appears twice
// in the text gets matched twice across two edits.
function highlightSpans(text, edits) {
  if (!text || !edits.length) return escapeHtml(text);
  // Sort by span length desc so a longer span wins over its substrings
  // when we mark non-overlapping positions.
  const sorted = edits
    .map((e, idx) => ({ idx, span: e.which === "original" ? e.original : e.suggested }))
    .filter((e) => e.span && e.span.length > 0)
    .sort((a, b) => b.span.length - a.span.length);
  const marks = []; // {start, end, idx}
  for (const { idx, span } of sorted) {
    let from = 0;
    while (true) {
      const at = text.indexOf(span, from);
      if (at === -1) break;
      const end = at + span.length;
      // Skip if this position overlaps an existing mark.
      const overlaps = marks.some((m) => !(end <= m.start || at >= m.end));
      if (!overlaps) {
        marks.push({ start: at, end, idx });
      }
      from = at + 1;
    }
  }
  if (!marks.length) return escapeHtml(text);
  marks.sort((a, b) => a.start - b.start);
  let out = "";
  let cursor = 0;
  for (const m of marks) {
    out += escapeHtml(text.slice(cursor, m.start));
    out += `<mark class="refine-mark" data-idx="${m.idx}">${escapeHtml(text.slice(m.start, m.end))}</mark>`;
    cursor = m.end;
  }
  out += escapeHtml(text.slice(cursor));
  return out;
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

// Persist the textarea contents and the last refine result so a
// quick detour to another page doesn't lose work.
export function saveState() {
  const textarea = document.getElementById("refine-text");
  const text = textarea ? textarea.value : "";
  if (!text && !lastResult) return null;
  return { text, lastResult };
}
