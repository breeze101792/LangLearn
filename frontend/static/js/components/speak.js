// Pronunciation (TTS) helper. Wires up the speaker buttons rendered by
// `word-card.js` so a click fetches the audio and plays it.
//
// We use raw `fetch` instead of `api.js` because the response is binary
// audio, not the {ok, data} JSON envelope. The endpoint is `/api/tts/audio`
// and lives behind the same auth gate as everything else.
//
// Audio is cached per session as a blob URL so repeat plays don't re-hit
// the network. The backend also keeps a long-lived disk cache; the two
// compose.

import { toast } from "./toast.js";

const blobCache = new Map();   // key -> { url, contentType }
let activeAudio = null;         // currently-playing HTMLAudioElement

/**
 * Wire every `[data-action="speak"]` button inside `root`. Call this after
 * rendering any DOM that contains a word card.
 */
export function bindSpeakButtons(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('[data-action="speak"]').forEach((btn) => {
    if (btn.dataset.speakBound === "1") return;
    btn.dataset.speakBound = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const lang = btn.dataset.lang || "";
      const word = btn.dataset.word || "";
      if (!lang || !word) return;
      speak(btn, lang, word);
    });
  });
}

async function speak(btn, lang, word) {
  if (btn.disabled) return;
  const key = `${lang}:${word.toLowerCase()}`;
  const setBusy = (on) => {
    btn.disabled = on;
    btn.classList.toggle("word-card__speak--busy", on);
    btn.setAttribute("aria-busy", on ? "true" : "false");
  };
  setBusy(true);
  try {
    let url;
    let contentType = "audio/mpeg";
    const cached = blobCache.get(key);
    if (cached) {
      url = cached.url;
      contentType = cached.contentType;
    } else {
      const res = await fetchAudio(lang, word);
      if (!res) return;  // toast already shown
      const blob = await res.blob();
      contentType = res.headers.get("content-type") || contentType;
      url = URL.createObjectURL(blob);
      blobCache.set(key, { url, contentType });
    }
    play(url);
  } catch (e) {
    console.error("tts play failed", e);
    toast({ title: "Couldn't play pronunciation", message: String(e),
            variant: "error", ttl: 3000 });
  } finally {
    setBusy(false);
  }
}

async function fetchAudio(lang, word) {
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
  return resp;
}

function play(url) {
  try {
    if (activeAudio) {
      activeAudio.pause();
      activeAudio.src = "";
    }
  } catch (_) { /* */ }
  const audio = new Audio(url);
  activeAudio = audio;
  // Autoplay may be blocked until the user has interacted; the button
  // click counts, so .play() should resolve.
  const p = audio.play();
  if (p && typeof p.catch === "function") {
    p.catch((e) => {
      toast({ title: "Couldn't play pronunciation", message: String(e),
              variant: "error", ttl: 3000 });
    });
  }
  audio.addEventListener("ended", () => {
    if (activeAudio === audio) activeAudio = null;
  }, { once: true });
}
