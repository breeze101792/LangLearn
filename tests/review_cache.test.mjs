// Unit tests for findCachedRecord (frontend/static/js/components/review-cache.js).
// Run with:
//   node tests/review_cache.test.mjs
// Exits 0 on pass, 1 on first failure.

import { findCachedRecord } from "../frontend/static/js/components/review-cache.js";

let failures = 0;
let passed = 0;

function test(name, fn) {
  try {
    fn();
    console.log("ok  -", name);
    passed++;
  } catch (e) {
    console.log("FAIL -", name);
    console.log("       ", e.message);
    failures++;
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

function fakeStorage(seed = {}) {
  const data = { ...seed };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
  };
}

function seedWord(storage, lang, word, bySource) {
  const k = `langlearn:dict:v1:${lang}:${word.toLowerCase()}`;
  const all = {};
  all[k] = { bySource };
  storage.setItem("langlearn:dict:v1", JSON.stringify(all));
}

test("returns null when storage is empty", () => {
  const r = findCachedRecord("en", "cat", fakeStorage());
  assert(r === null, "expected null");
});

test("returns null when word is not cached", () => {
  const r = findCachedRecord("en", "cat", fakeStorage());
  assert(r === null, "expected null");
});

test("returns null for empty inputs", () => {
  const s = fakeStorage();
  seedWord(s, "en", "cat", { wordnet: { entry: { word: "cat" }, source: "wordnet", fetchedAt: 1 } });
  assert(findCachedRecord("", "cat", s) === null, "empty lang");
  assert(findCachedRecord("en", "", s) === null, "empty word");
  assert(findCachedRecord(null, "cat", s) === null, "null lang");
  assert(findCachedRecord("en", null, s) === null, "null word");
});

test("returns the only cached record with entry and source", () => {
  const s = fakeStorage();
  const entry = { word: "cat", senses: [] };
  seedWord(s, "en", "cat", { wordnet: { entry, source: "wordnet", fetchedAt: 100 } });
  const r = findCachedRecord("en", "cat", s);
  assert(r !== null, "expected a record");
  assert(r.entry && r.entry.word === "cat", "entry should be present with the right word");
  assert(r.source === "wordnet", "source should be wordnet");
});

test("REGRESSION: returns .source alongside .entry so the switcher can highlight the right provider", () => {
  // The original bug: findCachedEntry (in word-card.js) returned only the
  // entry, dropping the .source. The review page then fell back to the
  // vocab row's stale .source and the switcher highlighted the wrong
  // segment. findCachedRecord must return both.
  const s = fakeStorage();
  seedWord(s, "en", "run", {
    wordnet: { entry: { word: "run", fromWordnet: true }, source: "wordnet", fetchedAt: 50 },
    llm:     { entry: { word: "run", fromLlm: true },     source: "llm",      fetchedAt: 200 },
  });
  const r = findCachedRecord("en", "run", s);
  assert(r !== null, "expected a record");
  assert(r.entry && r.entry.fromLlm === true, "should pick the most recent entry (llm)");
  assert(r.source === "llm", "source must be llm so the switcher can highlight it");
});

test("picks the most recently fetched record when multiple are cached", () => {
  const s = fakeStorage();
  seedWord(s, "en", "dog", {
    wordnet: { entry: { word: "dog", fromWordnet: true }, source: "wordnet", fetchedAt: 1000 },
    llm:     { entry: { word: "dog", fromLlm: true },     source: "llm",      fetchedAt: 500 },
  });
  const r = findCachedRecord("en", "dog", s);
  assert(r.entry && r.entry.fromWordnet === true, "should pick the entry with newer fetchedAt (wordnet)");
  assert(r.source === "wordnet", "source should match the picked entry");
});

test("falls back to the bySource key when record has no .source field", () => {
  // Defensive: older cache entries may not carry a per-record .source.
  // We should still hand back the bySource key so the switcher can pick
  // a segment.
  const s = fakeStorage();
  seedWord(s, "en", "fox", {
    wordnet: { entry: { word: "fox" }, fetchedAt: 1 },
  });
  const r = findCachedRecord("en", "fox", s);
  assert(r !== null);
  assert(r.source === "wordnet", "fallback to bySource key when .source missing");
});

test("returns null when no entry under any provider has a .entry", () => {
  const s = fakeStorage();
  seedWord(s, "en", "owl", {
    wordnet: { source: "wordnet", fetchedAt: 1 },
  });
  assert(findCachedRecord("en", "owl", s) === null, "no entry → null");
});

test("returns null when bySource is empty", () => {
  const s = fakeStorage();
  seedWord(s, "en", "ant", {});
  assert(findCachedRecord("en", "ant", s) === null, "empty bySource → null");
});

test("returns null when storage has malformed JSON", () => {
  const s = fakeStorage({ "langlearn:dict:v1": "not json" });
  assert(findCachedRecord("en", "cat", s) === null, "malformed JSON → null");
});

test("returns null when storage.getItem throws (e.g. private mode)", () => {
  const s = { getItem: () => { throw new Error("blocked"); } };
  assert(findCachedRecord("en", "cat", s) === null, "throwing storage → null");
});

test("looks up the word case-insensitively (matches the cache key shape)", () => {
  const s = fakeStorage();
  seedWord(s, "en", "Cat", { wordnet: { entry: { word: "Cat" }, source: "wordnet", fetchedAt: 1 } });
  const r = findCachedRecord("en", "CAT", s);
  assert(r !== null, "should find the lowercased key");
  assert(r.entry && r.entry.word === "Cat");
  assert(r.source === "wordnet");
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
