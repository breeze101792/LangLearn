// Global right-click context menu for selected text.
//
// Items:
//   - Copy: copies the current selection (or, if the user right-clicked
//     on a word with no selection, copies that word).
//   - Paste: pastes the clipboard into the most recently focused editable
//     element (input / textarea / contenteditable). Falls back to
//     document.execCommand('paste') when no editable target is known.
//   - Lookup "<word>": opens the floating dictionary popup.
//   - Look up "<word>" in Dictionary: navigates to the Dictionary page
//     with the word pre-filled in the search box. Pairs with right-click
//     Copy to give a one-step "select a word anywhere → open Dictionary
//     → look up" flow.
//
// The menu suppresses the native contextmenu only when we have something
// meaningful to act on; inside editable controls we let the browser's
// menu through untouched.

import { openDictPopup } from "./dict-popup.js";
import { openTtsPopup } from "./tts-popup.js";
import { store } from "../state.js";

let menuEl = null;

const MENU_ID = "global-context-menu";

/**
 * Install the global contextmenu listener. Idempotent.
 */
export function bindContextMenu() {
  if (typeof document === "undefined") return;
  if (document.getElementById(MENU_ID)) return;
  document.addEventListener("contextmenu", onContextMenu, true);
  document.addEventListener("mousedown", onDocMouseDown, true);
  document.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("blur", hide);
  window.addEventListener("resize", hide);
  window.addEventListener("scroll", hide, true);
}

