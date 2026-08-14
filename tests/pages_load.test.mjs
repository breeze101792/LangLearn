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

import { JSDOM } from "/mnt/projects/webapp/notebook-server/node_modules/jsdom/lib/api.js";

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
  { hash: "#/review",      mod: "../frontend/static/js/pages/review.js",      render: "renderReview" },
  { hash: "#/vocabulary",  mod: "../frontend/static/js/pages/vocabulary.js",  render: "renderVocabulary" },
  { hash: "#/structures",  mod: "../frontend/static/js/pages/structures.js",  render: "renderStructures" },
  { hash: "#/phrases",     mod: "../frontend/static/js/pages/phrases.js",     render: "renderPhrases" },
  { hash: "#/analyze",     mod: "../frontend/static/js/pages/analyze.js",     render: "renderAnalyze" },
  { hash: "#/refine",      mod: "../frontend/static/js/pages/refine.js",      render: "renderRefine" },
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
]) {
  await testAsync(`helper ${helper} imports`, async () => {
    const mod = await importFresh(helper);
    assert(mod, "module didn't load");
  });
}

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
