// Unit tests for the floating TTS popup
// (frontend/static/js/components/tts-popup.js).
//
// Covers the user-facing behaviors that aren't trivially testable in
// the browser: opening the popup, displaying the selected text,
// presenting the playback controls, switching speed client-side
// without re-fetching, and tearing down cleanly on close.
//
// The popup depends on `Audio`, `fetch`, `URL.createObjectURL`, and
// the DOM. The test stubs each of them so the popup's logic runs in
// plain Node without touching the network or real audio drivers.
//
// Run with:
//   node tests/tts_popup.test.mjs
// Exits 0 on pass, 1 on first failure.

import { JSDOM } from "jsdom";

const dom = new JSDOM(
  `<!doctype html><html><body></body></html>`,
  { url: "http://localhost:5056/" }
);
const { window } = dom;
globalThis.window = window;
globalThis.document = window.document;
globalThis.HTMLElement = window.HTMLElement;
globalThis.Element = window.Element;
globalThis.Node = window.Node;
globalThis.Event = window.Event;
globalThis.KeyboardEvent = window.KeyboardEvent;
globalThis.MouseEvent = window.MouseEvent;
globalThis.requestAnimationFrame = window.requestAnimationFrame
  || ((cb) => setTimeout(cb, 0));
globalThis.localStorage = window.localStorage;
// fetchCalls is the shared log of fetch calls; defaultFetch (called
// from freshDom) populates it. Tests can inspect fetchCalls after
// triggering an action.
let fetchCalls = [];

// --- Audio stub ---------------------------------------------------------
//
// Track playbackRate, currentTime, and play/pause calls. No real audio.

function makeAudioCtor() {
  const calls = { created: 0, played: 0, paused: 0 };
  function FakeAudio(src) {
    calls.created++;
    this.src = src || "";
    this.playbackRate = 1;
    this.currentTime = 0;
    this.paused = true;
    this.ended = false;
    this._listeners = {};
    this.play = () => { calls.played++; this.paused = false; return Promise.resolve(); };
    this.pause = () => { calls.paused++; this.paused = true; };
    this.addEventListener = (name, fn) => {
      (this._listeners[name] = this._listeners[name] || []).push(fn);
    };
    this._fire = (name) => (this._listeners[name] || []).forEach((fn) => fn());
  }
  FakeAudio.calls = calls;
  return FakeAudio;
}

// --- URL.createObjectURL stub -------------------------------------------

let blobUrls = 0;
let revokedUrls = 0;
globalThis.URL = window.URL;
globalThis.URL.createObjectURL = () => { blobUrls++; return `blob:test/${blobUrls}`; };
globalThis.URL.revokeObjectURL = () => { revokedUrls++; };

// --- Test harness -------------------------------------------------------

let failures = 0;
let passed = 0;

async function testAsync(name, fn) {
  try {
    await fn();
    console.log("ok  -", name);
    passed++;
  } catch (e) {
    console.log("FAIL -", name);
    console.log("       ", e.message);
    failures++;
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

async function importFresh(file) {
  return import(`${file}?t=${Math.random().toString(36).slice(2)}`);
}

// Default fetch stub — returns a successful audio response and
// records the URL so tests can assert on it. Tests that want a
// different fetch behavior should override globalThis.fetch AFTER
// calling freshDom().
function defaultFetch(url, init) {
  fetchCalls.push({ url: String(url), method: (init && init.method) || "GET" });
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => "audio/mpeg" },
    blob: async () => ({ size: 4, type: "audio/mpeg" }),
  });
}

function freshDom() {
  // Reset the body so any popup element left over from a previous
  // test (the popup persists across close) is dropped, and its
  // inline styles reset. Also wipe localStorage so a saved position
  // from a prior test doesn't carry into the next one, and reset the
  // fetch stub so a previous test's failure mock doesn't leak.
  document.body.innerHTML = "";
  window.localStorage.clear();
  globalThis.fetch = defaultFetch;
  fetchCalls = [];
  blobUrls = 0;
  revokedUrls = 0;
}

// The popup defers the --open class add via requestAnimationFrame to
// let the entrance animation start from display:none. Wait one tick
// after opening so post-open assertions see the visible state.
async function openAndFlush(popup, opts) {
  await popup(opts);
  await new Promise((resolve) => setTimeout(resolve, 0));
}

// --- Tests --------------------------------------------------------------

await testAsync("module imports without error", async () => {
  const mod = await importFresh("../frontend/static/js/components/tts-popup.js");
  assert(typeof mod.openTtsPopup === "function", "exports openTtsPopup");
  assert(typeof mod.closeTtsPopup === "function", "exports closeTtsPopup");
});