function isEditable(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function getSelectionText() {
  const sel = window.getSelection ? window.getSelection() : null;
  if (!sel) return "";
  const text = String(sel.toString() || "");
  return text.replace(/\s+/g, " ").trim();
}

function wordAtPoint(x, y) {
  // Fallback: no selection but right-clicked on a word. caretRangeFromPoint
  // is supported in all modern browsers; we walk to the nearest word boundary
  // so the user gets a sensible target even on a bare right-click.
  let range = null;
  if (document.caretRangeFromPoint) {
    range = document.caretRangeFromPoint(x, y);
  } else if (document.caretPositionFromPoint) {
    const pos = document.caretPositionFromPoint(x, y);
    if (pos && pos.offsetNode && pos.offsetNode.nodeType === Node.TEXT_NODE) {
      range = document.createRange();
      range.setStart(pos.offsetNode, pos.offset);
      range.setEnd(pos.offsetNode, pos.offset);
    }
  }
  if (!range) return "";
  const node = range.startContainer;
  if (!node || node.nodeType !== Node.TEXT_NODE) return "";
  const text = String(node.textContent || "");
  const offset = range.startOffset;
  let start = offset;
  let end = offset;
  const isWordChar = (ch) => /[\p{L}\p{N}'\-\.]/u.test(ch);
  while (start > 0 && isWordChar(text[start - 1])) start--;
  while (end < text.length && isWordChar(text[end])) end++;
  if (start === end) return "";
  return text.slice(start, end);
}

function onContextMenu(e) {
  if (isEditable(e.target)) {
    hide();
    return;
  }
  const selected = getSelectionText();
  const word = selected || wordAtPoint(e.clientX, e.clientY);
  if (!word) {
    hide();
    return;
  }
  e.preventDefault();
  show(e.clientX, e.clientY, word, !!selected);
}

function show(x, y, word, hasSelection) {
  hide();
  menuEl = document.createElement("div");
  menuEl.id = MENU_ID;
  menuEl.className = "ctx-menu";
  menuEl.setAttribute("role", "menu");

  // Copy: copies the current selection. When the user right-clicked a
  // bare word, we still offer Copy so they get a one-click copy of the
  // word — but the button shows a subtle hint when nothing was selected.
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "ctx-menu__item";
  copyBtn.setAttribute("role", "menuitem");
  copyBtn.textContent = hasSelection ? "Copy" : `Copy "${truncate(word, 24)}"`;
  copyBtn.addEventListener("click", () => {
    const target = hasSelection ? getSelectionText() : word;
    hide();
    copyToClipboard(target);
  });
  menuEl.appendChild(copyBtn);

  // Paste: paste the clipboard into the most recently focused editable
  // element. Always offered (never disabled) — the function below is a
  // no-op when nothing editable is focused.
  const pasteBtn = document.createElement("button");
  pasteBtn.type = "button";
  pasteBtn.className = "ctx-menu__item";
  pasteBtn.setAttribute("role", "menuitem");
  pasteBtn.textContent = "Paste";
  pasteBtn.addEventListener("click", async () => {
    hide();
    await pasteFromClipboard();
  });
  menuEl.appendChild(pasteBtn);

  // Divider between clipboard actions and the lookup action.
  menuEl.appendChild(makeDivider());

  // Speak "<text>": floating TTS popup with playback controls. Acts on
  // the same selection/word resolution as the dictionary lookups, so
  // it can read back either a phrase the user highlighted or a single
  // word they right-clicked.
  const speakBtn = document.createElement("button");
  speakBtn.type = "button";
  speakBtn.className = "ctx-menu__item";
  speakBtn.setAttribute("role", "menuitem");
  speakBtn.textContent = `Speak "${truncate(word, 24)}"`;
  speakBtn.addEventListener("click", () => {
    const target = word;
    hide();
    openTtsPopup({ word: target });
  });
  menuEl.appendChild(speakBtn);

  // Lookup "<word>": popup dictionary.
  const lookupBtn = document.createElement("button");
  lookupBtn.type = "button";
  lookupBtn.className = "ctx-menu__item";
  lookupBtn.setAttribute("role", "menuitem");
  lookupBtn.textContent = `Lookup "${truncate(word, 24)}"`;
  lookupBtn.addEventListener("click", () => {
    const target = word;
    hide();
    openDictPopup({ word: target });
  });
  menuEl.appendChild(lookupBtn);

// Look up "<word>" in Dictionary: navigate to the Dictionary page with
// the search box pre-filled. The page consumes `pendingDictionaryWord`
// on mount and then clears it, so this stays a clean handoff even if
// the user never lands on the page.
  const lookupPageBtn = document.createElement("button");
  lookupPageBtn.type = "button";
  lookupPageBtn.className = "ctx-menu__item";
  lookupPageBtn.setAttribute("role", "menuitem");
  lookupPageBtn.textContent = `Look up "${truncate(word, 24)}" in Dictionary`;
  lookupPageBtn.addEventListener("click", () => {
    const target = word;
    hide();
    // Set the pending word FIRST so the dictionary page can read it
    // when its renderDictionary() runs in response to the hashchange.
    store.set({ pendingDictionaryWord: target });
    if (window.location.hash !== "#/dictionary") {
      window.location.hash = "#/dictionary";
    } else {
      // Already on the Dictionary page — the hashchange handler won't
      // re-run, so dispatch a manual event to force a re-render. The
      // page reads the pending word on every mount, so this works.
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  });
  menuEl.appendChild(lookupPageBtn);

  document.body.appendChild(menuEl);
  // Clamp to viewport so the menu never renders off-screen.
  const rect = menuEl.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  let left = x;
  let top = y;
  if (left + rect.width > vw) left = Math.max(4, vw - rect.width - 4);
  if (top + rect.height > vh) top = Math.max(4, vh - rect.height - 4);
  menuEl.style.left = `${left}px`;
  menuEl.style.top = `${top}px`;
}

function makeDivider() {
  const sep = document.createElement("div");
  sep.className = "ctx-menu__sep";
  sep.setAttribute("role", "separator");
  return sep;
}

async function copyToClipboard(text) {
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch (e) {
    // Fall through to execCommand fallback.
  }
  // Fallback for non-secure contexts (e.g. plain-http LAN). Hidden
  // textarea + execCommand keeps the legacy path alive.
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e) { /* no-op */ }
  ta.remove();
}

async function pasteFromClipboard() {
  // Find the most recently focused editable element so the paste lands
  // somewhere useful — usually the search box or last text input the
  // user touched. If we don't know one, fall back to whatever the
  // browser considers the active element, or no-op.
  const target = lastEditable() || (isEditable(document.activeElement)
    ? document.activeElement : null);
  let text = "";
  try {
    if (navigator.clipboard && window.isSecureContext) {
      text = await navigator.clipboard.readText();
    } else {
      text = await readClipboardLegacy();
    }
  } catch (e) {
    return;
  }
  if (!text) return;
  if (target && !target.readOnly && !target.disabled) {
    insertIntoEditable(target, text);
  } else {
    // No editable target: at least leave the text on the system
    // clipboard so a subsequent paste into the search box still works.
    // (We already have it; nothing more to do.)
  }
}

function lastEditable() {
  if (!lastEditable._el || !document.body.contains(lastEditable._el)) return null;
  return lastEditable._el;
}
// Track the last focused editable so we can paste into it later. Bound
// here rather than inside onContextMenu to keep the side-effect scope
// small.
function trackLastEditable(e) {
  if (isEditable(e.target)) lastEditable._el = e.target;
}
if (typeof document !== "undefined") {
  document.addEventListener("focusin", trackLastEditable, true);
}

function insertIntoEditable(el, text) {
  if (typeof el.setRangeText === "function") {
    // input / textarea
    const start = el.selectionStart || 0;
    const end = el.selectionEnd || 0;
    el.setRangeText(text, start, end, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
    return;
  }
  if (el.isContentEditable) {
    // contenteditable: insert at the current selection / caret.
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) {
      el.appendChild(document.createTextNode(text));
    } else {
      const range = sel.getRangeAt(0);
      range.deleteContents();
      range.insertNode(document.createTextNode(text));
      range.collapse(false);
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
  }
}

function readClipboardLegacy() {
  return new Promise((resolve) => {
    const ta = document.createElement("textarea");
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    let text = "";
    try { text = document.execCommand("paste") ? ta.value : ""; }
    catch (e) { text = ""; }
    ta.remove();
    resolve(text);
  });
}

function hide() {
  if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
  menuEl = null;
}

function onDocMouseDown(e) {
  if (!menuEl) return;
  if (e.target === menuEl || menuEl.contains(e.target)) return;
  hide();
}

function onKeyDown(e) {
  if (!menuEl) return;
  if (e.key === "Escape") {
    e.preventDefault();
    hide();
  }
}

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
