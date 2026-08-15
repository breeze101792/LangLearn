// Phrases list page.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";
import { consumeRestoredState } from "../components/page-state.js";

export function renderPhrases(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const primary = (state.settings && state.settings.explanation_primary) || null;
  const secondary = (state.settings && state.settings.explanation_secondary) || null;
  const restored = consumeRestoredState();

  const fields = [
    { key: "phrase",               label: "Phrase (in target language)", max: 500, required: true },
    { key: "example_sentence",     label: "Example sentence (in target language, showing the phrase in use)", max: 1000, required: true },
    { key: "explanation",          label: "Explanation (in target language, paragraph)", max: 1500, required: true },
  ];
  if (!primary || primary !== lang) {
    fields.push({ key: "explanation_primary", label: `Explanation (in ${labelFor(primary, "primary native")})`, max: 1000 });
  }
  if (secondary && secondary !== primary && secondary !== lang) {
    fields.push({ key: "explanation_secondary", label: `Explanation (in ${labelFor(secondary, "secondary native")})`, max: 1000 });
  }

  renderListPage({
    host,
    title: "Phrases",
    kind: "phrases",
    endpoint: "/api/phrases",
    fillEndpoint: "/api/phrases/fill",
    emptyMsg: "No phrases for this language yet.",
    emptyActions: true,
    rowRenderer: renderPhraseRow,
    fields,
    addBody: (lang, form) => ({
      language: lang,
      phrase: form.phrase,
      example_sentence: form.example_sentence,
      explanation: form.explanation,
      explanation_primary: form.explanation_primary,
      explanation_secondary: form.explanation_secondary,
      source: "user",
    }),
    restored,
  });
}

function labelFor(code, fallback) {
  const map = { en: "English", es: "Spanish", ja: "Japanese", pt: "Portuguese", zh: "Traditional Chinese", fr: "French", de: "German" };
  return map[code] || fallback;
}

function renderPhraseRow(item) {
  const isBuiltIn = item.source === "built-in";
  const isFamiliar = !!item.familiar;
  const badge = isBuiltIn
    ? `<span class="badge badge--builtin">Built-in</span><span class="badge badge--muted">Read-only</span>`
    : (item.source === "llm"
        ? `<span class="badge badge--user">You</span><span class="badge badge--ai">AI</span>`
        : `<span class="badge badge--user">You</span>`);
  const familiarBadge = isFamiliar ? `<span class="badge badge--ok">Familiar</span>` : "";
  const rememberBtn = `
    <button class="btn btn--ghost btn--sm remember-btn ${isFamiliar ? "remember-btn--on" : ""}"
            data-action="remember"
            data-id="${item.id}"
            aria-pressed="${isFamiliar}"
            title="${isFamiliar ? "Mark as unfamiliar" : "I remember this — hide from the list"}"
            aria-label="${isFamiliar ? "Mark as unfamiliar" : "Mark as familiar"}">
      <span aria-hidden="true">✓</span>
    </button>
  `;
  return `
    <article class="list-item ${isBuiltIn ? "list-item--builtin" : ""} ${isFamiliar ? "list-item--familiar" : ""}" data-id="${item.id}">
      <div class="list-item__badges">${badge}${familiarBadge}</div>
      <div class="list-item__main"><strong>"${escapeHtml(item.phrase || "")}"</strong></div>
      ${exampleSentenceLine(item)}
      ${item.explanation
        ? `<div class="list-item__meta list-item__meta--target" title="In the target language">
             <span class="list-item__meta-label">Explanation:</span>
             ${escapeHtml(item.explanation)}
           </div>`
        : ""}
      ${item.explanation_primary
        ? `<div class="list-item__meta list-item__meta--native" title="In your primary native language"><span class="list-item__meta-label">${escapeHtml(labelForTarget(item.language))}:</span> ${escapeHtml(item.explanation_primary)}</div>`
        : ""}
      ${item.explanation_secondary
        ? `<div class="list-item__meta list-item__meta--native" title="In your secondary native language"><span class="list-item__meta-label">${escapeHtml(labelForTarget(item.language, true))}:</span> ${escapeHtml(item.explanation_secondary)}</div>`
        : ""}
      <div class="list-item__actions">
        ${rememberBtn}
        ${isBuiltIn ? "" : `<button class="btn btn--ghost btn--sm" data-action="edit">Edit</button><button class="btn btn--ghost btn--sm" data-action="delete">Delete</button>`}
      </div>
    </article>
  `;
}