await testAsync("opening renders the popup with the text, controls, and speed buttons", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "hola mundo", lang: "es" });

  const popup = document.getElementById("tts-popup");
  assert(popup, "popup mounted");
  assert(!popup.hidden, "popup not hidden after open");
  assert(popup.classList.contains("tts-popup--open"), "popup has --open class");

  const textEl = popup.querySelector("[data-tts-text]");
  assert(textEl, "text element rendered");
  assert(textEl.textContent === "hola mundo", `text shows input, got "${textEl.textContent}"`);

  const playBtn = popup.querySelector('[data-tts-action="play"]');
  const replayBtn = popup.querySelector('[data-tts-action="replay"]');
  assert(playBtn, "play button rendered");
  assert(replayBtn, "replay button rendered");

  const speedButtons = popup.querySelectorAll("[data-tts-speed-value]");
  assert(speedButtons.length === 3, `expected 3 speed buttons, got ${speedButtons.length}`);
  const labels = [...speedButtons].map((b) => b.textContent.trim());
  assert(labels[0] === "0.5x" && labels[1] === "1x" && labels[2] === "1.5x",
    `expected 0.5x/1x/1.5x, got ${labels.join("/")}`);

  // Default is 1x, so the middle button should be active.
  const active = popup.querySelector(".tts-popup__speed-btn--active");
  assert(active, "one speed button has --active class");
  assert(active.dataset.ttsSpeedValue === "1", `1x active by default, got ${active.dataset.ttsSpeedValue}`);
});

await testAsync("opening fetches /api/tts/audio with the selected text", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "buenos días", lang: "es" });

  assert(fetchCalls.length === 1, `expected 1 fetch, got ${fetchCalls.length}`);
  const url = fetchCalls[0].url;
  assert(url.includes("/api/tts/audio"), `expected /api/tts/audio, got ${url}`);
  assert(url.includes("lang=es"), `lang in URL, got ${url}`);
  // Phrase must be space-joined with underscores per backend contract.
  assert(/word=buenos[_%]D/.test(url) || /word=buenos_d%C3%ADas/.test(url),
    `phrase URL-encoded with _, got ${url}`);
});

await testAsync("speed change updates the active button and does NOT re-fetch", async () => {
  freshDom();
  const Audio = makeAudioCtor();
  globalThis.Audio = Audio;
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "lento", lang: "es" });
  assert(fetchCalls.length === 1, "one fetch on open");
  assert(Audio.calls.created === 1, `expected 1 Audio, got ${Audio.calls.created}`);

  const popup = document.getElementById("tts-popup");
  const slowBtn = popup.querySelector('[data-tts-speed-value="0.5"]');
  assert(slowBtn, "0.5x button present");
  slowBtn.click();

  // Speed change must not trigger another fetch — the whole point of
  // the client-side playbackRate approach.
  assert(fetchCalls.length === 1, `speed change must not re-fetch, got ${fetchCalls.length}`);
  assert(slowBtn.classList.contains("tts-popup__speed-btn--active"),
    "0.5x button now active");
  const oneX = popup.querySelector('[data-tts-speed-value="1"]');
  assert(!oneX.classList.contains("tts-popup__speed-btn--active"),
    "1x button no longer active");
});

await testAsync("play button toggles Play / Pause label and pauses the audio", async () => {
  freshDom();
  const Audio = makeAudioCtor();
  globalThis.Audio = Audio;
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "uno", lang: "es" });
  const popup = document.getElementById("tts-popup");
  const playBtn = popup.querySelector('[data-tts-action="play"]');
  const labelEl = popup.querySelector("[data-tts-play-label]");
  assert(labelEl, "play label span present");
  // After auto-play on open, the label should read "Pause".
  assert(labelEl.textContent === "Pause", `label after auto-play is Pause, got "${labelEl.textContent}"`);

  // Click → should pause and relabel.
  playBtn.click();
  assert(labelEl.textContent === "Resume", `label after pause is Resume, got "${labelEl.textContent}"`);

  // Click again → should resume.
  playBtn.click();
  assert(labelEl.textContent === "Pause", `label after resume is Pause, got "${labelEl.textContent}"`);
});

await testAsync("closeTtsPopup hides the popup and revokes the object URL", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup, closeTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "cerrar", lang: "es" });
  const popup = document.getElementById("tts-popup");
  assert(!popup.hidden, "popup open before close");

  closeTtsPopup();

  assert(popup.hidden, "popup hidden after close");
  assert(!popup.classList.contains("tts-popup--open"), "--open class removed");
  assert(revokedUrls >= 1, `expected object URL revocation, got ${revokedUrls}`);
});

