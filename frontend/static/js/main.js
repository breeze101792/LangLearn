// Main entry point. Sets up router, theme, initial data load, nav rendering.

import { api, setUnauthorizedHandler } from "./api.js";
import { store } from "./state.js";
import { renderNavLinks } from "./components/nav-links.js";
import { renderNavDrawer } from "./components/nav-drawer.js";
import { renderLangSwitcher } from "./components/lang-switcher.js";
import { bindContextMenu } from "./components/context-menu.js";
import {
  loadPageState,
  savePageState,
  setRestoredState,
} from "./components/page-state.js";

async function boot() {
  applyStoredTheme();
  bindThemeCycle();
  bindContextMenu();

  setUnauthorizedHandler(() => {
    window.location.replace("/");
  });

  const statusRes = await api.get("/api/auth/status");
  if (statusRes.ok && statusRes.data && statusRes.data.auth_required) {
    // Server is gated; the SPA at "/" is only served when authenticated.
    // If we're here on "/" then the server let us in, so proceed.
  }

  await loadInitial();
  renderNavLinks(currentHash());
  renderNavDrawer(currentHash());
  renderLangSwitcher();
  route();

  window.addEventListener("hashchange", () => {
    renderNavLinks(currentHash());
    renderNavDrawer(currentHash());
    route();
  });

  document.addEventListener("app:language-changed", () => {
    route();
  });

  bindLogout();
}

function bindLogout() {
  const nav = document.querySelector(".nav__right");
  if (!nav) return;
  if (document.getElementById("logout-btn")) return;
  const btn = document.createElement("button");
  btn.id = "logout-btn";
  btn.className = "nav__icon-btn";
  btn.title = "Sign out";
  btn.setAttribute("aria-label", "Sign out");
  btn.textContent = "⎋";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    await api.post("/api/auth/logout");
    window.location.replace("/");
  });
  nav.appendChild(btn);
}

async function loadInitial() {
  const [settingsRes, langsRes] = await Promise.all([api.get("/api/settings"), api.get("/api/languages")]);
  if (settingsRes.ok) {
    store.set({ settings: settingsRes.data, activeLanguage: settingsRes.data.active_language });
  } else {
    console.error("settings load failed", settingsRes.error);
  }
  if (langsRes.ok) {
    store.set({ languages: langsRes.data });
  } else {
    console.error("languages load failed", langsRes.error);
  }
  store.set({ initialized: true });
}

function currentHash() {
  return window.location.hash || "#/dictionary";
}

// Maps each route hash to its page module loader. The loaders return a
// module with the page's `render*` function plus optional `saveState`
// (called by the router when navigating away) and `dispose` (called to
// drop any global listeners the page registered on mount).
const PAGES = [
  { hash: "#/review",      load: () => import("./pages/review.js") },
  { hash: "#/vocabulary",  load: () => import("./pages/vocabulary.js") },
  { hash: "#/structures",  load: () => import("./pages/structures.js") },
  { hash: "#/phrases",     load: () => import("./pages/phrases.js") },
  { hash: "#/analyze",     load: () => import("./pages/analyze.js") },
  { hash: "#/refine",      load: () => import("./pages/refine.js") },
  { hash: "#/settings",    load: () => import("./pages/settings.js") },
  { hash: "#/dictionary",  load: () => import("./pages/dictionary.js") },
];

// Resolve a hash to its page spec. Anything that doesn't match falls
// through to the dictionary (matching the previous default behavior).
function pageSpecFor(hash) {
  return PAGES.find((p) => p.hash === hash) || PAGES[PAGES.length - 1];
}

let currentModule = null;
let currentRouteHash = null;

async function route() {
  const main = document.getElementById("app-main");
  if (!main) return;
  const nextHash = currentHash();
  const prevHash = currentRouteHash;
  const prevModule = currentModule;

  // 1) Let the outgoing page snapshot its state. We capture *before* we
  //    tear down so the page can read live DOM/input values. Pages
  //    without saveState (e.g. the dictionary result on first paint)
  //    simply contribute nothing. Settings is excluded from
  //    save/restore entirely — the user wants a fresh view there.
  if (prevModule && prevHash && prevHash !== nextHash) {
    if (typeof prevModule.saveState === "function") {
      try {
        const snap = prevModule.saveState();
        savePageState(prevHash, snap);
      } catch (e) {
        console.warn("saveState failed for", prevHash, e);
      }
    }
    if (typeof prevModule.dispose === "function") {
      try { prevModule.dispose(); } catch (e) { /* best-effort */ }
    }
  }

  // 2) Stash the saved state for the new page. The page's render
  //    function pulls it via consumeRestoredState() at the top.
  setRestoredState(loadPageState(nextHash));

  // 3) Load and mount the new page.
  const spec = pageSpecFor(nextHash);
  const mod = await spec.load();
  currentModule = mod;
  currentRouteHash = nextHash;
  const fn = pickRenderFn(mod, spec.hash);
  if (typeof fn === "function") fn(main);
}

function pickRenderFn(mod, hash) {
  // Each page module exports a hash-named render function for back-compat
  // with the existing call sites in the older route() function.
  if (hash === "#/review") return mod.renderReview;
  if (hash === "#/vocabulary") return mod.renderVocabulary;
  if (hash === "#/structures") return mod.renderStructures;
  if (hash === "#/phrases") return mod.renderPhrases;
  if (hash === "#/analyze") return mod.renderAnalyze;
  if (hash === "#/refine") return mod.renderRefine;
  if (hash === "#/settings") return mod.renderSettings;
  return mod.renderDictionary;
}

function applyStoredTheme() {
  let theme = "auto";
  try {
    const saved = localStorage.getItem("langlearn:theme");
    if (saved === "auto" || saved === "light" || saved === "dark") theme = saved;
  } catch (e) { /* */ }
  const html = document.documentElement;
  if (theme === "auto") {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    html.setAttribute("data-theme", mql.matches ? "dark" : "light");
  } else {
    html.setAttribute("data-theme", theme);
  }
  html.setAttribute("data-theme-mode", theme);
}

function bindThemeCycle() {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  const order = ["auto", "light", "dark"];
  btn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme-mode") || "auto";
    const next = order[(order.indexOf(cur) + 1) % order.length];
    document.documentElement.setAttribute("data-theme-mode", next);
    if (next === "auto") {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      document.documentElement.setAttribute("data-theme", mql.matches ? "dark" : "light");
    } else {
      document.documentElement.setAttribute("data-theme", next);
    }
    try { localStorage.setItem("langlearn:theme", next); } catch (e) { /* */ }
    btn.setAttribute("title", `Theme: ${next}`);
  });
}

boot();