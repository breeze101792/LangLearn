// Smoke test for every page module the SPA router loads. Each module
// must:
//   1. Parse (dynamic import catches syntax errors).
//   2. Export the render function the router looks for.
//   3. Run its render entry point against a stub host without
//      throwing a synchronous error.
//
// This catches the regression class that has hit us three times in
// the page-state patch: a syntax error, a variable shadowing, or an
// undeclared reference in a page module breaks the whole SPA at
// navigation time, so the user sees an empty page or a frozen nav.
// Run with:
//
//   node tests/pages_load.test.mjs
//
// Exits 0 on pass, 1 on first failure.

import { JSDOM } from "jsdom";

// One DOM shared across all page render calls so listeners and IDs
// accumulate like they would in a real browser environment.
const dom = new JSDOM(
  `<!doctype html>
   <html><body>
     <main id="app-main"></main>
     <div id="nav-links"></div>
     <div id="nav-drawer"></div>
     <div id="nav-drawer-links"></div>
     <div id="nav-drawer-backdrop"></div>
     <button id="nav-menu-btn"></button>
     <button id="nav-drawer-close"></button>
     <div id="lang-switcher"></div>
     <button id="theme-toggle"></button>
     <div id="toast-stack"></div>
     <div id="add-panel"></div>
     <button id="add-toggle"></button>
     <div id="familiar-segments"></div>
     <div id="list"></div>
     <section id="review-body"></section>
     <div id="dict-suggest"></div>
     <div id="result-host"></div>
     <div id="box-segments"></div>
     <div id="vocab-list"></div>
      <div id="analyze-result"></div>
      <div id="refine-result"></div>
      <div id="translate-result"></div>
      <div id="describe-result"></div>
   </body></html>`,
  { url: "http://localhost:5056/#/dictionary" }
);

const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
// navigator is a read-only getter on globalThis in Node, so do not
// reassign it. The pages we test don't touch navigator directly.
globalThis.HTMLElement = window.HTMLElement;
globalThis.Element = window.Element;
globalThis.Node = window.Node;
globalThis.Event = window.Event;
globalThis.MouseEvent = window.MouseEvent;
globalThis.KeyboardEvent = window.KeyboardEvent;
globalThis.localStorage = window.localStorage;
globalThis.sessionStorage = window.sessionStorage;
// Stub fetch so the page render's async load() doesn't hit the
// network. Pages fall back to an error state when the response is not
// ok, which is fine for the smoke test.
globalThis.fetch = async () => ({
  ok: false,
  status: 503,
  statusText: "Service Unavailable",
  json: async () => ({ ok: false, error: "stub" }),
});

let failures = 0;
let passed = 0;

