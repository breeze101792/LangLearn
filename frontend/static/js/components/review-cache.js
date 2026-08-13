// Pure helper used by the Review page to pick the most-recently-fetched
// cached dictionary record for a word. The record includes the provider
// `.source` so the review page can highlight the matching segment in the
// provider switcher — `findCachedEntry` in word-card.js drops that field.
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

export function findCachedRecord(lang, word, storage) {
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
  let best = bySource[sources[0]];
  for (const s of sources) {
    if ((bySource[s].fetchedAt || 0) > (best.fetchedAt || 0)) best = bySource[s];
  }
  if (!best.entry) return null;
  return { entry: best.entry, source: best.source || sources[0] };
}
