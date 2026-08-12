// Main entry point. Sets up router, theme, initial data load, nav rendering.

import { api, setUnauthorizedHandler } from "./api.js";
import { store } from "./state.js";
import { renderNavLinks } from "./components/nav-links.js";
import { renderLangSwitcher } from "./components/lang-switcher.js";
import { bindContextMenu } from "./components/context-menu.js";

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
  renderLangSwitcher();
  route();

  window.addEventListener("hashchange", () => {
    renderNavLinks(currentHash());
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

function route() {
  const hash = currentHash();
  const main = document.getElementById("app-main");
  if (!main) return;
  if (hash === "#/review") return import("./pages/review.js").then((m) => m.renderReview(main));
  if (hash === "#/vocabulary") return import("./pages/vocabulary.js").then((m) => m.renderVocabulary(main));
  if (hash === "#/structures") return import("./pages/structures.js").then((m) => m.renderStructures(main));
  if (hash === "#/phrases") return import("./pages/phrases.js").then((m) => m.renderPhrases(main));
  if (hash === "#/settings") return import("./pages/settings.js").then((m) => m.renderSettings(main));
  return import("./pages/dictionary.js").then((m) => m.renderDictionary(main));
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