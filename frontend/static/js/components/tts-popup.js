// Floating TTS (text-to-speech) card. Mirrors the dict-popup pattern
// so a right-click "Speak" feels identical to a right-click "Lookup",
// but instead of a dictionary entry it shows an audio player for the
// selected word or phrase with play/pause/replay and a 0.5x / 1x / 1.5x
// segmented speed selector.
//
// Speed is applied client-side via `audio.playbackRate`; we don't
// re-fetch from the server at a different rate, so switching speeds is
// instant and doesn't grow the disk cache.

import { store } from "../state.js";
import { toast } from "./toast.js";

const POPUP_ID = "tts-popup";
const ESC_KEY = "Escape";

const SPEED_OPTIONS = [
  { value: 0.5, label: "0.5x" },
  { value: 1.0, label: "1x" },
  { value: 1.5, label: "1.5x" },
];

let popupEl = null;
let popupBody = null;
let audio = null;            // currently-playing HTMLAudioElement
let audioUrl = null;         // object URL backing `audio`
let currentSpeed = 1.0;
let onDismissCb = null;

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function activeLanguage() {
  const s = store.get().settings || {};
  return s.active_language || "en";
}

// Length cap matches the backend's word validator (200 chars) and
// what Google TTS will reliably synthesize as a single phrase. Past
// ~200 chars the audio quality drops noticeably and Google often
// truncates mid-word, so longer selections are still rejected.
const MAX_PHRASE_CHARS = 200;
// Accept common sentence punctuation so a paragraph selection reads
// naturally. Letters/numbers/whitespace plus the usual sentence
// punctuation (commas, periods, dashes, question/exclamation marks,
// quotes, parentheses, ellipsis). Anything weirder (emoji, math,
// URLs, control chars) is rejected with a friendly note rather
// than a server 400.
const ACCEPTABLE_RE = /^[\p{L}\p{N}\s'",\.!\?:;\-—–…()"«»]+$/u;

function isSpeakable(text) {
  if (!text) return { ok: false, reason: "empty" };
  if (text.length > MAX_PHRASE_CHARS) {
    return { ok: false, reason: "too_long" };
  }
  if (!ACCEPTABLE_RE.test(text)) {
    return { ok: false, reason: "punctuation" };
  }
  return { ok: true };
}

/**
 * Open (or refresh) the TTS popup for a word or phrase. Idempotent:
 * opening again while one is showing replaces its contents in place.
 *
 * @param {object} opts
 * @param {string} opts.word       - the word or phrase to speak.
 * @param {string} [opts.lang]     - override the active language.
 * @param {Function} [opts.onDismiss] - called when the popup is closed.
 */
export async function openTtsPopup({ word, lang, onDismiss } = {}) {
  const text = String(word || "").trim();
  if (!text) return;
  ensurePopup();
  onDismissCb = onDismiss || null;
  currentSpeed = 1.0;
  // Drop any audio from a previous open so the popup actually plays
  // the new word — without this, playOnce() would reuse the old
  // <audio> element and just rewind it, leaving the user staring at
  // the new word's display while the previous audio keeps playing.
  stopAudio();
  const lg = lang || activeLanguage();

  // If the selection is too long or contains punctuation Google TTS
  // can't pronounce, show a friendly inline note instead of firing
  // a request the backend will reject.
  const speakable = isSpeakable(text);
  if (!speakable.ok) {
    showPopup();
    renderUnsupported(text, speakable.reason);
    return;
  }
  showPopup();
  renderBody(text, lg);
  await playOnce(text, lg);
}

/**
 * Close the popup if it's open. No-op when not open.
 */
export function closeTtsPopup() {
  if (!popupEl) return;
  dismiss();
}

function ensurePopup() {
  if (popupEl && document.body.contains(popupEl)) return;
  popupEl = document.createElement("div");
  popupEl.id = POPUP_ID;
  popupEl.className = "tts-popup";
  popupEl.setAttribute("role", "dialog");
  popupEl.setAttribute("aria-modal", "false");
  popupEl.setAttribute("aria-label", "Pronunciation");
  popupEl.innerHTML = `
    <div class="tts-popup__chrome" data-popup-handle>
      <span class="tts-popup__title">Pronunciation</span>
      <button type="button" class="tts-popup__close" aria-label="Close">×</button>
    </div>
    <div class="tts-popup__body" data-popup-body></div>
  `;
  document.body.appendChild(popupEl);
  popupBody = popupEl.querySelector("[data-popup-body]");
  popupEl.querySelector(".tts-popup__close").addEventListener("click", dismiss);
  bindDrag(popupEl.querySelector("[data-popup-handle]"));
  popupEl.addEventListener("mousedown", (e) => {
    // Same scrim-click trick as dict-popup: clicks on the bare outer
    // box (i.e. not on the chrome or body) dismiss.
    if (e.target === popupEl) dismiss();
  });
  document.addEventListener("keydown", onKeyDown);
}

function showPopup() {
  if (!popupEl) return;
  applyPosition();
  popupEl.hidden = false;
  requestAnimationFrame(() => {
    if (popupEl) popupEl.classList.add("tts-popup--open");
  });
}

// --- Drag + persistent position ----------------------------------------
//
// Mirrors the dict-popup behavior: grab the title bar to move, persist
// the position to localStorage, clamp to the current viewport on
// re-open so a position saved on a large screen doesn't hide the
// popup on a small one.

function bindDrag(handle) {
  if (!handle) return;
  handle.addEventListener("mousedown", (e) => {
    // Don't start a drag when the user grabs the close button.
    if (e.target.closest(".tts-popup__close")) return;
    if (e.button !== 0) return;
    e.preventDefault();
    startDrag(e.clientX, e.clientY);
  });
}

function startDrag(startX, startY) {
  if (!popupEl) return;
  const rect = popupEl.getBoundingClientRect();
  const dx = startX - rect.left;
  const dy = startY - rect.top;
  popupEl.classList.add("tts-popup--dragging");
  const onMove = (ev) => {
    const left = clamp(ev.clientX - dx, -rect.width + 80, window.innerWidth - 80);
    const top = clamp(ev.clientY - dy, 0, window.innerHeight - 40);
    popupEl.style.left = `${left}px`;
    popupEl.style.top = `${top}px`;
    popupEl.style.right = "auto";
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    popupEl.classList.remove("tts-popup--dragging");
    persistPosition();
  };
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

const POS_KEY = "langlearn:tts-popup:pos:v1";

function loadPosition() {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (typeof p.left !== "number" || typeof p.top !== "number") return null;
    return p;
  } catch (e) {
    return null;
  }
}

function persistPosition() {
  if (!popupEl) return;
  const rect = popupEl.getBoundingClientRect();
  try {
    localStorage.setItem(POS_KEY, JSON.stringify({ left: rect.left, top: rect.top }));
  } catch (e) { /* ignore */ }
}

function applyPosition() {
  if (!popupEl) return;
  // Reset to default anchor before applying a stored offset.
  popupEl.style.left = "";
  popupEl.style.top = "";
  popupEl.style.right = "var(--sp-4)";
  const saved = loadPosition();
  if (!saved) return;
  const rect = popupEl.getBoundingClientRect();
  const maxLeft = Math.max(0, window.innerWidth - Math.min(rect.width, 120));
  const maxTop = Math.max(0, window.innerHeight - Math.min(rect.height, 40));
  const left = clamp(saved.left, 0, maxLeft);
  const top = clamp(saved.top, 0, maxTop);
  popupEl.style.left = `${left}px`;
  popupEl.style.top = `${top}px`;
  popupEl.style.right = "auto";
}

function hidePopup() {
  if (!popupEl) return;
  popupEl.classList.remove("tts-popup--open");
  popupEl.hidden = true;
}

function dismiss() {
  if (!popupEl) return;
  hidePopup();
  stopAudio();
  if (typeof onDismissCb === "function") {
    const cb = onDismissCb;
    onDismissCb = null;
    try { cb(); } catch (e) { console.error("tts popup onDismiss failed", e); }
  }
}

function onKeyDown(e) {
  if (e.key === ESC_KEY && popupEl && !popupEl.hidden) {
    dismiss();
  }
}

function renderUnsupported(text, reason) {
  if (!popupBody) return;
  const display = text.length > 120 ? text.slice(0, 120) + "…" : text;
  const reasonText = reason === "too_long"
    ? `That selection is ${text.length} characters long — the pronunciation service reads up to ${MAX_PHRASE_CHARS} characters at a time. Try a shorter selection.`
    : "That selection contains punctuation or symbols the pronunciation service can't read. Try a single word or a short phrase.";
  popupBody.innerHTML = `
    <p class="tts-popup__text" data-tts-text>${escapeHtml(display)}</p>
    <p class="tts-popup__note" data-tts-note>${escapeHtml(reasonText)}</p>
  `;
}

function renderBody(text, lang) {
  if (!popupBody) return;
  const display = text.replace(/_/g, " ");
  popupBody.innerHTML = `
    <p class="tts-popup__text" data-tts-text>${escapeHtml(display)}</p>
    <div class="tts-popup__controls" data-tts-controls>
      <button type="button" class="btn btn--primary" data-tts-action="play"
              aria-label="Play">
        <span data-tts-play-label>Play</span>
      </button>
      <button type="button" class="btn btn--ghost" data-tts-action="replay"
              aria-label="Replay from start">
        Replay
      </button>
    </div>
    <div class="tts-popup__speed" role="group" aria-label="Playback speed"
         data-tts-speed>
      ${SPEED_OPTIONS.map((opt) => `
        <button type="button" class="tts-popup__speed-btn${opt.value === currentSpeed ? " tts-popup__speed-btn--active" : ""}"
                data-tts-speed-value="${opt.value}"
                aria-pressed="${opt.value === currentSpeed ? "true" : "false"}">
          ${escapeHtml(opt.label)}
        </button>
      `).join("")}
    </div>
  `;
  bindControls(text, lang);
}

function bindControls(text, lang) {
  const playBtn = popupBody.querySelector('[data-tts-action="play"]');
  const replayBtn = popupBody.querySelector('[data-tts-action="replay"]');
  if (playBtn) {
    playBtn.addEventListener("click", () => {
      if (audio && !audio.paused) {
        pauseAudio();
        updatePlayLabel("Resume");
      } else if (audio && audio.paused && audio.currentTime > 0) {
        resumeAudio();
        updatePlayLabel("Pause");
      } else {
        // No audio loaded yet (still fetching) or audio ended — restart.
        playOnce(text, lang);
        updatePlayLabel("Pause");
      }
    });
  }
  if (replayBtn) {
    replayBtn.addEventListener("click", () => {
      replayAudio();
      updatePlayLabel("Pause");
    });
  }
  popupBody.querySelectorAll("[data-tts-speed-value]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const v = parseFloat(btn.dataset.ttsSpeedValue);
      if (!Number.isFinite(v)) return;
      setSpeed(v);
    });
  });
}

