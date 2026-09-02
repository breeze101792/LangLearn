// Pure helper used by the Assist page (Analyze / Refine / Translate /
// Describe) to cache LLM results in browser storage so re-clicking the
// same button with the same input is instant and doesn't re-hit the LLM.
//
// Why this exists: the Assist tools are LLM-heavy and previously hit the
// backend on every click. A user pasting the same paragraph twice would
// pay the latency twice. Caching by (tool, lang, text) makes repeat
// lookups free.
//
// Storage shape:
//
//   key:   <NAMESPACE>:<tool>:<lang>:<text-sha1>
//   value: { text, lang, fetchedAt, result }
//
//   NAMESPACE is a single localStorage key whose value is a flat map of
//   text-hash → payload. Using one key per (tool, lang, text) would
//   inflate the localStorage key count, but a single map gives us atomic
//   reads + writes and a single place to enforce the size cap.
//
// The function is pure: it only reads from `globalThis.localStorage` (or
// the `storage` argument tests pass). It is loaded by the Node test
// harness (tests/assist_cache.test.mjs) without a browser.
//
// Why sha1 the text: language + tool + a 200-char text + a 5-char lang
// is at most ~210 chars per key. localStorage key length isn't strictly
// capped but some browsers truncate around 5 KB per key, and a 4000-char
// textarea payload times hundreds of entries would blow past that. A
// 40-char hex digest keeps the key length bounded.

const NAMESPACE = "langlearn:assist:v2";
const MAX_ENTRIES = 200;

// Public so tests can seed the namespace without recreating the prefix.
export const ASSIST_NAMESPACE = NAMESPACE;

function keyFor(tool, lang, text) {
  return `${tool}:${lang || ""}:${textKey(text)}`;
}

function textKey(text) {
  // sha1 is just a fingerprint here — we need a stable, low-collision
  // digest of the input text so the localStorage key length stays
  // bounded regardless of how long the user types. We implement sha1
  // directly so the cache is usable from both the browser and the Node
  // test harness without pulling in a hash library. This is *not* a
  // cryptographic boundary; it's just a cache-key fingerprint.
  const str = String(text || "");
  const bytes = new TextEncoder().encode(str);
  const bitLen = bytes.length * 8;
  // Pad to a 64-byte multiple: 1 byte for 0x80, up to 8 bytes for
  // length, then zeros. ((len + 9 + 63) & ~63) rounds up.
  const padLen = (((bytes.length + 9) + 63) & ~63) - bytes.length;
  const buf = new Uint8Array(bytes.length + padLen);
  buf.set(bytes);
  buf[bytes.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(buf.length - 8, Math.floor(bitLen / 0x100000000), false);
  view.setUint32(buf.length - 4, bitLen >>> 0, false);

  let h0 = 0x67452301 | 0;
  let h1 = 0xEFCDAB89 | 0;
  let h2 = 0x98BADCFE | 0;
  let h3 = 0x10325476 | 0;
  let h4 = 0xC3D2E1F0 | 0;

  const w = new Uint32Array(80);
  for (let chunk = 0; chunk < buf.length; chunk += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = view.getUint32(chunk + i * 4, false);
    }
    for (let i = 16; i < 80; i++) {
      const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16];
      w[i] = ((x << 1) | (x >>> 31)) >>> 0;
    }
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      let f, k;
      if (i < 20) { f = (b & c) | ((~b >>> 0) & d); k = 0x5A827999; }
      else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
      else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
      else { f = b ^ c ^ d; k = 0xCA62C1D6; }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) >>> 0;
      e = d; d = c; c = ((b << 30) | (b >>> 2)) >>> 0; b = a; a = t;
    }
    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }

  return [h0, h1, h2, h3, h4]
    .map((n) => n.toString(16).padStart(8, "0"))
    .join("");
}

function readAll(storage) {
  let raw;
  try {
    raw = storage.getItem(NAMESPACE);
  } catch (e) {
    return {};
  }
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (e) {
    return {};
  }
}

