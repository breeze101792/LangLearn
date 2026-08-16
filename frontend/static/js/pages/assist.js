// Assist page: text + image tools on one page — Analyze, Refine, Translate,
// Describe — switched with a sub-nav tab bar exactly like the Learn page's
// Review / New words / Reviewed words subpages.
//
// Routes:
//   #/assist             — Analyze (default)
//   #/assist/refine      — Refine
//   #/assist/translate   — Translate
//   #/assist/describe    — Describe (image -> vocabulary)
//
// Each tool keeps its own textarea text and last result, persisted via
// page-state.js so switching tabs and returning keeps everything.

import { api, uploadForm } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { consumeRestoredState } from "../components/page-state.js";

// Per-tool live state, read by saveState() on navigation away.
const moduleState = {
  analyze: { lastResult: null },
  refine: { lastResult: null },
  translate: { lastResult: null },
  describe: { lastResult: null },
};

export function renderAssist(host) {
  const hash = window.location.hash || "#/assist";
  if (hash === "#/assist/refine") {
    renderRefinePage(host);
    return;
  }
  if (hash === "#/assist/translate") {
    renderTranslatePage(host);
    return;
  }
  if (hash === "#/assist/describe") {
    renderDescribePage(host);
    return;
  }
  renderAnalyzePage(host);
}

function renderAssistSubNav(host, active) {
  const items = [
    { key: "analyze", hash: "#/assist", label: "Analyze" },
    { key: "refine", hash: "#/assist/refine", label: "Refine" },
    { key: "translate", hash: "#/assist/translate", label: "Translate" },
    { key: "describe", hash: "#/assist/describe", label: "Describe" },
  ];
  host.insertAdjacentHTML("afterbegin", `
    <nav class="transfer-tabs" aria-label="Text tools">
      ${items.map((i) => `
        <a class="transfer-tabs__item ${i.key === active ? "is-active" : ""}"
           href="${i.hash}" aria-current="${i.key === active ? "page" : "false"}">${escapeHtml(i.label)}</a>
      `).join("")}
    </nav>
  `);
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

// Resize an image File in the browser before upload so a 30 MiB phone
// photo never crosses the wire. Loads the file into an <img>, draws it
// onto a canvas capped at MAX_EDGE px on its long side (preserving
// aspect ratio), and re-encodes as JPEG at quality 0.85. GIFs are
// flattened to a single frame (the first). Falls back gracefully: if
// the source is already small enough the original File is returned
// unchanged; if the canvas path throws, the caller catches and
// uploads the original.
//
// The MAX_EDGE of 1568 matches the long-side cap of OpenAI's "high
// detail" vision tier — anything bigger is downscaled by the provider
// anyway, so we save the upload bandwidth.
const DESCRIBE_MAX_EDGE = 1568;
const DESCRIBE_JPEG_QUALITY = 0.85;

function resizeImageForUpload(file) {
  return new Promise((resolve, reject) => {
    if (typeof document === "undefined" || typeof document.createElement !== "function") {
      resolve(file);
      return;
    }
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      try {
        const srcW = img.naturalWidth || img.width || 0;
        const srcH = img.naturalHeight || img.height || 0;
        if (!srcW || !srcH) {
          resolve(file);
          return;
        }
        const longest = Math.max(srcW, srcH);
        if (longest <= DESCRIBE_MAX_EDGE && file.size <= 1.5 * 1024 * 1024) {
          // Already small enough — keep the original bytes (and the
          // original format, which matters for PNGs with text).
          resolve(file);
          return;
        }
        const scale = DESCRIBE_MAX_EDGE / longest;
        const dstW = Math.max(1, Math.round(srcW * scale));
        const dstH = Math.max(1, Math.round(srcH * scale));
        const canvas = document.createElement("canvas");
        canvas.width = dstW;
        canvas.height = dstH;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          resolve(file);
          return;
        }
        // White background so transparent PNGs don't get black
        // backgrounds when re-encoded as JPEG.
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, dstW, dstH);
        ctx.drawImage(img, 0, 0, dstW, dstH);
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              resolve(file);
              return;
            }
            const name = (file.name || "image").replace(/\.[^.]+$/, "") + ".jpg";
            resolve(new File([blob], name, { type: "image/jpeg" }));
          },
          "image/jpeg",
          DESCRIBE_JPEG_QUALITY,
        );
      } catch (e) {
        reject(e);
      }
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("could not decode image"));
    };
    img.src = url;
  });
}

// ---------------------------------------------------------------------------
// Analyze tool
// ---------------------------------------------------------------------------

