// Per-page state save/restore, browser-only (sessionStorage).
//
// Each non-settings page can save a small JSON-serializable object that
// describes its current user-visible state (search input, current page,
// current provider, etc.). When the user navigates back to that page
// within the same tab, the saved state is handed back to the page so it
// can re-render the same view without losing the user's work.
//
// The store is per-tab by design (sessionStorage), so closing and
// reopening the browser starts every page fresh. Settings is excluded:
// the user explicitly asked for it to always be a fresh view.
//
// API:
//   savePageState(hash, state)     — write state under <hash>
//   loadPageState(hash)            — read it back, returns null if missing
//   clearPageState(hash)           — drop the entry
//   consumeRestoredState()         — pull the stashed state for the
//                                    currently-mounting page (router sets
//                                    it just before calling render*)
//   isRestorableHash(hash)         — false for #/settings
//
// The state object must be JSON-serializable. The router runs save→load
// on every hash change, so pages that don't opt in (don't call
// consumeRestoredState) simply ignore it.

const STATE_KEY_PREFIX = "langlearn:page-state:v1:";
const RESTORED_STATE_KEY = "__langlearnRestoredPageState__";
const SETTINGS_HASH = "#/settings";

export function isRestorableHash(hash) {
  return !!hash && hash !== SETTINGS_HASH;
}

export function savePageState(hash, state) {
  if (!isRestorableHash(hash)) return;
  if (state == null) {
    clearPageState(hash);
    return;
  }
  try {
    const payload = JSON.stringify({ savedAt: Date.now(), state });
    sessionStorage.setItem(STATE_KEY_PREFIX + hash, payload);
  } catch (e) { /* sessionStorage full or disabled — drop silently */ }
}

export function loadPageState(hash) {
  if (!isRestorableHash(hash)) return null;
  let raw;
  try {
    raw = sessionStorage.getItem(STATE_KEY_PREFIX + hash);
  } catch (e) {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed.state || null : null;
  } catch (e) {
    return null;
  }
}

export function clearPageState(hash) {
  if (!hash) return;
  try {
    sessionStorage.removeItem(STATE_KEY_PREFIX + hash);
  } catch (e) { /* */ }
}

// Bridge between the router and a page's render function. The router sets
// the state via `setRestoredState` right before calling the page's
// `render*`. The page's first line should be:
//
//   const restored = consumeRestoredState();
//
// which returns the state once and then clears it so a hot-reload or
// re-mount doesn't replay an old restoration.
export function setRestoredState(state) {
  globalThis[RESTORED_STATE_KEY] = state == null ? null : state;
}

export function consumeRestoredState() {
  const v = globalThis[RESTORED_STATE_KEY];
  globalThis[RESTORED_STATE_KEY] = null;
  return v == null ? null : v;
}