function setSpeed(v) {
  currentSpeed = v;
  if (audio) audio.playbackRate = v;
  if (!popupBody) return;
  popupBody.querySelectorAll("[data-tts-speed-value]").forEach((btn) => {
    const matches = parseFloat(btn.dataset.ttsSpeedValue) === v;
    btn.setAttribute("aria-pressed", matches ? "true" : "false");
    btn.classList.toggle("tts-popup__speed-btn--active", matches);
  });
}

function updatePlayLabel(label) {
  if (!popupBody) return;
  const el = popupBody.querySelector("[data-tts-play-label]");
  if (el) el.textContent = label;
}

async function playOnce(text, lang) {
  try {
    if (!audio || !audioUrl) {
      const url = await fetchAudioUrl(text, lang);
      if (!url) return;
      audio = new Audio(url);
      audioUrl = url;
      audio.playbackRate = currentSpeed;
      audio.addEventListener("ended", () => {
        updatePlayLabel("Play");
      }, { once: true });
      audio.addEventListener("error", () => {
        toast({ title: "Couldn't play pronunciation",
                message: "Audio playback error.",
                variant: "error", ttl: 3000 });
        updatePlayLabel("Play");
      }, { once: true });
    } else {
      audio.currentTime = 0;
    }
    updatePlayLabel("Pause");
    const p = audio.play();
    if (p && typeof p.catch === "function") {
      p.catch((e) => {
        toast({ title: "Couldn't play pronunciation",
                message: String(e), variant: "error", ttl: 3000 });
        updatePlayLabel("Play");
      });
    }
  } catch (e) {
    console.error("tts play failed", e);
    toast({ title: "Couldn't play pronunciation",
            message: String(e), variant: "error", ttl: 3000 });
    updatePlayLabel("Play");
  }
}