function renderAnalyzePage(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();

  let lastResult = moduleState.analyze.lastResult;

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
  renderAssistSubNav(host, "analyze");

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

  if (restored && typeof restored === "object") {
    if (typeof restored.text === "string") {
      textarea.value = restored.text;
    }
    if (restored.lastResult && typeof restored.lastResult === "object") {
      lastResult = restored.lastResult;
      moduleState.analyze.lastResult = lastResult;
      renderResult(result, lastResult);
    }
  }

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
      moduleState.analyze.lastResult = lastResult;
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

  function section(title, body) {
    return `
      <section class="analyze-section">
        <h2 class="analyze-section__title">${escapeHtml(title)}</h2>
        <div class="analyze-section__items">${body}</div>
      </section>
    `;
  }
}

// ---------------------------------------------------------------------------
// Refine tool
// ---------------------------------------------------------------------------

function renderRefinePage(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();

  let lastResult = moduleState.refine.lastResult;

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
  renderAssistSubNav(host, "refine");

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

  if (restored && typeof restored === "object" && typeof restored.text === "string") {
    textarea.value = restored.text;
    if (restored.lastResult && typeof restored.lastResult === "object") {
      lastResult = restored.lastResult;
      moduleState.refine.lastResult = lastResult;
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
      moduleState.refine.lastResult = lastResult;
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

// ---------------------------------------------------------------------------
// Translate tool
// ---------------------------------------------------------------------------

function renderTranslatePage(host) {
  const state = store.get();
  const targetLang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();
  const targetName = langDisplayName(targetLang, languages);

  let lastResult = moduleState.translate.lastResult;

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
  renderAssistSubNav(host, "translate");

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
    if (restored.lastResult && typeof restored.lastResult === "object") {
      lastResult = restored.lastResult;
      moduleState.translate.lastResult = lastResult;
      renderResult(result, lastResult, targetName);
    }
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
      lastResult = res.data || null;
      moduleState.translate.lastResult = lastResult;
      renderResult(result, lastResult, targetName);
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

// ---------------------------------------------------------------------------
// Describe tool (image -> target-language description + vocab list)
// ---------------------------------------------------------------------------

function renderDescribePage(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const languages = state.languages || [];
  const restored = consumeRestoredState();

  let lastResult = moduleState.describe.lastResult;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Describe</h1>
      <p class="page-head__subtitle">Upload a photo. The AI describes it in ${escapeHtml(langDisplayName(lang, languages))} and pulls out concrete vocabulary items you can add to your box with one click.</p>
    </header>
    <section class="card">
      <div class="field">
        <label class="field__label" for="describe-file">Photo (jpg / png / webp / gif)</label>
        <input id="describe-file" type="file" accept="image/jpeg,image/png,image/webp,image/gif" class="input">
        <p class="field__hint">Large photos are resized in your browser before upload. The image is sent to the LLM once and not stored on the server.</p>
      </div>
      <div id="describe-preview-wrap" class="describe-preview-wrap" hidden>
        <img id="describe-preview" class="describe-preview" alt="Selected photo preview">
        <button id="describe-clear" type="button" class="btn btn--ghost btn--sm">Remove</button>
      </div>
      <div class="row" style="margin-top: var(--sp-3); align-items: center">
        <button id="describe-btn" class="btn btn--primary" type="button" disabled>✨ Describe</button>
        <span class="spacer"></span>
        <span class="badge badge--ai">In ${escapeHtml(langDisplayName(lang, languages))}</span>
        ${primary ? `<span class="badge">${escapeHtml(langDisplayName(primary, languages))} notes</span>` : ""}
        ${secondary ? `<span class="badge">${escapeHtml(langDisplayName(secondary, languages))} notes</span>` : ""}
      </div>
    </section>
    <section id="describe-result" style="margin-top: var(--sp-4)"></section>
  `;
  renderAssistSubNav(host, "describe");

  const btn = host.querySelector("#describe-btn");
  const fileInput = host.querySelector("#describe-file");
  const previewWrap = host.querySelector("#describe-preview-wrap");
  const previewImg = host.querySelector("#describe-preview");
  const clearBtn = host.querySelector("#describe-clear");
  const result = host.querySelector("#describe-result");

  let selectedFile = null;
  let objectUrl = null;

  function clearPreview() {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    selectedFile = null;
    previewWrap.hidden = true;
    previewImg.removeAttribute("src");
    btn.disabled = true;
  }

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      clearPreview();
      return;
    }
    // Allow large originals (the resize pass shrinks them before upload),
    // but still cap the raw input so a 100 MiB phone photo doesn't hang
    // the tab. 30 MiB matches the server-side cap.
    if (file.size > 30 * 1024 * 1024) {
      toast({ title: "Image too large", message: "Pick a file under 30 MiB.", variant: "error" });
      fileInput.value = "";
      clearPreview();
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp", "image/gif"].includes(file.type)) {
      toast({ title: "Unsupported file type", message: "Use jpg, png, webp, or gif.", variant: "error" });
      fileInput.value = "";
      clearPreview();
      return;
    }
    selectedFile = file;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    previewImg.src = objectUrl;
    previewWrap.hidden = false;
    btn.disabled = false;
  });

  clearBtn.addEventListener("click", () => {
    fileInput.value = "";
    clearPreview();
  });

  btn.addEventListener("click", runDescribe);

  if (restored && typeof restored === "object") {
    if (restored.lastResult && typeof restored.lastResult === "object") {
      lastResult = restored.lastResult;
      moduleState.describe.lastResult = lastResult;
      renderResult(result, lastResult);
    }
  }

  async function runDescribe() {
    if (!selectedFile) {
      toast({ title: "Pick a photo first", variant: "error" });
      fileInput.focus();
      return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Describing…";
    result.innerHTML = `
      <div class="card">
        <div class="row" style="gap: var(--sp-3); align-items: center">
          <span class="spinner"></span>
          <span>Looking at the picture…</span>
        </div>
      </div>
    `;
    try {
      // Resize on the client so we never upload a 30 MiB phone photo.
      // Falls back to the original file if the canvas path fails (e.g.
      // the browser can't decode the format); the server still caps at
      // 30 MiB as a safety net.
      let uploadFile = selectedFile;
      try {
        uploadFile = await resizeImageForUpload(selectedFile);
      } catch (e) {
        console.warn("client-side image resize failed, uploading original", e);
      }
      const formData = new FormData();
      formData.append("file", uploadFile, uploadFile.name || selectedFile.name);
      formData.append("language", lang);
      const res = await uploadForm("/api/describe", formData);
      if (!res.ok) {
        result.innerHTML = `
          <div class="card" style="border-left: 4px solid var(--danger)">
            <strong>Couldn't describe the photo</strong>
            <p class="field__hint">${escapeHtml(res.error || "unknown error")}</p>
          </div>`;
        return;
      }
      lastResult = res.data || { description: "", words: [] };
      moduleState.describe.lastResult = lastResult;
      renderResult(result, lastResult);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  function renderResult(host, data) {
    const description = (data && data.description) || "";
    const descPrimary = (data && data.description_primary) || null;
    const descSecondary = (data && data.description_secondary) || null;
    const words = (data && data.words) || [];

    if (!description && !words.length) {
      host.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">🪶</div>
          <div class="empty-state__title">Nothing to describe</div>
          <div class="empty-state__msg">The AI couldn't find anything concrete in the picture. Try a different photo.</div>
        </div>
      `;
      return;
    }

    host.innerHTML = `
      <div class="list">
        ${description ? `
          <section class="analyze-section">
            <h2 class="analyze-section__title">Description</h2>
            <div class="card refine-card">
              <p class="refine-card__text">${escapeHtml(description)}</p>
              ${descPrimary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(primary, languages))}:</span> ${escapeHtml(descPrimary)}</p>` : ""}
              ${descSecondary ? `<p class="refine-card__native"><span class="list-item__meta-label">${escapeHtml(langDisplayName(secondary, languages))}:</span> ${escapeHtml(descSecondary)}</p>` : ""}
            </div>
          </section>
        ` : ""}
        ${words.length ? `
          <section class="analyze-section">
            <h2 class="analyze-section__title">Vocabulary (${words.length})</h2>
            <div class="analyze-section__items">
              ${words.map((w, i) => renderWord(w, i)).join("")}
            </div>
          </section>
        ` : ""}
      </div>
    `;
    host.querySelectorAll("button[data-save]").forEach((b) => {
      b.addEventListener("click", () => onSave(b));
    });
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
    const idx = Number(btn.dataset.idx);
    const list = lastResult.words || [];
    const item = list[idx];
    if (!item) return;
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Saving…";
    const res = await api.post("/api/vocab/add-from-entry", {
      lang,
      word: item.word,
      source: "llm",
      pos: item.pos,
      glossary: item.glossary,
      example: item.example,
      explanation_primary: item.explanation_primary,
      explanation_secondary: item.explanation_secondary,
    });
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

// Persist the active tool's textarea and last result so a quick detour to
// another page doesn't lose work. The router calls this on every hash
// change; without saving, navigating back would re-render a blank page.
export function saveState() {
  const hash = window.location.hash || "#/assist";
  const tool = hash === "#/assist/refine" ? "refine"
    : hash === "#/assist/translate" ? "translate"
    : hash === "#/assist/describe" ? "describe"
    : "analyze";
  const id = {
    analyze: "analyze-text",
    refine: "refine-text",
    translate: "translate-text",
    describe: null,
  }[tool];
  const textarea = id ? document.getElementById(id) : null;
  const text = textarea ? textarea.value : "";
  const lastResult = moduleState[tool].lastResult;
  if (!text && !lastResult) return null;
  return { text, lastResult };
}

export function dispose() {
  // Nothing global to detach; each tool only binds handlers to its own DOM.
}
