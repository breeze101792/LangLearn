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
//
// Parsing notes: the extract is requested with `exsectionformat=wiki`
// so section boundaries arrive as `== Language ==` and
// `=== Part of speech ===` markers. Every edition localizes its
// headings (`Español`, `Français`, `Haus (Deutsch)`, `日本語`, …), so
// language sections are matched with per-edition patterns instead of
// English names, and part-of-speech groups are detected structurally
// from the `===` level regardless of language.
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

// Patterns matched against `== ... ==` heading text to find this
// language's section on its own edition. One entry may use several
// section kinds (e.g. ja splits kanji / hiragana / katakana entries).
const LANG_SECTION_PATTERNS = {
  en: [/^English$/],
  es: [/^Español$/],
  fr: [/^Français$/],
  de: [/\(Deutsch\)$/],                              // "== Haus (Deutsch) =="
  ja: [/^日本語$/, /^漢字$/, /^ひらがな$/, /^カタカナ$/],
  pt: [/^Português$/],
  zh: [/^漢語$/, /^汉语$/],
};

// `=== ... ===` sections that carry metadata, not definitions.
// Localized across the supported editions (both script variants kept
// where editions mix them).
const SKIP_SECTIONS = new Set([
  // etymology
  "etymology", "etimología", "étymologie", "etymologie",
  "etimologia", "語源", "字源", "詞源", "词源",
  // pronunciation
  "pronunciation", "pronunciación", "prononciation", "aussprache",
  "pronúncia", "發音", "发音", "発音", "讀音", "读音",
  // usage / quotes
  "usage notes", "quotations", "notes", "notas", "remarques",
  "anmerkungen", "beispiele", "用例", "註釋", "注释",
  // relations / navigation
  "alternative forms", "variant forms", "obsolete spellings",
  "synonyms", "antonyms", "hyponyms", "hypernyms", "coordinate terms",
  "translations", "derived terms", "related terms", "descendants",
  "see also", "anagrams", "references", "external links",
  "traducciones", "términos derivados", "palabras relacionadas",
  "descendientes", "véase también", "anagramas", "referencias",
  "enlaces externos", "compuestos", "derivados",
  "traductions", "termes dérivés", "mots liés", "voir aussi",
  "expressions", "dérivés", "apparentés", "paronymes",
  "übersetzungen", "sinnverwandte wörter", "gegenwörter",
  "unterbegriffe", "oberbegriffe", "siehe auch", "anagramme",
  "quellen", "weblinks", "redewendungen", "sprichwörter",
  "wortbildung", "abgeleitete begriffe",
  "訳語", "派生語", "関連語", "成句", "複合語", "参考", "外部リンク",
  "翻譯", "翻译", "衍生詞", "相關詞", "參見", "異序詞", "來源",
  "參考資料", "外部連結", "組詞", "组词", "複合詞", "复合词",
  "替代寫法", "替代写法", "替換寫法",
  "sinónimos", "sinônimos", "antónimos", "antônimos", "ver também",
  "expressões", "derivações", "locuciones", "refranes", "modismos",
  "información adicional", "forma flexiva", "forma verbal",
  "forma sustantiva", "forma adjetiva",
]);

const MAX_SENSES = 6;
const MAX_GLOSSARY = 1000;
const TIMEOUT_MS = 10_000;

// Etymology sections get special treatment: on several editions
// (es, fr) the actual part-of-speech sections nest *inside* them,
// so their subtrees must stay parseable — only their direct prose
// is dropped.
const ETYMOLOGY_SECTIONS = new Set([
  "etymology", "etimología", "étymologie", "etymologie",
  "etimologia", "語源", "字源", "詞源", "词源",
]);

// Browser User-Agent is set automatically by the runtime; the Wiktionary
// policy applies to the upstream *server* only, not to client requests.

function urlFor(lang, word) {
  const host = WIKTIONARY_HOST[lang] || `${lang}.wiktionary.org`;
  const params = new URLSearchParams({
    action: "query",
    prop: "extracts",
    explaintext: "1",
    exsectionformat: "wiki",
    redirects: "1",
    titles: word,
    // Required on anonymous cross-origin calls: without it Wikimedia
    // sends no Access-Control-Allow-Origin header and the browser
    // blocks the response ("NetworkError when attempting to fetch").
    origin: "*",
    format: "json",
  });
  return `https://${host}/w/api.php?${params.toString()}`;
}

/**
 * Split an extract line into `{ level, title }` for wiki-format
 * headings (`= X =`, `== X ==`, `=== X ===`) or null for ordinary
 * lines. pt.wiktionary starts language sections at a single `=`.
 */
function parseHeading(line) {
  const m = line.match(/^(={1,6})\s*(.*?)\s*\1\s*$/);
  if (!m) return null;
  return { level: m[1].length, title: m[2] };
}

function isLanguageHeading(title, lang) {
  const patterns = LANG_SECTION_PATTERNS[lang];
  if (!patterns) return false;
  return patterns.some((re) => re.test(title));
}

function isSkippedSection(title) {
  // Editions number repeated sections ("Etimología 1", "Étymologie 2").
  const normalized = title.toLowerCase().replace(/\s+\d+$/, "");
  return SKIP_SECTIONS.has(normalized);
}

function isEtymologySection(title) {
  const normalized = title.toLowerCase().replace(/\s+\d+$/, "");
  return ETYMOLOGY_SECTIONS.has(normalized);
}

/**
 * Find the section of the Wiktionary extract that corresponds to
 * `lang`. With wiki-format headings, the language section runs from
 * its `== Name ==` marker to the next marker at that same level.
 * Editions disagree on the top level: most use level 2, but pt nests
 * language sections at level 1 (`= Português =`). Returns
 * `{ lines, langLevel }` or null if the language isn't present.
 */
