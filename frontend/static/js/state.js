// Simple pub/sub store. Single global state object with subscribers.

const state = {
  settings: null,
  languages: [],
  activeLanguage: null,
  initialized: false,
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