function writeAll(storage, obj) {
  try {
    storage.setItem(NAMESPACE, JSON.stringify(obj));
  } catch (e) {
    // localStorage full or unavailable. Drop the oldest half and retry
    // once so the cache degrades gracefully rather than throwing.
    const keys = Object.keys(obj).sort((a, b) => {
      const ta = (obj[a] && obj[a].fetchedAt) || 0;
      const tb = (obj[b] && obj[b].fetchedAt) || 0;
      return ta - tb;
    });
    const keep = Math.max(1, Math.floor(keys.length / 2));
    const trimmed = {};
    keys.slice(keys.length - keep).forEach((k) => { trimmed[k] = obj[k]; });
    try {
      storage.setItem(NAMESPACE, JSON.stringify(trimmed));
    } catch (e2) { /* give up */ }
  }
}

function enforceCap(obj) {
  // Per (tool, lang) bucket: keep at most MAX_ENTRIES by dropping the
  // oldest fetchedAt. The cap matters because a user pasting the same
  // long paragraph thousands of times would otherwise fill storage.
  const buckets = new Map();
  for (const key of Object.keys(obj)) {
    const sep = key.indexOf(":");
    const second = key.indexOf(":", sep + 1);
    const bucket = key.slice(0, second); // "<tool>:<lang>"
    if (!buckets.has(bucket)) buckets.set(bucket, []);
    buckets.get(bucket).push(key);
  }
  for (const [, keys] of buckets) {
    if (keys.length <= MAX_ENTRIES) continue;
    keys.sort((a, b) => ((obj[a].fetchedAt || 0) - (obj[b].fetchedAt || 0)));
    const drop = keys.length - MAX_ENTRIES;
    for (let i = 0; i < drop; i++) delete obj[keys[i]];
  }
}

/**
 * Return the cached result for `tool`+`lang`+`text`, or null on miss.
 * `storage` defaults to `globalThis.localStorage` so callers in the
 * browser pass nothing; tests pass a fake.
 */
export function getCached(tool, lang, text, storage) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!tool || !store) return null;
  const all = readAll(store);
  const hit = all[keyFor(tool, lang, text)];
  if (!hit) return null;
  // Return only the LLM result, not the bookkeeping fields.
  return hit.result || null;
}

/**
 * Store a result for `tool`+`lang`+`text`. `result` is whatever the
 * backend returned; the caller decides its shape. Best-effort: if
 * storage is full or disabled the call is dropped.
 */
export function setCached(tool, lang, text, result, storage) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!tool || !store) return;
  const all = readAll(store);
  all[keyFor(tool, lang, text)] = {
    text,
    lang: lang || "",
    fetchedAt: Date.now(),
    result,
  };
  enforceCap(all);
  writeAll(store, all);
}

/**
 * Drop a single entry so the next call re-hits the LLM. Used by the
 * Regenerate button.
 */
export function clearCached(tool, lang, text, storage) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!tool || !store) return;
  const all = readAll(store);
  delete all[keyFor(tool, lang, text)];
  writeAll(store, all);
}

/**
 * Drop every entry for a given tool. Not wired today, but exposed so
 * tests and future settings (e.g. "Clear my Analyze history") can use
 * the same primitive.
 */
export function clearTool(tool, storage) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!tool || !store) return;
  const all = readAll(store);
  const prefix = `${tool}:`;
  for (const key of Object.keys(all)) {
    if (key.startsWith(prefix)) delete all[key];
  }
  writeAll(store, all);
}

/**
 * Drop everything in the namespace. Wired to clear-cache.js via the
 * "langlearn:" prefix matcher, so this is here for symmetry.
 */
export function clearAll(storage) {
  const store = storage || (typeof globalThis !== "undefined" && globalThis.localStorage);
  if (!store) return;
  try { store.removeItem(NAMESPACE); } catch (e) { /* */ }
}