function exampleSentenceLine(item) {
  const sentence = item.example_sentence;
  if (!sentence) {
    return `<div class="list-item__meta list-item__meta--missing" title="In the target language">Example: <em>(missing)</em></div>`;
  }
  return `<div class="list-item__meta list-item__meta--target" title="In the target language"><span class="list-item__meta-label">Example:</span> <code>${escapeHtml(sentence)}</code></div>`;
}

function labelForTarget(lang, secondary = false) {
  const state = store.get();
  const code = secondary
    ? (state.settings && state.settings.explanation_secondary) || null
    : (state.settings && state.settings.explanation_primary) || null;
  const map = { en: "English", es: "Spanish", ja: "Japanese",
                pt: "Portuguese", zh: "Traditional Chinese",
                fr: "French", de: "German" };
  if (code && map[code]) return map[code];
  return secondary ? "Native 2" : "Native 1";
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// shared generic list+add page
function renderListPage({ host, title, kind, endpoint, fillEndpoint, emptyMsg, emptyActions, rowRenderer, fields, addBody, restored }) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const pageSize = (state.settings && state.settings.page_size) || 20;
  let viewFamiliar = false;
  let offset = 0;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">${title}</h1>
      <p class="page-head__subtitle">Common ${title.toLowerCase()} for your active language.</p>
    </header>
    <section class="row row--between" style="margin-bottom: var(--sp-3); align-items: center; gap: var(--sp-3)">
      <div class="segmented" id="familiar-segments" role="tablist" aria-label="Familiarity filter">
        <button class="segmented__item is-active" data-familiar="0" role="tab" aria-selected="true">Unfamiliar</button>
        <button class="segmented__item" data-familiar="1" role="tab" aria-selected="false">Familiar</button>
      </div>
      <button id="add-toggle" class="btn btn--primary">+ Add</button>
    </section>
    <section id="add-panel" style="display: none"></section>
    <section id="list"></section>
  `;

  document.getElementById("add-toggle").addEventListener("click", () => {
    const panel = document.getElementById("add-panel");
    if (panel.style.display === "none") {
      renderAddForm(panel, lang, fields, fillEndpoint, addBody, endpoint, async () => {
        panel.style.display = "none";
        await load();
      });
      panel.style.display = "block";
    } else {
      panel.style.display = "none";
    }
  });

  // Restore the saved familiar filter, pagination, and add-panel state so
  // the user lands on the same view they left.
  if (restored && typeof restored === "object") {
    if (restored.viewFamiliar === true || restored.viewFamiliar === false) {
      viewFamiliar = restored.viewFamiliar;
      host.querySelectorAll("#familiar-segments .segmented__item").forEach((b) => {
        const on = (b.dataset.familiar === "1") === viewFamiliar;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
    if (Number.isFinite(restored.offset) && restored.offset >= 0) {
      offset = restored.offset;
    }
    if (restored.addOpen && restored.addDraft && typeof restored.addDraft === "object") {
      const panel = document.getElementById("add-panel");
      renderAddForm(panel, lang, fields, fillEndpoint, addBody, endpoint, async () => {
        panel.style.display = "none";
        await load();
      });
      fields.forEach((f) => {
        if (Object.prototype.hasOwnProperty.call(restored.addDraft, f.key)) {
          const ta = panel.querySelector(`#field-${f.key}`);
          if (ta && typeof restored.addDraft[f.key] === "string") ta.value = restored.addDraft[f.key];
        }
      });
      panel.style.display = "block";
    }
  }

  host.querySelector("#familiar-segments").addEventListener("click", (e) => {
    const btn = e.target.closest("button.segmented__item");
    if (!btn) return;
    const next = btn.dataset.familiar === "1";
    if (next === viewFamiliar) return;
    viewFamiliar = next;
    offset = 0;
    host.querySelectorAll("#familiar-segments .segmented__item").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    load();
  });

  const list = host.querySelector("#list");
  list.addEventListener("click", (e) => {
    const prev = e.target.closest("button[data-pager='prev']");
    if (prev && !prev.disabled) { offset = Math.max(0, offset - pageSize); load(); return; }
    const next = e.target.closest("button[data-pager='next']");
    if (next && !next.disabled) { offset += pageSize; load(); return; }
  });

  async function load() {
    const qs = `lang=${encodeURIComponent(lang)}` +
      `&familiar=${viewFamiliar ? "1" : "0"}` +
      `&limit=${pageSize}&offset=${offset}`;
    const res = await api.get(`${endpoint}?${qs}`);
    if (!res.ok) {
      list.innerHTML = `<div class="card" style="border-left: 4px solid var(--danger)">${escapeHtml(res.error)}</div>`;
      return;
    }
    const data = res.data || {};
    const items = data.items || [];
    const total = Number(data.total) || 0;
    if (!items.length) {
      const msg = viewFamiliar
        ? `No ${title.toLowerCase()} you've marked familiar yet.`
        : emptyMsg;
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">${viewFamiliar ? "✅" : "💬"}</div>
          <div class="empty-state__title">${escapeHtml(msg)}</div>
          ${emptyActions && !viewFamiliar ? `
            <div class="row" style="margin-top: var(--sp-3)">
              <button class="btn btn--primary" id="seed-now">✨ Generate starter set</button>
              <button class="btn btn--ghost" id="add-now">+ Add manually</button>
            </div>` : ""}
        </div>`;
      if (emptyActions && !viewFamiliar) {
        list.querySelector("#seed-now")?.addEventListener("click", async () => {
          const r = await api.post(`/api/languages/${lang}/initialize`, { force: false });
          if (!r.ok) {
            toast({ title: "Could not seed", message: r.error, variant: "error" });
            return;
          }
          toast({ title: "Starter set generated", message: `${r.data.structures || 0} structures, ${r.data.phrases || 0} phrases`, variant: "success" });
          await load();
        });
        list.querySelector("#add-now")?.addEventListener("click", () => {
          document.getElementById("add-toggle").click();
        });
      }
      return;
    }
    list.innerHTML = `<div class="list">${items.map((i) => rowRenderer(i)).join("")}</div>` + renderPager(items.length, total);
    list.querySelectorAll("[data-action='edit']").forEach((b) => {
      b.addEventListener("click", () => editRow(items, b, endpoint, load));
    });
    list.querySelectorAll("[data-action='delete']").forEach((b) => {
      b.addEventListener("click", () => deleteRow(b, endpoint, load));
    });
    list.querySelectorAll("[data-action='remember']").forEach((b) => {
      b.addEventListener("click", () => rememberRow(b, endpoint, load));
    });
  }

  function renderPager(itemCount, total) {
    if (!total) return "";
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.floor(offset / pageSize) + 1;
    const atStart = offset === 0;
    const atEnd = offset + itemCount >= total;
    return `
      <div class="row" style="margin-top: var(--sp-3); justify-content: center">
        <button class="btn btn--ghost btn--sm" data-pager="prev" ${atStart ? "disabled" : ""}>← Previous</button>
        <span class="field__hint" style="margin: 0 var(--sp-2)">Page ${currentPage} of ${totalPages} · ${total} total</span>
        <button class="btn btn--ghost btn--sm" data-pager="next" ${atEnd ? "disabled" : ""}>Next →</button>
      </div>
    `;
  }

  load();

  // Expose the live state on the module so saveState() can read it
  // when the user navigates away.
  moduleState.viewFamiliar = () => viewFamiliar;
  moduleState.offset = () => offset;
  moduleState.addOpen = () => {
    const panel = document.getElementById("add-panel");
    return !!(panel && panel.style.display !== "none");
  };
  moduleState.addDraft = () => {
    const panel = document.getElementById("add-panel");
    if (!panel) return null;
    const draft = {};
    fields.forEach((f) => {
      const ta = panel.querySelector(`#field-${f.key}`);
      if (ta && ta.value) draft[f.key] = ta.value;
    });
    return draft;
  };
}

