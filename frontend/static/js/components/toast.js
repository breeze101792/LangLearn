// Toast queue with auto-dismiss and optional Undo.

let stackEl = null;
const queue = [];

function ensureStack() {
  if (stackEl) return stackEl;
  stackEl = document.getElementById("toast-stack");
  return stackEl;
}

export function toast({ title, message, variant = "info", ttl = 5000, actions = [] } = {}) {
  const root = ensureStack();
  if (!root) return;
  const el = document.createElement("div");
  el.className = `toast toast--${variant}`;
  el.setAttribute("role", variant === "error" ? "alert" : "status");
  const titleEl = document.createElement("div");
  titleEl.className = "toast__title";
  titleEl.textContent = title || "";
  const msgEl = document.createElement("div");
  msgEl.className = "toast__msg";
  msgEl.textContent = message || "";
  el.appendChild(titleEl);
  if (message) el.appendChild(msgEl);
  let progressEl = null;
  if (ttl > 0) {
    progressEl = document.createElement("div");
    progressEl.className = "toast__progress";
    el.appendChild(progressEl);
  }
  if (actions.length) {
    const actionsEl = document.createElement("div");
    actionsEl.className = "toast__actions";
    actions.forEach((a) => {
      const b = document.createElement("button");
      b.className = "btn btn--ghost btn--sm";
      b.textContent = a.label;
      b.addEventListener("click", () => {
        try { a.onClick?.(); } catch (e) { console.error(e); }
        dismiss();
      });
      actionsEl.appendChild(b);
    });
    el.appendChild(actionsEl);
  }
  root.appendChild(el);
  queue.push(el);
  while (queue.length > 3) {
    const old = queue.shift();
    dismissEl(old, /* instant */ true);
  }

  const startedAt = Date.now();
  let raf = 0;
  function tick() {
    if (!progressEl || !document.body.contains(el)) return;
    const pct = Math.max(0, 1 - (Date.now() - startedAt) / ttl);
    progressEl.style.width = `${(pct * 100).toFixed(1)}%`;
    if (pct <= 0) {
      dismiss();
      return;
    }
    raf = requestAnimationFrame(tick);
  }
  if (progressEl) raf = requestAnimationFrame(tick);

  function dismiss() {
    cancelAnimationFrame(raf);
    dismissEl(el);
  }
  if (ttl > 0) setTimeout(dismiss, ttl);
  return dismiss;
}

function dismissEl(el, instant = false) {
  if (!el || !document.body.contains(el)) return;
  if (instant) {
    el.remove();
    return;
  }
  el.style.transition = "opacity 180ms";
  el.style.opacity = "0";
  setTimeout(() => el.remove(), 200);
}