function sliceLanguageSection(extract, lang) {
  if (!extract) return null;
  const lines = extract.split("\n");
  let start = -1;
  let langLevel = 2;
  for (let i = 0; i < lines.length; i++) {
    const head = parseHeading(lines[i]);
    if (head && head.level <= 3 && isLanguageHeading(head.title, lang)) {
      start = i + 1;
      langLevel = head.level;
      break;
    }
  }
  if (start < 0) return null;
  let end = lines.length;
  for (let j = start; j < lines.length; j++) {
    const head = parseHeading(lines[j]);
    if (head && head.level === langLevel) { end = j; break; }
  }
  return { lines: lines.slice(start, end), langLevel };
}

/**
 * Within a single-language section, every heading deeper than the
 * language level starts a candidate sense group unless its title is a
 * metadata section (etymology, pronunciation, translations, …).
 * Nesting depth varies per edition (`=== Noun ===` on en,
 * `==== Sustantivo femenino ====`, `== Substantivo ==` on pt), so no
 * single "POS level" is assumed — unknown containers merely add one
 * bounded group instead of hiding the real definitions. Returns a
 * list of `Sense` objects in the shape consumed by the dict-card.
 */
function parseSection(sectionLines, langLevel, word) {
  const senses = [];
  let pos = null;
  let defs = [];
  // While inside a skipped non-etymology metadata section
  // (Pronunciation, Translations, …) every deeper heading is
  // suppressed too — its subsections (conjugation tables,
  // derived-term lists) are not definitions. Etymology subtrees stay
  // parseable because several editions nest POS inside them.
  let skipDepth = 0;

  const flush = () => {
    if (pos && defs.length > 0) senses.push(makeSense(pos, defs, word));
    pos = null;
    defs = [];
  };

  for (const raw of sectionLines) {
    const stripped = raw.trim();
    if (!stripped) continue;
    const head = parseHeading(stripped);
    if (head) {
      if (head.level <= langLevel) {          // next language started
        flush();
        skipDepth = 0;
        continue;
      }
      if (skipDepth && head.level > skipDepth) continue;   // suppressed
      skipDepth = 0;
      flush();
      if (isSkippedSection(head.title)) {
        if (!isEtymologySection(head.title)) {
          skipDepth = head.level;
        }
      } else {
        pos = head.title.toLowerCase();
      }
      continue;
    }
    if (pos !== null) defs.push(stripped);   // pre-POS prose stays ignored
  }
  flush();
  return senses;
}

function makeSense(pos, defLines, word) {
  const defs = [];
  for (const line of defLines) {
    const text = stripDefinitionLine(line, word);
    if (!text) continue;
    defs.push({ glossary: text.slice(0, MAX_GLOSSARY), example: null });
    if (defs.length >= MAX_SENSES) break;
  }
  return { pos, source: "wiktionary", definitions: defs };
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Clean one raw definition line. Editions prefix glosses with list
 * numbering ("1 Vivienda"), bracket refs ("[1] Haus") or bullets,
 * all of which we strip before display. Metadata lines that survive
 * section filtering (field labels like "Bedeutungen:", pronunciation
 * lines with IPA, headword inflection rows) are dropped outright.
 */
function stripDefinitionLine(line, word) {
  let text = line.trim();
  text = text.replace(/^\(?(\d{1,2}|[a-z])\)?[.)]\s+/, "");   // "1." "(2)" "a)"
  text = text.replace(/^\d{1,2}\s+/, "");                     // "1 Vivienda"
  text = text.replace(/^\[\d+\]\s*/, "");                     // "[1] …"
  text = text.replace(/^[-*•]+\s*/, "");                      // bullet leftovers
  text = text.replace(/\s*\[\d+\]/g, "").trim();              // trailing refs
  text = text.replace(/\s*\[edit\]\s*/g, "").trim();
  if (!text || text === "—" || text === "-") return "";
  if (text.endsWith(":")) return "";                          // "Bedeutungen:"
  if (/\\[^\\]+\\/.test(text)) return "";                     // IPA: \me.zɔ̃\
  if (/^IPA\b/i.test(text)) return "";                        // de: IPA: [haʊ̯s]
  if (text.includes("¦")) return "";                          // "casa ¦ plural: casas"
  if (/,\s*Plural:/i.test(text)) return "";                   // "Haus, Plural: Häu·ser"
  if (word) {
    // Pure headword inflection rows: "set (third-person singular …)",
    // "hospital (countable and uncountable, plural hospitals)".
    const inflected = new RegExp(
      `^${escapeRegExp(word)}\\s*\\(.*\\)$`, "is");
    if (inflected.test(text) &&
        /plural|singular|participle|past|present|comparative|superlative|countable|uncountable|feminine|masculine|neuter/i.test(text)) {
      return "";
    }
  }
  return text;
}

/**
 * Build a fresh empty entry. Used when the language isn't supported,
 * the word isn't in the edition, or the parser sees no usable section.
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
  if (!pages) return null;
  const page = Object.values(pages)[0];
  if (!page || "missing" in page) return null;   // absent title → clean miss
  const extract = page.extract || "";
  if (!extract.trim()) return null;
  return { extract };
}

/**
 * Browser-side Wiktionary lookup. Returns:
 *   - `{ entry: WordEntry, source: "wiktionary", word, lang }` on hit
 *   - `{ entry: null, source: "", ... }` on miss (never throws)
 *   - throws only on genuine network failure (caller decides)
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
    if (!fetched) return emptyEntry(word, lang);
    const section = sliceLanguageSection(fetched.extract, lang);
    if (!section) return emptyEntry(word, lang);
    const senses = parseSection(section.lines, section.langLevel, word);
    if (senses.length === 0) return emptyEntry(word, lang);
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