const moduleState = { viewFamiliar: null, offset: null, addOpen: null, addDraft: null };

export function saveState() {
  if (!moduleState.viewFamiliar) return null;
  const viewFamiliar = moduleState.viewFamiliar();
  const offset = moduleState.offset ? moduleState.offset() : 0;
  const addOpen = moduleState.addOpen ? moduleState.addOpen() : false;
  if (!viewFamiliar && !addOpen && !offset) return null;
  return { viewFamiliar, offset, addOpen, addDraft: addOpen ? (moduleState.addDraft() || {}) : null };
}

async function editRow(items, btn, endpoint, load) {
  const id = Number(btn.closest(".list-item").dataset.id || "0");
  const item = items.find((i) => i.id === id);
  if (!item) return;
  const primary = prompt("Phrase", item.phrase || "");
  if (primary == null) return;
  const res = await api.put(`${endpoint}/${id}`, { phrase: primary });
  if (!res.ok) {
    toast({ title: "Couldn't update", message: res.error, variant: "error" });
    return;
  }
  toast({ title: "Saved", variant: "success", ttl: 1500 });
  load();
}

async function deleteRow(btn, endpoint, load) {
  const id = Number(btn.closest(".list-item").dataset.id || "0");
  const res = await api.del(`${endpoint}/${id}`);
  if (!res.ok) {
    toast({ title: "Couldn't delete", message: res.error, variant: "error" });
    return;
  }
  toast({ title: "Deleted", variant: "info", ttl: 1500 });
  load();
}

