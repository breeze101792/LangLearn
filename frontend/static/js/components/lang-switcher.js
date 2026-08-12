// Language switcher dropdown.

import { store } from "../state.js";
import { api } from "../api.js";
import { toast } from "./toast.js";

let mounted = null;

export function renderLangSwitcher() {
  const host = document.getElementById("lang-switcher");
  if (!host) return;
  host.innerHTML = "";
  const s = store.get();
  const active = s.activeLanguage || (s.settings && s.settings.active_language) || "en";
  const languages = (s.languages || []).filter((l) => l.seeded || l.code === active);
  if (!languages.length) return;

  const wrap = document.createElement("div");
  wrap.className = "lang-switcher";
  wrap.innerHTML = `
    <button class="lang-switcher__trigger" type="button" aria-haspopup="menu" aria-expanded="false">
      <span>${displayFor(active)} ▾</span>
    </button>
    <div class="lang-switcher__menu" role="menu">
      ${languages.map((l) => `
        <button class="lang-switcher__item ${l.code === active ? "lang-switcher__item--active" : ""}" role="menuitemradio" aria-checked="${l.code === active}" data-code="${l.code}">
          <span>${l.display_name}</span>
          <span style="color:var(--text-muted); font-size:12px;">${l.code}${l.seeded ? "" : " •"}</span>
        </button>
      `).join("")}
    </div>
  `;
  host.appendChild(wrap);

  const trigger = wrap.querySelector(".lang-switcher__trigger");
  const menu = wrap.querySelector(".lang-switcher__menu");
  trigger.addEventListener("click", () => {
    const open = wrap.classList.toggle("lang-switcher--open");
    trigger.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => {
    if (!wrap.contains(e.target)) {
      wrap.classList.remove("lang-switcher--open");
      trigger.setAttribute("aria-expanded", "false");
    }
  });
  menu.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-code]");
    if (!btn) return;
    const code = btn.dataset.code;
    if (!code || code === active) return;
    const upd = await api.put("/api/settings", { active_language: code });
    if (upd.ok) {
      store.set({ settings: upd.data, activeLanguage: code });
      renderLangSwitcher();
      document.dispatchEvent(new CustomEvent("app:language-changed", { detail: code }));
    } else {
      toast({ title: "Could not switch language", message: upd.error, variant: "error" });
    }
  });

  mounted = wrap;
}

function displayFor(code) {
  const s = store.get();
  const lang = (s.languages || []).find((l) => l.code === code);
  return lang ? `${lang.display_name}` : code.toUpperCase();
}