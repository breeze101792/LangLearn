// Global right-click context menu for selected text.
//
// Currently exposes a single "Lookup" item that asks the popup dictionary to
// fetch the selected word. The menu suppresses the native contextmenu only
// when we have something meaningful to act on; inside editable controls we
// let the browser's menu through untouched.

import { openDictPopup } from "./dict-popup.js";

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
  show(e.clientX, e.clientY, word);
}

function show(x, y, word) {
  hide();
  menuEl = document.createElement("div");
  menuEl.id = MENU_ID;
  menuEl.className = "ctx-menu";
  menuEl.setAttribute("role", "menu");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ctx-menu__item";
  btn.setAttribute("role", "menuitem");
  btn.textContent = `Lookup "${truncate(word, 24)}"`;
  btn.addEventListener("click", () => {
    const target = word;
    hide();
    openDictPopup({ word: target });
  });
  menuEl.appendChild(btn);
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
