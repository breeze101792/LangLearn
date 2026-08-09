// Vocabulary page: browse and self-rate items in your active language.
//
// Reuses the existing Leitner box (1-5) as a "memory level". The segmented
// control filters by box; each row also lets you move an item to a
// different box in place. Delete is undo-able for 5 seconds. List is
// paginated using the same page_size setting as Review (settings.page_size,
// default 20).

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";

const BOX_LABELS = {
  1: "Box 1 (new)",
  2: "Box 2",
  3: "Box 3",
  4: "Box 4",
  5: "Box 5 (mastered)",
};

export function renderVocabulary(host) {
  const state = store.get();
  const lang = (state.settings && state.settings.active_language) || "en";
  const pageSize = (state.settings && state.settings.page_size) || 20;

  host.innerHTML = `
    <header class="page-head">
      <h1 class="page-head__title">Vocabulary</h1>
      <p class="page-head__subtitle">Words you've looked up, grouped by how well you remember them.</p>
    </header>
    <section class="row" style="margin-bottom: var(--sp-3)">
      <div class="segmented" id="box-segments" role="tablist" aria-label="Memory level filter">
        <button class="segmented__item is-active" data-box="" role="tab" aria-selected="true">All</button>
        ${[1, 2, 3, 4, 5].map((b) =>
          `<button class="segmented__item" data-box="${b}" role="tab" aria-selected="false">${escapeHtml(BOX_LABELS[b])} <span class="badge badge--muted" data-count="${b}">0</span></button>`
        ).join("")}
      </div>
    </section>
    <section id="vocab-list"></section>
  `;

  const list = host.querySelector("#vocab-list");
  const segments = host.querySelector("#box-segments");

  let activeBox = "";
  let offset = 0;

  segments.addEventListener("click", (e) => {
    const btn = e.target.closest("button.segmented__item");
    if (!btn) return;
    activeBox = btn.dataset.box || "";
    offset = 0;
    segments.querySelectorAll("button.segmented__item").forEach((b) => {
      const on = (b === btn);
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    load();
  });

  list.addEventListener("click", (e) => {
    const prev = e.target.closest("button[data-pager='prev']");
    if (prev && !prev.disabled) { offset = Math.max(0, offset - pageSize); load(); return; }
    const next = e.target.closest("button[data-pager='next']");
    if (next && !next.disabled) { offset += pageSize; load(); return; }

    const boxBtn = e.target.closest("button[data-set-box]");
    if (boxBtn) {
      const li = boxBtn.closest(".list-item");
      const id = Number(li?.dataset.id || "0");
      const box = Number(boxBtn.dataset.setBox);
      if (id && box) moveToBox(id, box, li);
      return;
    }
    const delBtn = e.target.closest("button[data-action='delete']");
    if (delBtn) {
      const li = delBtn.closest(".list-item");
      const id = Number(li?.dataset.id || "0");
      if (id) deleteRow(id, li);
    }
  });

  async function load() {
    const qs = `lang=${encodeURIComponent(lang)}` +
      (activeBox ? `&box=${encodeURIComponent(activeBox)}` : "") +
      `&limit=${pageSize}&offset=${offset}`;
    const res = await api.get(`/api/vocab?${qs}`);
    if (!res.ok) {
      list.innerHTML = `<div class="card" style="border-left: 4px solid var(--danger)">${escapeHtml(res.error || "load failed")}</div>`;
      return;
    }
    const { items, total, by_box } = res.data;
    updateCounts(by_box);
    renderList(items || [], Number(total) || 0);
  }

  function updateCounts(by_box) {
    by_box = by_box || {};
    const total = Object.values(by_box).reduce((s, n) => s + Number(n || 0), 0);
    const totalBadge = segments.querySelector("button[data-box=''] .badge");
    if (totalBadge) totalBadge.textContent = String(total);
    for (let b = 1; b <= 5; b++) {
      const el = segments.querySelector(`button[data-box='${b}'] [data-count='${b}']`);
      if (el) el.textContent = String(by_box[b] || 0);
    }
  }

  function renderList(items, total) {
    if (!items.length) {
      const msg = activeBox
        ? `No vocab items in ${escapeHtml(BOX_LABELS[Number(activeBox)])} yet.`
        : "No vocab items for this language yet. Look up a word in Dictionary to add one.";
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-state__icon">📚</div>
          <div class="empty-state__title">${msg}</div>
        </div>`;
      return;
    }
    list.innerHTML = `<div class="list">${items.map(renderRow).join("")}</div>` + renderPager(items.length, total);
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

  function renderRow(item) {
    const box = clampBox(item.leitner_box);
    const sourceBadge = item.source === "llm"
      ? `<span class="badge badge--ai">AI</span>`
      : item.source === "user"
        ? `<span class="badge badge--user">You</span>`
        : `<span class="badge badge--builtin">WordNet</span>`;
    const pos = item.pos ? `<span class="badge badge--muted">${escapeHtml(item.pos)}</span>` : "";
    const boxBtns = [1, 2, 3, 4, 5].map((b) =>
      `<button class="btn btn--sm ${b === box ? "btn--primary" : "btn--ghost"}"
               data-set-box="${b}" aria-label="Move to box ${b}"
               title="${escapeHtml(BOX_LABELS[b])}">${b}</button>`
    ).join("");
    const wordDisplay = item.word.replace(/_/g, " ");
    return `
      <article class="list-item" data-id="${item.id}">
        <div class="list-item__badges">${sourceBadge}${pos}<span class="badge badge--muted">${escapeHtml(BOX_LABELS[box])}</span></div>
        <div class="list-item__main"><strong>${escapeHtml(wordDisplay)}</strong></div>
        ${item.glossary ? `<div class="list-item__meta">${escapeHtml(item.glossary)}</div>` : ""}
        ${item.example ? `<div class="list-item__meta" style="color: var(--text-muted)"><em>${escapeHtml(item.example)}</em></div>` : ""}
        <div class="list-item__actions">
          <div class="row" style="gap: var(--sp-1)">${boxBtns}</div>
          <div class="spacer"></div>
          <button class="btn btn--ghost btn--sm" data-action="delete">Delete</button>
        </div>
      </article>
    `;
  }

  async function moveToBox(id, box, li) {
    if (!li) return;
    const res = await api.patch(`/api/vocab/${id}`, { leitner_box: box });
    if (!res.ok) {
      toast({ title: "Couldn't update level", message: res.error || "unknown error", variant: "error" });
      return;
    }
    // Reflect the new level in the row's badge and button states locally,
    // then re-fetch so the segment counts and pagination stay in sync.
    const newBox = clampBox(res.data.leitner_box);
    const badge = li.querySelector(".list-item__badges .badge--muted");
    if (badge) badge.textContent = BOX_LABELS[newBox];
    li.querySelectorAll("button[data-set-box]").forEach((b) => {
      const isActive = Number(b.dataset.setBox) === newBox;
      b.classList.toggle("btn--primary", isActive);
      b.classList.toggle("btn--ghost", !isActive);
    });
    load();
  }

  async function deleteRow(id, li) {
    const res = await api.del(`/api/vocab/${id}`);
    if (!res.ok) {
      toast({ title: "Couldn't delete", message: res.error || "unknown error", variant: "error" });
      return;
    }
    const undoToken = res.data && res.data.undo_token;
    const deletedId = (res.data && res.data.deleted_id) || id;
    if (li) li.remove();
    toast({
      title: "Removed from vocabulary",
      message: "You can undo for 5 seconds.",
      variant: "info",
      ttl: 5500,
      actions: undoToken ? [{
        label: "Undo",
        onClick: async () => {
          const r = await api.post(`/api/vocab/${deletedId}/restore`, { undo_token: undoToken });
          if (!r.ok) {
            toast({ title: "Couldn't restore", message: r.error || "undo expired", variant: "error" });
            return;
          }
          toast({ title: "Restored", variant: "success", ttl: 1500 });
          load();
        },
      }] : [],
    });
    load();
  }

  load();
}

function clampBox(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 1) return 1;
  if (n > 5) return 5;
  return Math.round(n);
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
