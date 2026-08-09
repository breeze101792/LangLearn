// Phrases list page.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";

export function renderPhrases(host) {
  renderListPage({
    host,
    title: "Phrases",
    kind: "phrases",
    endpoint: "/api/phrases",
    fillEndpoint: "/api/phrases/fill",
    emptyMsg: "No phrases for this language yet.",
    emptyActions: true,
    rowRenderer: renderPhraseRow,
    fields: [
      { key: "phrase",               label: "Phrase (in target language)", max: 500 },
      { key: "literal_translation",  label: "Literal translation (optional)", max: 500 },
      { key: "explanation_primary",  label: "Explanation (primary native)", max: 1000 },
      { key: "explanation_secondary", label: "Explanation (secondary native, optional)", max: 1000 },
    ],
    addBody: (lang, form) => ({
      language: lang,
      phrase: form.phrase,
      literal_translation: form.literal_translation,
      explanation_primary: form.explanation_primary,
      explanation_secondary: form.explanation_secondary,
      source: "user",
    }),
  });
}

function renderPhraseRow(item) {
  const isBuiltIn = item.source === "built-in";
  const badge = isBuiltIn
    ? `<span class="badge badge--builtin">Built-in</span><span class="badge badge--muted">Read-only</span>`
    : (item.source === "llm"
        ? `<span class="badge badge--user">You</span><span class="badge badge--ai">AI</span>`
        : `<span class="badge badge--user">You</span>`);
  return `
    <article class="list-item ${isBuiltIn ? "list-item--builtin" : ""}" data-id="${item.id}">
      <div class="list-item__badges">${badge}</div>
      <div class="list-item__main"><strong>"${escapeHtml(item.phrase || "")}"</strong></div>
      ${item.literal_translation ? `<div class="list-item__meta">Literal: ${escapeHtml(item.literal_translation)}</div>` : ""}
      ${item.explanation_primary ? `<div class="list-item__meta">${escapeHtml(item.explanation_primary)}</div>` : ""}
      ${item.explanation_secondary ? `<div class="list-item__meta">${escapeHtml(item.explanation_secondary)}</div>` : ""}
      ${isBuiltIn ? "" : `<div class="list-item__actions"><button class="btn btn--ghost btn--sm" data-action="edit">Edit</button><button class="btn btn--ghost btn--sm" data-action="delete">Delete</button></div>`}
    </article>
  `;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// shared generic list+add page
function renderListPage({ host, title, kind, endpoint, fillEndpoint, emptyMsg, emptyActions, rowRenderer, fields, addBody }) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">${title}</h1>
      <p class="page-head__subtitle">Common ${title.toLowerCase()} for your active language.</p>
    </header>
    <section class="row row--right" style="margin-bottom: var(--sp-3)">
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

  const list = host.querySelector("#list");
  async function load() {
    const res = await api.get(`${endpoint}?lang=${encodeURIComponent(lang)}`);
    if (!res.ok) {
      list.innerHTML = `<div class="card" style="border-left: 4px solid var(--danger)">${escapeHtml(res.error)}</div>`;
      return;
    }
    const items = res.data.items || [];
    if (!items.length) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">💬</div>
          <div class="empty-state__title">${escapeHtml(emptyMsg)}</div>
          ${emptyActions ? `
            <div class="row" style="margin-top: var(--sp-3)">
              <button class="btn btn--primary" id="seed-now">✨ Generate starter set</button>
              <button class="btn btn--ghost" id="add-now">+ Add manually</button>
            </div>` : ""}
        </div>`;
      if (emptyActions) {
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
    list.innerHTML = `<div class="list">${items.map((i) => rowRenderer(i)).join("")}</div>`;
    list.querySelectorAll("[data-action='edit']").forEach((b) => {
      b.addEventListener("click", () => editRow(items, b, endpoint, load));
    });
    list.querySelectorAll("[data-action='delete']").forEach((b) => {
      b.addEventListener("click", () => deleteRow(b, endpoint, load));
    });
  }
  load();
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
      <label class="field__label" for="field-${f.key}">${escapeHtml(f.label)}</label>
      <textarea id="field-${f.key}" class="input" maxlength="${f.max}" rows="2"></textarea>
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
    const res = await api.post(endpoint, addBody(lang, form));
    if (!res.ok) {
      toast({ title: "Couldn't save", message: res.error, variant: "error" });
      return;
    }
    toast({ title: "Saved", variant: "success", ttl: 1500 });
    onSaved();
  });
}