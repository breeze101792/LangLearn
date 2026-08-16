// Clear all user-specific cached data. Called on login and logout so a
// new session never sees the previous user's data.
//
// What we keep:
//   - "langlearn:theme"  (device-level UI preference, not user data)
//
// What we clear:
//   - localStorage:   everything under "langlearn:" except the theme
//   - sessionStorage:  everything under "langlearn:"

const THEME_KEY = "langlearn:theme";
const PREFIX = "langlearn:";

function clearStorage(storage) {
  try {
    const keys = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k && k.startsWith(PREFIX) && k !== THEME_KEY) keys.push(k);
    }
    keys.forEach((k) => { try { storage.removeItem(k); } catch (e) { /* */ } });
  } catch (e) { /* */ }
}

export function clearUserData() {
  clearStorage(localStorage);
  clearStorage(sessionStorage);
}