await testAsync("Escape key dismisses the popup", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "escape", lang: "es" });
  const popup = document.getElementById("tts-popup");
  assert(!popup.hidden, "popup open");

  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));

  assert(popup.hidden, "popup hidden after Escape");
});

await testAsync("fetch failure does not throw and still mounts the popup", async () => {
  freshDom();
  fetchCalls = [];
  globalThis.fetch = async () => ({
    ok: false,
    status: 502,
    json: async () => ({ ok: false, error: "TTS blew up" }),
  });
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  // Should resolve cleanly (not throw) even when fetch fails.
  await openAndFlush(openTtsPopup, { word: "fallo", lang: "es" });
  const popup = document.getElementById("tts-popup");
  assert(popup, "popup still mounted on failure");
});

// --- Selection guard ---------------------------------------------------
//
// The popup must refuse very long or punctuation-heavy selections with
// a friendly inline message instead of firing a request the backend
// would reject with a terse 400.

await testAsync("selection guard: selection past 200 chars shows a friendly note and does not fetch", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  const longText = "a".repeat(500);
  await openAndFlush(openTtsPopup, { word: longText, lang: "en" });

  assert(fetchCalls.length === 0,
    `must not fetch for unsupported selection, got ${fetchCalls.length}`);

  const popup = document.getElementById("tts-popup");
  assert(popup, "popup still mounted");
  const note = popup.querySelector("[data-tts-note]");
  assert(note, "note element rendered for unsupported selection");
  assert(/200/.test(note.textContent),
    `note explains the limit, got "${note.textContent}"`);
  assert(!popup.querySelector('[data-tts-action="play"]'),
    "no play button shown for unsupported selection");
});

await testAsync("selection guard: sentences with punctuation are accepted", async () => {
  // Commas, periods, em-dashes, question marks, apostrophes — all
  // the punctuation users actually type in real sentences should
  // pass through to the TTS endpoint.
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  const sentence = "Hello, world — what's up?";
  await openAndFlush(openTtsPopup, { word: sentence, lang: "en" });

  assert(fetchCalls.length === 1,
    `must fetch once for punctuated sentence, got ${fetchCalls.length}`);
  const popup = document.getElementById("tts-popup");
  assert(!popup.querySelector("[data-tts-note]"),
    "no rejection note shown for punctuated sentence");
  const textEl = popup.querySelector("[data-tts-text]");
  assert(textEl.textContent === sentence,
    `popup shows the sentence, got "${textEl.textContent}"`);
});

await testAsync("selection guard: characters the TTS engine can't read are still rejected", async () => {
  // Emoji, URLs, control chars — these will trip up Google's TTS
  // even if they pass the 200-char check, so reject them inline
  // with the same friendly note as before.
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, {
    word: "hello 😀 world",
    lang: "en",
  });

  assert(fetchCalls.length === 0,
    `must not fetch for emoji-containing selection, got ${fetchCalls.length}`);
  const popup = document.getElementById("tts-popup");
  const note = popup.querySelector("[data-tts-note]");
  assert(note, "note element rendered");
  assert(/punctuation|symbols/i.test(note.textContent),
    `note explains the limit, got "${note.textContent}"`);
});

await testAsync("selection guard: exactly at the 200-char cap is accepted", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  const text = "a".repeat(200);
  await openAndFlush(openTtsPopup, { word: text, lang: "en" });

  assert(fetchCalls.length === 1, `200-char selection must fetch, got ${fetchCalls.length}`);
  assert(!document.getElementById("tts-popup").querySelector("[data-tts-note]"),
    "no rejection note at exactly 200 chars");
});

await testAsync("selection guard: 201 chars is rejected", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  const text = "a".repeat(201);
  await openAndFlush(openTtsPopup, { word: text, lang: "en" });

  assert(fetchCalls.length === 0,
    `201 chars must not fetch, got ${fetchCalls.length}`);
  const popup = document.getElementById("tts-popup");
  const note = popup.querySelector("[data-tts-note]");
  assert(note, "note element rendered");
  assert(/200/.test(note.textContent),
    `note mentions the 200 char limit, got "${note.textContent}"`);
});

// --- Drag + persistent position ---------------------------------------

await testAsync("drag: mousedown on the chrome moves the popup", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "mover", lang: "es" });
  const popup = document.getElementById("tts-popup");
  const handle = popup.querySelector("[data-popup-handle]");
  assert(handle, "drag handle (chrome) present");

  // Pretend the popup currently sits at (200, 150). Simulate grabbing
  // it at the chrome's top-left and dragging 100px right / 50px down.
  popup.getBoundingClientRect = () => ({ left: 200, top: 150, right: 560, bottom: 300, width: 360, height: 150 });
  handle.dispatchEvent(new window.MouseEvent("mousedown", { button: 0, clientX: 200, clientY: 150 }));
  document.dispatchEvent(new window.MouseEvent("mousemove", { clientX: 300, clientY: 200 }));
  document.dispatchEvent(new window.MouseEvent("mouseup", { clientX: 300, clientY: 200 }));

  assert(popup.style.left === "300px", `popup left updated, got "${popup.style.left}"`);
  assert(popup.style.top === "200px", `popup top updated, got "${popup.style.top}"`);
  assert(popup.style.right === "auto", `popup right cleared, got "${popup.style.right}"`);
});