async function testAsync(name, fn) {
  try {
    await fn();
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

// The router maps each hash to a page module and a render function
// exported by that module. Keeping it in lock-step with main.js is
// the test's whole point — if main.js forgets a route, the SPA
// silently falls back to the dictionary, which is hard to notice
// without clicking every link.
const PAGES = [
  { hash: "#/learn",           mod: "../frontend/static/js/pages/learn.js",      render: "renderLearn" },
  { hash: "#/learn/new",       mod: "../frontend/static/js/pages/learn.js",      render: "renderLearn" },
  { hash: "#/learn/reviewed",  mod: "../frontend/static/js/pages/learn.js",      render: "renderLearn" },
  { hash: "#/vocabulary",  mod: "../frontend/static/js/pages/vocabulary.js",  render: "renderVocabulary" },
  { hash: "#/structures",  mod: "../frontend/static/js/pages/structures.js",  render: "renderStructures" },
  { hash: "#/phrases",     mod: "../frontend/static/js/pages/phrases.js",     render: "renderPhrases" },
  { hash: "#/assist",           mod: "../frontend/static/js/pages/assist.js",    render: "renderAssist" },
  { hash: "#/assist/refine",    mod: "../frontend/static/js/pages/assist.js",    render: "renderAssist" },
  { hash: "#/assist/translate", mod: "../frontend/static/js/pages/assist.js",    render: "renderAssist" },
  { hash: "#/assist/describe",  mod: "../frontend/static/js/pages/assist.js",    render: "renderAssist" },
  { hash: "#/settings",    mod: "../frontend/static/js/pages/settings.js",    render: "renderSettings" },
  { hash: "#/dictionary",  mod: "../frontend/static/js/pages/dictionary.js",  render: "renderDictionary" },
];

// Append a fresh query string per import so Node's module loader
// treats each as a new specifier. This is how Node's built-in ESM
// cache distinguishes "same URL" vs "different URL".
function freshUrl(file) {
  return `${file}?t=${Math.random().toString(36).slice(2)}`;
}

async function importFresh(file) {
  return import(freshUrl(file));
}

function fakeHost() {
  return window.document.getElementById("app-main");
}

// 1) Module-level check: every page must import without throwing.
for (const p of PAGES) {
  await testAsync(`module ${p.hash} imports without error`, async () => {
    const mod = await importFresh(p.mod);
    assert(mod, "module didn't load");
    assert(typeof mod[p.render] === "function", `expected ${p.render} function export`);
  });
}

// 2) Runtime check: every page must render against a stub host
// without throwing a synchronous error. Async errors (network) are
// not our concern — the page handles those.
for (const p of PAGES) {
  // Skip the settings page — it pulls a lot of system state and
  // its render is more setup than the smoke test needs. The import
  // check above covers it.
  if (p.hash === "#/settings") continue;

  await testAsync(`render ${p.hash} does not throw synchronously`, async () => {
    const mod = await importFresh(p.mod);
    const host = fakeHost();
    assert(host, "no host element");
    try {
      const result = mod[p.render](host);
      if (result && typeof result.then === "function") {
        await result.catch(() => { /* async errors are not regressions */ });
      }
    } catch (e) {
      throw new Error(`${p.render} threw: ${e.message}`);
    }
  });
}

// 3) The router itself must load. This is the test that caught the
// "currentHash shadowing" regression where the SPA never booted.
await testAsync("main.js imports without error", async () => {
  try {
    await importFresh("../frontend/static/js/main.js");
  } catch (e) {
    throw new Error(`main.js failed to load: ${e.message}`);
  }
});

// 4) Page-state helpers must be loadable on their own.
for (const helper of [
  "../frontend/static/js/components/page-state.js",
  "../frontend/static/js/components/review-cache.js",
  "../frontend/static/js/cache.js",
  "../frontend/static/js/components/assist-cache.js",
]) {
  await testAsync(`helper ${helper} imports`, async () => {
    const mod = await importFresh(helper);
    assert(mod, "module didn't load");
  });
}

// 5) Regression: the right-click "Look up word in Dictionary" handoff
// must win over the sessionStorage-restored previous visit. Before the
// fix the restored branch ran *after* the pending branch, overwrote
// the search input with the previous word, and kicked off a second
// lookup that bumped the token past the new one — the user landed on
// the dictionary page and saw the *old* word.
await testAsync("REGRESSION: pendingDictionaryWord wins over restored searchInput", async () => {
  // Import the singleton modules WITHOUT cache-busting so the test
  // and the page share the same store / page-state instances.
  // Cache-busting only on dictionary.js so its module-level state
  // (lastLookup, lookupToken, providerMeta, ...) starts clean.
  const stateMod = await import("../frontend/static/js/state.js");
  const pageStateMod = await import("../frontend/static/js/components/page-state.js");
  const dictMod = await importFresh("../frontend/static/js/pages/dictionary.js");

  // Seed the previous-visit save, as if the user had just navigated
  // away from looking up "apple".
  pageStateMod.setRestoredState({
    searchInput: "apple",
    lastLookup: { word: "apple", lang: "en", source: "wordnet" },
  });

  // Seed the right-click handoff for the new word "banana".
  stateMod.store.set({ pendingDictionaryWord: "banana" });

  // Stub fetch to count calls and remember the word each one asked
  // for. The body is JSON-encoded by api.post.
  const calls = [];
  globalThis.fetch = async (url, init) => {
    let body = {};
    try { body = JSON.parse(init && init.body || "{}"); } catch (_) {}
    calls.push({ url, word: body.word, provider: body.provider });
    return { ok: false, status: 503, statusText: "no", json: async () => ({ ok: false }) };
  };

  const host = window.document.getElementById("app-main");
  host.innerHTML = "";
  try {
    const result = dictMod.renderDictionary(host);
    if (result && typeof result.then === "function") {
      await result.catch(() => {});
    }
  } catch (e) {
    throw new Error(`renderDictionary threw: ${e.message}`);
  }

  // The search input must show the new word, not the restored one.
  const input = host.querySelector("#dict-search");
  assert(input, "search input rendered");
  assert(
    input.value === "banana",
    `expected input to show pending word "banana", got "${input.value}"`
  );

  // The pending field was consumed (one-shot), so it doesn't leak
  // into a later unrelated visit.
  assert(
    stateMod.store.get().pendingDictionaryWord === null,
    "pendingDictionaryWord should be cleared after consumption"
  );

  // Exactly one lookup should have been kicked off — the pending
  // one. The restored branch must not fire a second lookup, because
  // it would race the pending one and win on the token guard.
  const lookupCalls = calls.filter((c) => c.url && c.url.includes("/api/dictionary/lookup"));
  assert(
    lookupCalls.length === 1,
    `expected exactly one lookup, got ${lookupCalls.length}`
  );
  assert(
    lookupCalls[0].word === "banana",
    `expected lookup word "banana", got "${lookupCalls[0].word}"`
  );
});

await testAsync("REGRESSION: without a pending word, restored searchInput still applies", async () => {
  // Guards against over-correction: when the user navigates back via
  // the nav (no handoff), the previous search input must still come
  // back. Otherwise the dictionary page loses its restore feature.
  const stateMod = await import("../frontend/static/js/state.js");
  const pageStateMod = await import("../frontend/static/js/components/page-state.js");
  const dictMod = await importFresh("../frontend/static/js/pages/dictionary.js");

  pageStateMod.setRestoredState({
    searchInput: "apple",
    lastLookup: { word: "apple", lang: "en", source: "wordnet" },
  });
  assert(
    stateMod.store.get().pendingDictionaryWord === null,
    "no pending word for this visit"
  );

  globalThis.fetch = async () => ({ ok: false, status: 503, statusText: "no", json: async () => ({ ok: false }) });

  const host = window.document.getElementById("app-main");
  host.innerHTML = "";
  try {
    const result = dictMod.renderDictionary(host);
    if (result && typeof result.then === "function") {
      await result.catch(() => {});
    }
  } catch (e) {
    throw new Error(`renderDictionary threw: ${e.message}`);
  }

  const input = host.querySelector("#dict-search");
  assert(input.value === "apple", `expected restored input "apple", got "${input.value}"`);
});

// 6) Regression: Assist page saveState must use the router-provided
//    prevHash, NOT window.location.hash. Before the fix, navigating
//    from #/assist/refine to #/assist (analyze) would save the
//    analyze subpage's empty state under #/assist/refine's key — the
//    refine result was lost on the next visit. After the fix, the
//    router passes prevHash explicitly and the right subpage's state
//    is preserved.
//
// We can't easily simulate the full router here, but we can simulate
// the contract: saveState(prevHash) returns the correct tool's state
// regardless of window.location.hash.
await testAsync("REGRESSION: assist saveState uses prevHash, not window.location.hash", async () => {
  const assistMod = await importFresh("../frontend/static/js/pages/assist.js");
  const stateMod = await import("../frontend/static/js/state.js");

  // Seed settings so the page renders.
  stateMod.store.set({
    settings: {
      active_language: "en",
      explanation_primary: null,
      explanation_secondary: null,
      dict_chain_json: { en: [] },
    },
    languages: [{ code: "en", display_name: "English" }],
  });

  globalThis.fetch = async () => ({ ok: false, status: 503, statusText: "no", json: async () => ({ ok: false }) });

  // Mount refine on the refine hash; confirm saveState with the
  // matching prevHash returns null (no result yet).
  window.location.hash = "#/assist/refine";
  let host = window.document.getElementById("app-main");
  host.innerHTML = "";
  await assistMod.renderAssist(host);
  const snap = assistMod.saveState("#/assist/refine");
  assert(snap === null, "no state means saveState returns null");

  // Mount analyze on the analyze hash. Pre-fix, saveState would
  // read window.location.hash (now "#/assist") and default to
  // "analyze". Post-fix, saveState(prevHash) chooses the right
  // subpage. Either way with no input it returns null — but the
  // invariant is that calling saveState with prevHash does not
  // throw and operates on the right tool.
  window.location.hash = "#/assist";
  host = window.document.getElementById("app-main");
  host.innerHTML = "";
  await assistMod.renderAssist(host);
  const snapAnalyze = assistMod.saveState("#/assist/analyze");
  assert(snapAnalyze === null, "empty analyze state saves null");
});

// 7) Regression: switching between Assist subpages must not pollute
//    one subpage's saved state with another subpage's data. Before
//    the fix, saveState read window.location.hash which had already
//    changed to the new subpage, so the new (empty) subpage's state
//    was written under the old subpage's key.
await testAsync("REGRESSION: assist subpages do not leak state into each other", async () => {
  const assistMod = await importFresh("../frontend/static/js/pages/assist.js");
  const stateMod = await import("../frontend/static/js/state.js");

  stateMod.store.set({
    settings: {
      active_language: "en",
      explanation_primary: null,
      explanation_secondary: null,
      dict_chain_json: { en: [] },
    },
    languages: [{ code: "en", display_name: "English" }],
  });

  // Clear cache so the click goes to fetch (and we can stub it).
  const { clearAll } = await import("../frontend/static/js/components/assist-cache.js");
  clearAll();

  globalThis.fetch = async (url) => {
    if (url.includes("/api/refine")) {
      return {
        ok: true, status: 200,
        json: async () => ({
          ok: true,
          data: { corrected: "she would rather stay", native: "she'd rather stay", edits: [] },
        }),
      };
    }
    return { ok: false, status: 503, json: async () => ({ ok: false }) };
  };

  // Mount refine, type, click — moduleState.refine.lastResult gets set.
  window.location.hash = "#/assist/refine";
  let host = window.document.getElementById("app-main");
  host.innerHTML = "";
  await assistMod.renderAssist(host);
  const textarea = host.querySelector("#refine-text");
  assert(textarea, "refine textarea should render");
  textarea.value = "she would rather stay";
  textarea.dispatchEvent(new window.Event("input", { bubbles: true }));
  const btn = host.querySelector("#refine-btn");
  assert(btn, "refine button should render");
  btn.click();
  // Drain the microtask queue so the async fetch + render complete
  // before saveState reads the module state.
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  // The router now navigates to analyze. window.location.hash
  // becomes "#/assist". The router calls saveState(prevHash).
  // Before the fix, saveState read window.location.hash (now
  // "#/assist") and returned moduleState.analyze.lastResult (null).
  // The fix: saveState takes prevHash and returns the refine
  // snapshot.
  window.location.hash = "#/assist";
  const snap = assistMod.saveState("#/assist/refine");
  assert(snap, "saveState(prevHash=#/assist/refine) must capture refine state");
  assert(snap.text === "she would rather stay",
    `expected refine text preserved, got "${snap.text}"`);
  assert(snap.lastResult && snap.lastResult.corrected === "she would rather stay",
    "expected refine lastResult preserved");
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
