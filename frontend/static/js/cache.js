// Dictionary result cache in localStorage. LRU by language, max 1000 words per lang.
// Each entry: map of provider -> { lang, word, entry, source, fetchedAt } so a
// word's WordNet and LLM results don't overwrite each other.
// Loaded lazily; first time we use localStorage we might be disabled (private mode).
//
// Key namespace: langlearn:dict:v1:<lang>:<word>.

const NAMESPACE = "langlearn:dict:v1";
const MAX_PER_LANG = 1000;

function keyFor(lang, word) {
  return `${NAMESPACE}:${lang}:${word.toLowerCase()}`;
}

function readAll() {
  try {
    const raw = localStorage.getItem(NAMESPACE);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function writeAll(obj) {
  try {
    localStorage.setItem(NAMESPACE, JSON.stringify(obj));
  } catch (e) {
    // full or unavailable — drop oldest half
    const keys = Object.keys(obj);
    if (keys.length > MAX_PER_LANG) {
      const trimmed = {};
      keys.slice(Math.floor(keys.length / 2)).forEach((k) => (trimmed[k] = obj[k]));
      try {
        localStorage.setItem(NAMESPACE, JSON.stringify(trimmed));
      } catch (e2) { /* give up */ }
    }
  }
}

export const cache = {
  /**
   * Return the cached entry for `lang`+`word`. When `source` is given, only an
   * entry from exactly that provider counts; otherwise return the most recent
   * entry.
   */
  get(lang, word, source) {
    const k = keyFor(lang, word);
    const all = readAll();
    const hit = all[k];
    if (!hit) return null;
    const bySource = hit.bySource || {};
    if (source) return bySource[source] || null;
    // No provider requested: return the most recently stored result.
    const sources = Object.keys(bySource);
    if (sources.length === 0) return null;
    let best = bySource[sources[0]];
    for (const s of sources) {
      if ((bySource[s].fetchedAt || 0) > (best.fetchedAt || 0)) best = bySource[s];
    }
    return best;
  },
  /**
   * Walk `sources` in order and return the first provider that has an entry
   * cached for `lang`+`word`. Used for fresh search-box lookups so the result
   * resets to the leading provider of the chain on every new word.
   */
  getInChain(lang, word, sources) {
    for (const source of sources || []) {
      const hit = this.get(lang, word, source);
      if (hit) return hit;
    }
    return null;
  },
  set(lang, word, source, payload) {
    const k = keyFor(lang, word);
    const all = readAll();
    // enforce per-lang cap
    const prefix = `${NAMESPACE}:${lang}:`;
    const sameLang = Object.keys(all).filter((x) => x.startsWith(prefix));
    if (sameLang.length >= MAX_PER_LANG) {
      sameLang.slice(0, sameLang.length - MAX_PER_LANG + 1).forEach((x) => delete all[x]);
    }
    const prev = all[k] || { bySource: {} };
    prev.bySource = prev.bySource || {};
    prev.bySource[source] = { ...payload, source, fetchedAt: Date.now() };
    all[k] = prev;
    writeAll(all);
  },
  clearLang(lang) {
    const all = readAll();
    const prefix = `${NAMESPACE}:${lang}:`;
    Object.keys(all).filter((x) => x.startsWith(prefix)).forEach((x) => delete all[x]);
    writeAll(all);
  },
  clearAll() {
    try { localStorage.removeItem(NAMESPACE); } catch (e) { /* */ }
  },
};