await testAsync("drag: mousedown on the close button does NOT start a drag", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "cerrar", lang: "es" });
  const popup = document.getElementById("tts-popup");
  const closeBtn = popup.querySelector(".tts-popup__close");
  assert(closeBtn, "close button present");

  closeBtn.dispatchEvent(new window.MouseEvent("mousedown", { button: 0, bubbles: true }));
  // The popup should still have its default positioning — no inline
  // left/top set by a drag handler.
  assert(!popup.style.left, `no drag from close button, left="${popup.style.left}"`);
  assert(!popup.style.top, `no drag from close button, top="${popup.style.top}"`);
});

await testAsync("drag: persisted position is restored on next open", async () => {
  freshDom();
  globalThis.Audio = makeAudioCtor();
  const { openTtsPopup, closeTtsPopup } =
    await importFresh("../frontend/static/js/components/tts-popup.js");

  // Seed localStorage with a saved position before opening.
  window.localStorage.setItem(
    "langlearn:tts-popup:pos:v1",
    JSON.stringify({ left: 123, top: 45 })
  );

  await openAndFlush(openTtsPopup, { word: "uno", lang: "es" });

  const popup = document.getElementById("tts-popup");
  // applyPosition reads the popup's size to clamp the saved offset
  // into the current viewport; jsdom returns zeros so the size is
  // effectively unlimited here, which lets the saved coords land
  // verbatim.
  popup.getBoundingClientRect = () =>
    ({ left: 0, top: 0, right: 360, bottom: 150, width: 360, height: 150 });

  // Close and re-open — the second open must apply the saved offset.
  closeTtsPopup();
  // Re-seed, since jsdom's zero-rect may have caused the popup to
  // overwrite the saved value with {0, 0} during the first open.
  window.localStorage.setItem(
    "langlearn:tts-popup:pos:v1",
    JSON.stringify({ left: 123, top: 45 })
  );
  await openAndFlush(openTtsPopup, { word: "dos", lang: "es" });

  const popup2 = document.getElementById("tts-popup");
  assert(popup2.style.left === "123px",
    `restored left, got "${popup2.style.left}"`);
  assert(popup2.style.top === "45px",
    `restored top, got "${popup2.style.top}"`);
  assert(popup2.style.right === "auto",
    `default right anchor cleared, got "${popup2.style.right}"`);
});

// --- Re-open with a new word ------------------------------------------

await testAsync("REGRESSION: re-opening with a new word plays the new audio, not the old", async () => {
  // The user reported: open the popup, listen to word A, then right-click
  // another selection and choose Speak again. The popup updated to show
  // word B but kept playing A's audio. Root cause was that playOnce()
  // reused the cached <audio> element and just rewound it, never
  // fetching a fresh audio source for the new word.
  freshDom();
  const Audio = makeAudioCtor();
  globalThis.Audio = Audio;
  const { openTtsPopup } = await importFresh("../frontend/static/js/components/tts-popup.js");

  await openAndFlush(openTtsPopup, { word: "primero", lang: "es" });
  assert(Audio.calls.created === 1, "first open created one Audio");
  assert(Audio.calls.created === 1, "first open created one Audio");

  await openAndFlush(openTtsPopup, { word: "segundo", lang: "es" });

  // A fresh audio element must be created — the old one is dropped
  // because openTtsPopup now calls stopAudio() before playOnce().
  assert(Audio.calls.created === 2,
    `second open must create a fresh Audio, got ${Audio.calls.created}`);

  // Two fetches total — one per word.
  assert(fetchCalls.length === 2,
    `two audio fetches, got ${fetchCalls.length}`);

  // The second fetch must be for the new word, not the old.
  const secondUrl = fetchCalls[1].url;
  assert(/word=segundo/.test(secondUrl),
    `second fetch is for new word, got ${secondUrl}`);
  assert(!/word=primero/.test(secondUrl),
    `second fetch must not be for old word, got ${secondUrl}`);

  // The popup must also display the new word, not the old.
  const popup = document.getElementById("tts-popup");
  const textEl = popup.querySelector("[data-tts-text]");
  assert(textEl.textContent === "segundo",
    `popup shows new word, got "${textEl.textContent}"`);
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
