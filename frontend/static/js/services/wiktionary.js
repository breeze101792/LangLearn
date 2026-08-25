// Client-side Wiktionary dictionary service.
//
// Each supported language maps to a `xx.wiktionary.org` edition that
// returns plain-text definitions. The browser calls the Wiktionary
// API directly; the server is never involved in the network request.
// Results are cached in `localStorage` (via the shared `cache.js`)
// under the source name "wiktionary" so subsequent lookups for the
// same word are instant and offline.
//
// The service exports:
//   - `lookup(word, lang)` — returns a `WordEntry` shape compatible
//     with the rest of the app, or `{ entry: null }` on miss.
//   - `clearCachedWord(word, lang)` — drops a single cached entry
//     so the next lookup re-fetches (used by the dictionary page's
//     "Refresh" affordance when the user wants an updated definition).
//
// The Wiktionary API is rate-limited; the parser is bounded to the
// first few senses per part-of-speech to keep responses small and
// rendering fast.

const WIKTIONARY_HOST = {
  en: "en.wiktionary.org",
  es: "es.wiktionary.org",
  fr: "fr.wiktionary.org",
  de: "de.wiktionary.org",
  ja: "ja.wiktionary.org",
  pt: "pt.wiktionary.org",
  zh: "zh.wiktionary.org",
};

const POS_HEADINGS = new Set([
  "noun", "verb", "adjective", "adverb",
  "interjection", "preposition", "conjunction", "pronoun",
  "determiner", "article", "particle", "numeral",
  "proper noun", "phrase", "prefix", "suffix",
]);

const MAX_SENSES = 6;
const MAX_GLOSSARY = 1000;
const TIMEOUT_MS = 10_000;

// Browser User-Agent is set automatically by the runtime; the Wiktionary
// policy applies to the upstream *server* only, not to client requests.

function urlFor(lang, word) {
  const host = WIKTIONARY_HOST[lang] || `${lang}.wiktionary.org`;
  const params = new URLSearchParams({
    action: "query",
    prop: "extracts",
    explaintext: "1",
    exsectionformat: "plain",
    redirects: "1",
    titles: word,
    format: "json",
  });
  return `https://${host}/w/api.php?${params.toString()}`;
}

function displayNameFor(lang) {
  // The Wiktionary response section headers use these display names
  // (e.g. "English", "Spanish"). Match exactly to slice the right
  // section out of the multilingual entry.
  return ({
    en: "English",
    es: "Spanish",
    fr: "French",
    de: "German",
    ja: "Japanese",
    pt: "Portuguese",
    zh: "Chinese",
  })[lang] || lang;
}

/**
 * Find the section of the Wiktionary extract that corresponds to
 * `lang`. The extract is a multilingual entry: each language gets a
 * heading line whose stripped value is its display name. Returns the
 * section text or null if the language isn't in the entry.
 */
function sliceLanguageSection(extract, lang) {
  const target = displayNameFor(lang).toLowerCase();
  const lines = extract.split("\n");
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim().toLowerCase() === target) { start = i + 1; break; }
  }
  if (start < 0) return null;
  // Walk to the next language heading. A language heading is a
  // non-empty line whose lowercase value is any of the other
  // display names we know about.
  const otherNames = new Set(
    Object.values(WIKTIONARY_HOST ? {} : {})
      .concat(Object.values({
        en: "English", es: "Spanish", fr: "French", de: "German",
        ja: "Japanese", pt: "Portuguese", zh: "Chinese",
      })).map((s) => s.toLowerCase()).filter((n) => n !== target)
  );
  for (let j = start; j < lines.length; j++) {
    const stripped = lines[j].trim();
    if (stripped && otherNames.has(stripped.toLowerCase())) {
      return lines.slice(start, j).join("\n");
    }
  }
  return lines.slice(start).join("\n");
}

/**
 * Within a single-language section, split by part-of-speech headings
 * and pull numbered definitions out of each. Returns a list of
 * `Sense` objects in the shape consumed by the dict-card.
 */
