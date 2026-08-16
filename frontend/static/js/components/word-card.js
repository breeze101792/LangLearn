// Reusable word card. Renders one WordEntry (or a single-sense equivalent).
// Used by Dictionary (full multi-sense card) and Review (single-sense reveal).
//
// Returns an HTML string suitable for innerHTML insertion. All user-provided
// strings go through `escapeHtml`.

const PRIMARY_LANG_KEY = "primary";
const SECONDARY_LANG_KEY = "secondary";

/**
 * Render a word-card for one WordEntry or one vocab row.
 *
 * @param {object} entry - either a full WordEntry (`{word, language, senses}`)
 *   or a "flat" row from the vocab table (`{word, language, pos, glossary,
 *   example, explanation_primary, explanation_secondary, source}`).
 * @param {object} opts
 * @param {string} [opts.source]   - "wordnet" | "llm" | "user" (display badge)
 * @param {Array}  [opts.languages] - language catalog for labeling explanations
 * @param {string} [opts.explanationPrimary]   - language code for the primary explanation
 * @param {string} [opts.explanationSecondary] - language code for the secondary explanation
 * @param {boolean} [opts.compact]  - hide head/source badge; used in review card
 */
export function renderWordCard(entry, opts = {}) {
  const source = opts.source || entry.source || "";
  const compact = !!opts.compact;
  const senses = normalizeSenses(entry);
  const lang = entry.language || "";
  const headword = entry.word || "";

  const actions = opts.actions || "";
  const speakBtn = headword && lang
    ? `<button type="button" class="word-card__speak" data-action="speak" data-word="${escapeHtml(headword)}" data-lang="${escapeHtml(lang)}" aria-label="Pronounce ${escapeHtml(headword)}" title="Pronounce ${escapeHtml(headword)}">🔊</button>`
    : "";
  const head = compact ? "" : `
    <header class="word-card__head">
      <h2 class="word-card__headword">${escapeHtml(headword)}</h2>
      ${speakBtn}
      <span class="word-card__pos">${escapeHtml(lang)}</span>
      <span class="word-card__source">${sourceBadge(source)}</span>
      <span class="word-card__actions">${actions}</span>
    </header>
  `;

  const body = senses.length === 0
    ? `<div class="field__hint">No sense data available.</div>`
    : `<ol class="word-card__senses" style="list-style: none; padding-left: 0; margin: 0">
        ${senses.map((s, i) => renderSense(s, i + 1, opts)).join("")}
      </ol>`;

  // `bare` strips the .card chrome so the entry can nest inside another
  // card surface (e.g. the Review page's result card). Used by callers
  // that already wrap the word card in their own .card.
  const wrapperClass = opts.bare ? "word-card" : "card word-card";
  return `<article class="${wrapperClass}">${head}${body}</article>`;
}

/**
 * Build a WordEntry-like object from a single vocab_items row.
 */
export function entryFromVocabRow(row) {
  return {
    word: row.word,
    language: row.language,
    source: row.source,
    senses: [
      {
        pos: row.pos || "",
        source: row.source || "",
        definitions: [{ glossary: row.glossary || "", example: row.example || null }],
        explanations: {
          primary: row.explanation_primary || null,
          secondary: row.explanation_secondary || null,
        },
      },
    ],
  };
}

/**
 * Look up a richer WordEntry in the localStorage cache (filled by the
 * Dictionary page when it runs LLM lookups). Returns null if no entry.
 */
export function findCachedEntry(lang, word) {
  if (!lang || !word) return null;
  try {
    const raw = localStorage.getItem("langlearn:dict:v1");
    if (!raw) return null;
    const all = JSON.parse(raw);
    const k = `langlearn:dict:v1:${lang}:${word.toLowerCase()}`;
    const hit = all[k];
    if (!hit) return null;
    const bySource = hit.bySource || {};
    const sources = Object.keys(bySource);
    if (sources.length === 0) return null;
    let best = bySource[sources[0]];
    for (const s of sources) {
      if ((bySource[s].fetchedAt || 0) > (best.fetchedAt || 0)) best = bySource[s];
    }
    return best.entry || null;
  } catch (e) {
    return null;
  }
}

function normalizeSenses(entry) {
  if (Array.isArray(entry.senses) && entry.senses.length) return entry.senses;
  if (entry.glossary || entry.example) {
    return [{
      pos: entry.pos || "",
      source: entry.source || "",
      definitions: [{ glossary: entry.glossary || "", example: entry.example || null }],
      explanations: {
        primary: entry.explanation_primary || null,
        secondary: entry.explanation_secondary || null,
      },
    }];
  }
  return [];
}

function renderSense(sense, n, opts) {
  const defs = (sense.definitions || []).map((d) => {
    const ex = d.example
      ? `<div class="word-card__sense__example">"${escapeHtml(d.example)}"</div>`
      : "";
    return `<div>${n}. ${escapeHtml(d.glossary || "")}${ex}</div>`;
  }).join("");
  const expls = sense.explanations || {};
  const primaryLabel = explainLabel(opts.explanationPrimary);
  const secondaryLabel = explainLabel(opts.explanationSecondary);
  const explHtml = `
    <div class="word-card__sense__expl">
      ${expls.primary ? `<div class="expl-line"><span class="ll-tag">${escapeHtml(primaryLabel)}</span><span>${escapeHtml(expls.primary)}</span></div>` : ""}
      ${expls.secondary ? `<div class="expl-line"><span class="ll-tag">${escapeHtml(secondaryLabel)}</span><span>${escapeHtml(expls.secondary)}</span></div>` : ""}
    </div>
  `;
  const sourceBadgeInline = sense.source === "llm"
    ? ` <span class="badge badge--ai">AI</span>` : "";
  return `
    <li class="word-card__sense">
      <div class="word-card__sense__pos-line">
        <span class="word-card__pos">${escapeHtml(sense.pos || "—")}</span>${sourceBadgeInline}
      </div>
      <div class="word-card__sense__gloss">
        ${defs}
      </div>
      ${explHtml}
    </li>
  `;
}

function sourceBadge(source) {
  if (source === "wordnet") return `<span class="badge badge--builtin">WordNet</span>`;
  if (source === "llm") return `<span class="badge badge--ai">AI</span>`;
  return "";
}

function explainLabel(code) {
  return code || "—";
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