function pauseAudio() {
  if (audio) audio.pause();
}

function resumeAudio() {
  if (!audio) return;
  const p = audio.play();
  if (p && typeof p.catch === "function") {
    p.catch((e) => {
      toast({ title: "Couldn't play pronunciation",
              message: String(e), variant: "error", ttl: 3000 });
    });
  }
}

function replayAudio() {
  if (!audio) return;
  audio.currentTime = 0;
  const p = audio.play();
  if (p && typeof p.catch === "function") {
    p.catch((e) => {
      toast({ title: "Couldn't play pronunciation",
              message: String(e), variant: "error", ttl: 3000 });
    });
  }
}

function stopAudio() {
  try {
    if (audio) {
      audio.pause();
      audio.src = "";
    }
  } catch (_) { /* */ }
  if (audioUrl) {
    try { URL.revokeObjectURL(audioUrl); } catch (_) { /* */ }
  }
  audio = null;
  audioUrl = null;
}

async function fetchAudioUrl(text, lang) {
  const word = text.replace(/\s+/g, "_");
  const qs = `lang=${encodeURIComponent(lang)}&word=${encodeURIComponent(word)}`;
  let resp;
  try {
    resp = await fetch(`/api/tts/audio?${qs}`, {
      method: "GET",
      credentials: "same-origin",
    });
  } catch (e) {
    toast({ title: "Couldn't reach the pronunciation service",
            message: "Network error.", variant: "error", ttl: 3000 });
    return null;
  }
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      if (data && data.error) msg = data.error;
    } catch (_) { /* non-JSON error body */ }
    toast({ title: "Pronunciation unavailable", message: msg,
            variant: "error", ttl: 3000 });
    return null;
  }
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}
