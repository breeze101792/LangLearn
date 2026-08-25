// Reusable word card. Renders one WordEntry (or a single-sense equivalent).
// Used by Dictionary (full multi-sense card) and Review (single-sense reveal).
//
// Returns an HTML string suitable for innerHTML insertion. All user-provided
// strings go through `escapeHtml`.

const PRIMARY_LANG_KEY = "primary";
const SECONDARY_LANG_KEY = "secondary";

// Human-readable source labels for the head badge. The "Wiktionary"
// label is the brand name; the language code is shown separately in
// the meta row so the user sees which edition produced the result.
const SOURCE_LABEL = {
  wordnet: "WordNet",
  wiktionary: "Wiktionary",
  llm: "AI",
};

/**
 * Render a word-card for one WordEntry or one vocab row.
 *
 * @param {object} entry - either a full WordEntry (`{word, language, senses}`)
 *   or a "flat" row from the vocab table (`{word, language, pos, glossary,
 *   example, explanation_primary, explanation_secondary, source}`).
 * @param {object} opts
 * @param {string} [opts.source]   - "wordnet" | "wiktionary" | "llm" (display badge)
 * @param {Array}  [opts.languages] - language catalog for labeling explanations
 * @param {string} [opts.explanationPrimary]   - language code for the primary explanation
 * @param {string} [opts.explanationSecondary] - language code for the secondary explanation
 * @param {boolean} [opts.compact]  - hide head/source badge; used in review card
 */
export function renderWordCard(entry, opts = {}) {
  const source = opts.source || entry.source || "";
  const compact = !!opts.compact;
  const lang = entry.language || "";
  const headword = entry.word || "";

  // One "row" per definition (POS + text + optional example). Each
  // row will be numbered, restarting at 1 every time the POS changes.
  // This matches how learners expect to see a dictionary entry:
  // "noun 1, noun 2, verb 1" rather than "noun 1, noun 2, verb 3".
  const rows = expandRows(entry);
  const senses = normalizeSenses(entry);

  const actions = opts.actions || "";
  const speakBtn = headword && lang
    ? `<button type="button" class="word-card__speak" data-action="speak" data-word="${escapeHtml(headword)}" data-lang="${escapeHtml(lang)}" aria-label="Pronounce ${escapeHtml(headword)}" title="Pronounce ${escapeHtml(headword)}">🔊</button>`
    : "";
  const head = compact ? "" : `
    <header class="word-card__head">
      <div class="word-card__headline">
        <h2 class="word-card__headword">${escapeHtml(headword)}</h2>
        ${speakBtn}
      </div>
      ${renderMeta(source, lang)}
      ${actions ? `<div class="word-card__actions">${actions}</div>` : ""}
    </header>
  `;

  const body = rows.length === 0
    ? `<div class="field__hint">No sense data available.</div>`
    : `<div class="word-card__senses">${renderRowsGroupedByPos(rows, opts)}</div>`;

  // `bare` strips the .card chrome so the entry can nest inside another
  // card surface (e.g. the Review page's result card). Used by callers
  // that already wrap the word card in their own .card.
  const wrapperClass = opts.bare ? "word-card" : "card word-card";
  return `<article class="${wrapperClass}">${head}${body}</article>`;
}

function renderMeta(source, lang) {
  const sourceModifier = SOURCE_LABEL[source] ? source : "default";
  const sourceBadge = source
    ? `<span class="word-card__source-badge word-card__source-badge--${escapeHtml(sourceModifier)}">${escapeHtml(SOURCE_LABEL[source] || source)}</span>`
    : "";
  const langChip = lang
    ? `<span class="word-card__lang">${escapeHtml(lang)}</span>`
    : "";
  if (!sourceBadge && !langChip) return "";
  return `<div class="word-card__meta">${sourceBadge}${langChip}</div>`;
}

/**
 * Flatten the (senses × definitions) structure into one row per
 * definition. Each row carries its POS, glossary, and example. The
 * renderer numbers rows per-POS. Empty glossaries are dropped so a
 * malformed entry doesn't render an empty numbered line.
 */
function expandRows(entry) {
  const senses = normalizeSenses(entry);
  const out = [];
  for (const sense of senses) {
    const pos = (sense.pos || "—").toLowerCase();
    const defs = sense.definitions || [];
    if (defs.length === 0) continue;
    for (const d of defs) {
      const glossary = (d.glossary || "").trim();
      if (!glossary) continue;
      out.push({
        pos,
        posRaw: sense.pos || "—",
        glossary,
        example: typeof d.example === "string" && d.example.trim() ? d.example.trim() : null,
        explanations: sense.explanations || null,
        source: sense.source || "",
      });
    }
  }
  return out;
}

/**
 * Render rows grouped under their POS. Numbering restarts at 1
 * every time the POS changes, so a card with two noun senses and
 * one verb sense renders "1, 2" under NOUN and "1" under VERB.
 */
function renderRowsGroupedByPos(rows, opts) {
  const groups = [];
  let cur = null;
  for (const row of rows) {
    if (!cur || cur.pos !== row.pos) {
      cur = { pos: row.pos, posRaw: row.posRaw, rows: [] };
      groups.push(cur);
    }
    cur.rows.push(row);
  }
  return groups.map((g) => renderPosGroup(g, opts)).join("");
}

function renderPosGroup(group, opts) {
  const heading = `
    <h3 class="word-card__pos-heading">
      <span class="word-card__pos-tag">${escapeHtml(group.posRaw || "—")}</span>
    </h3>
  `;
  const items = group.rows.map((row, idx) => renderRow(row, idx + 1, opts)).join("");
  return `<section class="word-card__pos-group" data-pos="${escapeHtml(group.pos)}">${heading}<ol class="word-card__defs">${items}</ol></section>`;
}

function renderRow(row, n, opts) {
  const exampleHtml = row.example
    ? `<blockquote class="word-card__example">"${escapeHtml(row.example)}"</blockquote>`
    : "";
  const expls = row.explanations || {};
  const primaryLabel = explainLabel(opts.explanationPrimary);
  const secondaryLabel = explainLabel(opts.explanationSecondary);
  const explHtml = `
    <div class="word-card__sense__expl">
      ${expls.primary ? `<div class="expl-line"><span class="ll-tag">${escapeHtml(primaryLabel)}</span><span>${escapeHtml(expls.primary)}</span></div>` : ""}
      ${expls.secondary ? `<div class="expl-line"><span class="ll-tag">${escapeHtml(secondaryLabel)}</span><span>${escapeHtml(expls.secondary)}</span></div>` : ""}
    </div>
  `;
  return `
    <li class="word-card__def">
      <span class="word-card__def__num" aria-hidden="true">${n}.</span>
      <div class="word-card__def__body">
        <p class="word-card__def__gloss">${escapeHtml(row.glossary)}</p>
        ${exampleHtml}
        ${explHtml}
      </div>
    </li>
  `;
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