function renderAddForm(panel, lang, fields, fillEndpoint, addBody, endpoint, onSaved) {
  const inputs = {};
  const formHtml = fields.map((f) => `
    <div class="field">
      <label class="field__label" for="field-${f.key}">${escapeHtml(f.label)}${f.required ? ' <span class="field__required" aria-label="required">*</span>' : ""}</label>
      <textarea id="field-${f.key}" class="input" maxlength="${f.max}" rows="2"${f.required ? ' required aria-required="true"' : ""}></textarea>
    </div>
  `).join("");
  panel.innerHTML = `
    <div class="card">
      <h2 class="card__title">Add new</h2>
      ${formHtml}
      <div class="row" style="margin-top: var(--sp-3)">
        <button id="ai-fill" class="btn btn--ghost">✨ Fill with AI</button>
        <div class="spacer"></div>
        <button id="cancel-add" class="btn btn--ghost">Cancel</button>
        <button id="save-add" class="btn btn--primary">Save</button>
      </div>
    </div>
  `;
  fields.forEach((f) => {
    inputs[f.key] = panel.querySelector(`#field-${f.key}`);
  });
  panel.querySelector("#cancel-add").addEventListener("click", () => { panel.style.display = "none"; });
  panel.querySelector("#ai-fill").addEventListener("click", async () => {
    const partial = {};
    fields.forEach((f) => { partial[f.key] = inputs[f.key].value.trim() || null; });
    if (Object.values(partial).every((v) => v == null)) {
      toast({ title: "Nothing to fill", message: "Add at least one detail before asking the AI to fill the rest.", variant: "error" });
      return;
    }
    const btn = panel.querySelector("#ai-fill");
    btn.disabled = true;
    btn.textContent = "Filling…";
    const res = await api.post(fillEndpoint, { language: lang, ...partial });
    btn.disabled = false;
    btn.textContent = "✨ Fill with AI";
    if (!res.ok) {
      toast({ title: "AI fill failed", message: res.error, variant: "error" });
      return;
    }
    const filled = res.data || {};
    fields.forEach((f) => {
      if (!inputs[f.key].value.trim() && filled[f.key]) {
        inputs[f.key].value = filled[f.key];
        inputs[f.key].style.background = "var(--accent-soft)";
        setTimeout(() => { inputs[f.key].style.background = ""; }, 600);
      }
    });
  });
  panel.querySelector("#save-add").addEventListener("click", async () => {
    const form = {};
    fields.forEach((f) => { form[f.key] = inputs[f.key].value.trim() || null; });
    for (const f of fields) {
      if (f.required && !form[f.key]) {
        toast({ title: `${f.key} is required`, message: `Enter a ${f.key.replace(/_/g, " ")} before saving.`, variant: "error" });
        inputs[f.key].focus();
        return;
      }
    }
    const res = await api.post(endpoint, addBody(lang, form));
    if (!res.ok) {
      toast({ title: "Couldn't save", message: res.error, variant: "error" });
      return;
    }
    toast({ title: "Saved", variant: "success", ttl: 1500 });
    onSaved();
  });
}

async function rememberRow(btn, endpoint, load) {
  const id = Number(btn.dataset.id || btn.closest(".list-item")?.dataset.id || "0");
  if (!id) return;
  const wasFamiliar = btn.getAttribute("aria-pressed") === "true";
  const next = !wasFamiliar;
  btn.disabled = true;
  const res = await api.patch(`${endpoint}/${id}`, { familiar: next });
  btn.disabled = false;
  if (!res.ok) {
    toast({ title: "Couldn't update", message: res.error, variant: "error" });
    return;
  }
  load();
}