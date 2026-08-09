// LLM dictionary cache in localStorage. LRU by language, max 1000 entries per lang.
// Each entry: { key: "<lang>:<word>", lang, word, entry, source, fetchedAt }.
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
  get(lang, word) {
    const k = keyFor(lang, word);
    const all = readAll();
    return all[k] || null;
  },
  set(lang, word, payload) {
    const k = keyFor(lang, word);
    const all = readAll();
    // enforce per-lang cap
    const prefix = `${NAMESPACE}:${lang}:`;
    const sameLang = Object.keys(all).filter((x) => x.startsWith(prefix));
    if (sameLang.length >= MAX_PER_LANG) {
      sameLang.slice(0, sameLang.length - MAX_PER_LANG + 1).forEach((x) => delete all[x]);
    }
    all[k] = { ...payload, fetchedAt: Date.now() };
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