function parseSection(section) {
  const senses = [];
  const lines = section.split("\n");
  let pos = null;
  let defs = [];
  for (const raw of lines) {
    const stripped = raw.trim();
    if (!stripped) continue;
    if (POS_HEADINGS.has(stripped.toLowerCase())) {
      if (pos) senses.push(makeSense(pos, defs));
      pos = stripped.toLowerCase();
      defs = [];
      continue;
    }
    if (pos === null) continue;       // pre-POS metadata (Pronunciation, Etymology, ...)
    defs.push(stripped);
  }
  if (pos) senses.push(makeSense(pos, defs));
  return senses;
}

function makeSense(pos, defLines) {
  const defs = [];
  for (const line of defLines) {
    const text = stripDefinitionLine(line);
    if (!text) continue;
    defs.push({ glossary: text.slice(0, MAX_GLOSSARY), example: null });
    if (defs.length >= MAX_SENSES) break;
  }
  return { pos, source: "wiktionary", definitions: defs };
}

/**
 * Convert a raw definition line (e.g. "1 A challenge, trial." or
 * " (academia) An examination.") into a clean definition text. The
 * Wiktionary plain-text response sometimes leaves bracket markers
 * ("[1]") for citations, which we strip.
 */
function stripDefinitionLine(line) {
  // Match leading numeric ("1", "2."), parenthetical qualifier
  // ("(academia)"), or just whitespace. Capture the rest.
  const m = line.match(/^\s*(?:\d+\.|\(\d+\)|\([\w ,/.-]+\)|)\s*(.*\S)\s*$/);
  if (!m) return "";
  let text = m[1];
  if (!text || text === "—" || text === "-") return "";
  text = text.replace(/\s*\[\d+\]/g, "").trim();
  text = text.replace(/\s*\[edit\]\s*/g, "").trim();
  return text;
}

/**
 * Build a fresh empty entry. Used when the language isn't supported,
 * the network is down, or the parser sees no usable section.
 */
function emptyEntry(word, lang) {
  return {
    entry: null,
    word,
    lang,
    source: "",
    error: null,
  };
}

async function fetchFromWiktionary(word, lang, signal) {
  const url = urlFor(lang, word);
  const resp = await fetch(url, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) return null;
  const data = await resp.json().catch(() => null);
  if (!data) return null;
  const pages = data.query && data.query.pages;
  if (!pages) return [];
  const page = Object.values(pages)[0];
  if (!page || "missing" in page) return [];
  const extract = page.extract || "";
  if (!extract.trim()) return [];
  return { extract, lang };
}

/**
 * Browser-side Wiktionary lookup. Returns:
 *   - `{ entry: WordEntry, source: "wiktionary", word, lang }` on hit
 *   - `{ entry: null, source: "", ... }` on miss
 *   - throws on network failure (caller catches and continues chain)
 */
export async function lookup(word, lang, options = {}) {
  if (!word || !lang || !WIKTIONARY_HOST[lang]) {
    return { entry: null, source: "", word, lang, error: null };
  }
  const cache = options.cache;          // injected for tests; real call uses import
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const fetched = await fetchFromWiktionary(word, lang, controller.signal);
    clearTimeout(timeoutId);
    if (!fetched) return { entry: null, source: "", word, lang, error: null };
    const section = sliceLanguageSection(fetched.extract, lang);
    if (!section) return { entry: null, source: "", word, lang, error: null };
    const senses = parseSection(section);
    if (senses.length === 0) {
      return { entry: null, source: "", word, lang, error: null };
    }
    const entry = {
      word,
      language: lang,
      source: "wiktionary",
      senses,
    };
    return { entry, source: "wiktionary", word, lang, error: null };
  } catch (e) {
    clearTimeout(timeoutId);
    if (e && e.name === "AbortError") {
      return { entry: null, source: "", word, lang, error: "timeout" };
    }
    throw e;                             // surface network failure to caller
  }
}

export const WIKTIONARY_LANGS = Object.keys(WIKTIONARY_HOST);
