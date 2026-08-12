// Simple pub/sub store. Single global state object with subscribers.

const state = {
  settings: null,
  languages: [],
  activeLanguage: null,
  initialized: false,
  // Set by the right-click "Look up word" item so the Dictionary page
  // can pre-fill the search input on its next mount. The Dictionary
  // page clears it after consuming.
  pendingDictionaryWord: null,
};

const subs = new Set();

export const store = {
  get: () => state,
  set(patch) {
    Object.assign(state, patch);
    subs.forEach((fn) => {
      try { fn(state); } catch (e) { console.error("subscriber failed", e); }
    });
  },
  subscribe(fn) {
    subs.add(fn);
    return () => subs.delete(fn);
  },
};