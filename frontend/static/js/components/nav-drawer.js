// Mobile drawer nav. Renders the same ROUTES as nav-links.js into the
// off-canvas drawer and wires the hamburger trigger, backdrop, close
// button, Escape key, and link-click auto-close. Active link is kept
// in sync with the top-bar render by accepting the same activeHash.

import { createDrawer } from "./drawer-state.js";

const ROUTES = [
  { hash: "#/dictionary", label: "Dictionary" },
  { hash: "#/analyze",    label: "Analyze" },
  { hash: "#/refine",     label: "Refine" },
  { hash: "#/review",     label: "Review" },
  { hash: "#/vocabulary", label: "Vocabulary" },
  { hash: "#/structures", label: "Structures" },
  { hash: "#/phrases",    label: "Phrases" },
  { hash: "#/settings",   label: "Settings" },
];

let prevTrigger = null;
let prevBackdrop = null;
let prevCloseBtn = null;
let prevEscHandler = null;
let prevLinksClickHandler = null;
let boundHost = null;
let drawerCtl = null;

export function renderNavDrawer(activeHash) {
  const drawer = document.getElementById("nav-drawer");
  const trigger = document.getElementById("nav-menu-btn");
  const backdrop = document.getElementById("nav-drawer-backdrop");
  const closeBtn = document.getElementById("nav-drawer-close");
  const linksHost = document.getElementById("nav-drawer-links");
  if (!drawer || !trigger || !backdrop || !closeBtn || !linksHost) return;

  drawerCtl = createDrawer({ drawer, backdrop, trigger, body: document.body });

  renderLinks(linksHost, activeHash);
  bindTrigger(trigger);
  bindBackdrop(backdrop);
  bindCloseBtn(closeBtn);
  bindEscape();
  bindLinkAutoClose(linksHost);
}

function renderLinks(host, activeHash) {
  host.innerHTML = "";
  for (const r of ROUTES) {
    const a = document.createElement("a");
    a.href = r.hash;
    a.className = "nav-drawer__link" + (r.hash === activeHash ? " nav-drawer__link--active" : "");
    a.textContent = r.label;
    a.setAttribute("aria-current", r.hash === activeHash ? "page" : "false");
    host.appendChild(a);
  }
}

function bindTrigger(trigger) {
  if (prevTrigger) prevTrigger.removeEventListener("click", prevTrigger._handler);
  const handler = () => {
    if (drawerCtl.isOpen()) drawerCtl.close();
    else drawerCtl.open();
  };
  trigger.addEventListener("click", handler);
  trigger._handler = handler;
  prevTrigger = trigger;
}

function bindBackdrop(backdrop) {
  if (prevBackdrop) prevBackdrop.removeEventListener("click", prevBackdrop._handler);
  const handler = () => drawerCtl.close();
  backdrop.addEventListener("click", handler);
  backdrop._handler = handler;
  prevBackdrop = backdrop;
}

function bindCloseBtn(closeBtn) {
  if (prevCloseBtn) prevCloseBtn.removeEventListener("click", prevCloseBtn._handler);
  const handler = () => drawerCtl.close();
  closeBtn.addEventListener("click", handler);
  closeBtn._handler = handler;
  prevCloseBtn = closeBtn;
}

function bindEscape() {
  if (prevEscHandler) document.removeEventListener("keydown", prevEscHandler);
  const handler = (e) => {
    if (e.key === "Escape" && drawerCtl.isOpen()) {
      e.preventDefault();
      drawerCtl.close();
    }
  };
  document.addEventListener("keydown", handler);
  prevEscHandler = handler;
}

function bindLinkAutoClose(linksHost) {
  if (prevLinksClickHandler && boundHost === linksHost) {
    boundHost.removeEventListener("click", prevLinksClickHandler);
  }
  const handler = (e) => drawerCtl.onLinkClick(e.target);
  linksHost.addEventListener("click", handler);
  prevLinksClickHandler = handler;
  boundHost = linksHost;
}
