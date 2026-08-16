// Pure helper used by the Review page to pick a cached dictionary record
// for a word. The record includes the provider `.source` so the review
// page can highlight the matching segment in the provider switcher —
// `findCachedEntry` in word-card.js drops that field.
//
// Pass `chainOrder` (an ordered list of provider names) to walk the
// chain in order and return the first provider with a cached entry, the
// same way the Dictionary page does. Omit it to fall back to the
// most-recently-fetched record.
//
// The function is pure: it only reads from the `langlearn:dict:v1`
// localStorage key. It is loaded by the Node test harness
// (tests/review_cache.test.mjs) without a browser.
//
// `storage` defaults to `globalThis.localStorage` so callers in the
// browser pass nothing; tests pass a fake.

const NAMESPACE = "langlearn:dict:v1";

function keyFor(lang, word) {
  return `${NAMESPACE}:${lang}:${word.toLowerCase()}`;
}

// `storage` defaults to `globalThis.localStorage` so callers in the
// browser pass nothing; tests pass a fake.
//
// `chainOrder` is an optional ordered list of provider names (as built by
// switcherProvidersFor on the Review page). When supplied, we walk it in
// order and return the first provider that has a cached entry for the word
// — this mirrors the Dictionary page's behaviour (cache.getInChain), so
// the review card highlights the chain's leading provider instead of the
// most-recently-fetched one. When omitted, we fall back to the most recent.
export function findCachedRecord(lang, word, storage, chainOrder) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!lang || !word || !store) return null;
  let raw;
  try {
    raw = store.getItem(NAMESPACE);
  } catch (e) {
    return null;
  }
  if (!raw) return null;
  let all;
  try {
    all = JSON.parse(raw);
  } catch (e) {
    return null;
  }
  const k = keyFor(lang, word);
  const hit = all[k];
  if (!hit || !hit.bySource) return null;
  const bySource = hit.bySource;
  const sources = Object.keys(bySource);
  if (sources.length === 0) return null;

  let best = null;
  if (Array.isArray(chainOrder) && chainOrder.length) {
    for (const name of chainOrder) {
      const rec = bySource[name];
      if (rec && rec.entry) { best = rec; break; }
    }
  }
  if (!best) {
    best = bySource[sources[0]];
    for (const s of sources) {
      if ((bySource[s].fetchedAt || 0) > (best.fetchedAt || 0)) best = bySource[s];
    }
  }
  if (!best || !best.entry) return null;
  return { entry: best.entry, source: best.source || sources[0] };
}
