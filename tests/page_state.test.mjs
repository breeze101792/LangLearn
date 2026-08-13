// Unit tests for the page-state save/restore helper
// (frontend/static/js/components/page-state.js).
//
// The helper uses sessionStorage and a global stash key, both of
// which the test setup overrides so the tests can run in plain Node.
//
// Run with:
//   node tests/page_state.test.mjs
// Exits 0 on pass, 1 on first failure.

import {
  savePageState,
  loadPageState,
  clearPageState,
  setRestoredState,
  consumeRestoredState,
  isRestorableHash,
} from "../frontend/static/js/components/page-state.js";

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

function fakeSessionStorage() {
  const data = {};
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
    clear: () => { for (const k of Object.keys(data)) delete data[k]; },
  };
}

function setup() {
  const store = fakeSessionStorage();
  globalThis.sessionStorage = store;
  // Reset the restored-state stash between tests.
  setRestoredState(null);
  return store;
}

// `isRestorableHash`: only #/settings is excluded.
test("isRestorableHash rejects #/settings", () => {
  assert(isRestorableHash("#/dictionary") === true);
  assert(isRestorableHash("#/review") === true);
  assert(isRestorableHash("#/vocabulary") === true);
  assert(isRestorableHash("#/structures") === true);
  assert(isRestorableHash("#/phrases") === true);
  assert(isRestorableHash("#/analyze") === true);
  assert(isRestorableHash("#/refine") === true);
  assert(isRestorableHash("#/settings") === false);
  assert(isRestorableHash("") === false);
  assert(isRestorableHash(null) === false);
});

// `savePageState` + `loadPageState` round-trip.
test("REGRESSION: a page that has been saved restores identically on load", () => {
  // The user complaint: looked up 'apple' in dictionary → went to
  // settings → came back to dictionary and the search input was
  // gone. The fix: pages save their state on navigate-away and
  // loadPageState returns it for the next mount.
  const s = setup();
  const state = {
    searchInput: "apple",
    lastLookup: { word: "apple", lang: "en", source: "wordnet" },
  };
  savePageState("#/dictionary", state);
  const loaded = loadPageState("#/dictionary");
  assert(loaded !== null, "expected saved state to load");
  assert(loaded.searchInput === "apple", "searchInput round-trips");
  assert(loaded.lastLookup && loaded.lastLookup.word === "apple", "lastLookup.word round-trips");
  assert(loaded.lastLookup.source === "wordnet", "lastLookup.source round-trips");
});

test("savePageState skips the settings hash entirely", () => {
  const s = setup();
  savePageState("#/settings", { searchInput: "should not save" });
  assert(s.data["langlearn:page-state:v1:#/settings"] === undefined, "no entry written for #/settings");
  assert(loadPageState("#/settings") === null, "loading settings returns null");
});

test("savePageState with null state clears the existing entry", () => {
  const s = setup();
  savePageState("#/dictionary", { searchInput: "apple" });
  assert(loadPageState("#/dictionary") !== null, "saved first");
  savePageState("#/dictionary", null);
  assert(loadPageState("#/dictionary") === null, "null clears");
});

test("clearPageState removes just the named entry", () => {
  const s = setup();
  savePageState("#/dictionary", { searchInput: "apple" });
  savePageState("#/vocabulary", { activeBox: "1" });
  clearPageState("#/dictionary");
  assert(loadPageState("#/dictionary") === null, "dictionary cleared");
  assert(loadPageState("#/vocabulary") !== null, "vocabulary preserved");
});

test("loadPageState returns null when no entry exists", () => {
  setup();
  assert(loadPageState("#/dictionary") === null);
});

test("loadPageState returns null for malformed JSON without throwing", () => {
  const s = setup();
  s.setItem("langlearn:page-state:v1:#/dictionary", "not json");
  assert(loadPageState("#/dictionary") === null);
});

test("loadPageState returns null for empty hash", () => {
  setup();
  assert(loadPageState("") === null);
  assert(loadPageState(null) === null);
});

test("loadPageState returns null when sessionStorage.getItem throws", () => {
  globalThis.sessionStorage = { getItem: () => { throw new Error("blocked"); } };
  setRestoredState(null);
  assert(loadPageState("#/dictionary") === null, "throwing storage → null");
});

test("savePageState tolerates throwing sessionStorage", () => {
  globalThis.sessionStorage = { setItem: () => { throw new Error("quota"); } };
  setRestoredState(null);
  // Should not throw.
  savePageState("#/dictionary", { searchInput: "x" });
  assert(true, "no throw");
});

// `setRestoredState` / `consumeRestoredState` bridge.
test("consumeRestoredState returns the stashed state once and clears it", () => {
  setup();
  setRestoredState({ text: "hello" });
  const first = consumeRestoredState();
  assert(first && first.text === "hello", "first call yields the state");
  assert(consumeRestoredState() === null, "second call yields null (consumed)");
});

test("setRestoredState(null) clears any prior state", () => {
  setup();
  setRestoredState({ text: "hello" });
  setRestoredState(null);
  assert(consumeRestoredState() === null);
});

test("setRestoredState accepts any JSON-serializable value, not just objects", () => {
  setup();
  setRestoredState("anything");
  assert(consumeRestoredState() === "anything");
});

test("REGRESSION: after consumeRestoredState, a second mount of the same page does not replay the old state", () => {
  // The restore path must be one-shot: the page calls consume at the
  // top of render, and a re-render (e.g. language change) must not
  // reapply the old saved state.
  setup();
  setRestoredState({ searchInput: "apple" });
  assert(consumeRestoredState() !== null, "first mount consumes");
  assert(consumeRestoredState() === null, "second mount gets nothing");
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
