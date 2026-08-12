// Transfer page: export user content as JSON/CSV and import with a
// merge preview so the user can decide what to add / overwrite / skip.
//
// UI flow:
//
//   Export tab:
//     - Pick scope (vocab / structures / phrases / full backup).
//     - Pick format (JSON / CSV). CSV is restricted to single-table scope.
//     - Optional language filter for vocab exports.
//     - Download button calls /api/transfer/export; JSON comes back as
//       {ok,data}, CSV as a file download (Content-Disposition).
//
//   Import tab (three steps):
//     1) Pick table, choose a file (or paste text), pick format.
//        CSV files may have a header row (auto-mapped) or no header
//        (manual column mapping UI shows up next).
//     2) Confirm column mapping. Auto-guess fills the inputs; user can
//        edit. Click "Preview" → POST /api/transfer/import/preview.
//     3) Merge preview: each parsed row tagged new/existing/invalid.
//        Bulk + per-row actions (Add / Overwrite / Skip). Click Apply
//        → POST /api/transfer/import/apply.

import { api } from "../api.js";
import { store } from "../state.js";
import { toast } from "../components/toast.js";

const SCOPES = [
  { id: "vocab",      label: "Vocabulary words" },
  { id: "structures", label: "Structures" },
  { id: "phrases",    label: "Phrases" },
  { id: "all",        label: "Full backup (JSON only)" },
];

const TABLES = [
  { id: "vocab",      label: "Vocabulary words" },
  { id: "structures", label: "Structures" },
  { id: "phrases",    label: "Phrases" },
];

// Canonical field set per table — matches backend.services.transfer.
// Editing these lists in two places is intentional friction: the
// backend is the source of truth, but we mirror them here so the
// mapping UI does not need a round-trip before the user picks columns.
const FIELDS_BY_TABLE = {
  vocab: [
    "language", "word", "source", "pos", "glossary", "example",
    "explanation_primary", "explanation_secondary",
    "leitner_box", "next_due", "added_at",
  ],
  structures: [
    "language", "pattern", "example_sentence", "explanation",
    "explanation_primary", "explanation_secondary",
    "source", "familiar", "added_at",
  ],
  phrases: [
    "language", "phrase", "example_sentence", "explanation",
    "explanation_primary", "explanation_secondary",
    "source", "familiar", "added_at",
  ],
};

const REQUIRED_BY_TABLE = {
  vocab: ["language", "word", "glossary"],
  structures: ["language", "pattern", "example_sentence", "explanation"],
  phrases: ["language", "phrase", "example_sentence", "explanation"],
};

const ACTION_OPTIONS = [
  { id: "add",       label: "Add" },
  { id: "overwrite", label: "Overwrite" },
  { id: "skip",      label: "Skip" },
];

let activeTab = "export";

// `host` is the container; this render expects to live inside the
// Settings page (which provides the title and main nav). When called
// from a top-level route the caller wraps it with page chrome.
export function renderTransfer(host) {
  const state = store.get();
  const languages = state.languages || [];
  const langOptions = languages.map((l) =>
    `<option value="${escapeAttr(l.code)}">${escapeHtml(l.display_name || l.code)}</option>`
  ).join("");

  host.innerHTML = `
    <div class="settings__section">
      <h2 class="card__title">Backup &amp; restore</h2>
      <p class="field__hint" style="margin-bottom: var(--sp-3)">Export what you have, or import rows from another source — including other apps.</p>
      <nav class="transfer-tabs" role="tablist" aria-label="Backup sections">
        <button class="transfer-tabs__item is-active" data-tab="export" role="tab" aria-selected="true">Export</button>
        <button class="transfer-tabs__item" data-tab="import" role="tab" aria-selected="false">Import</button>
      </nav>
      <div id="transfer-main"></div>
    </div>
  `;

  const tabs = host.querySelector(".transfer-tabs");
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button.transfer-tabs__item");
    if (!btn) return;
    activeTab = btn.dataset.tab;
    tabs.querySelectorAll("button.transfer-tabs__item").forEach((b) => {
      const on = (b === btn);
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    renderMain();
  });

  const main = host.querySelector("#transfer-main");
  function renderMain() {
    if (activeTab === "export") renderExport(main, langOptions);
    else renderImport(main, langOptions);
  }
  renderMain();
}

// ---------- export tab ----------

function renderExport(host, langOptions) {
  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Export</h2>
      <div class="field">
        <label class="field__label" for="export-scope">What to include</label>
        <select id="export-scope" class="select">
          ${SCOPES.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label class="field__label" for="export-format">Format</label>
        <select id="export-format" class="select">
          <option value="json">JSON (lossless, includes everything)</option>
          <option value="csv">CSV (single table, flat)</option>
        </select>
        <span class="field__hint" id="export-format-hint">CSV requires a single-table scope.</span>
      </div>
      <div class="field">
        <label class="field__label" for="export-lang">Language (optional)</label>
        <select id="export-lang" class="select">
          <option value="">All languages</option>
          ${langOptions}
        </select>
        <span class="field__hint">Restrict the export to one language.</span>
      </div>
      <div class="row" style="margin-top: var(--sp-3); gap: var(--sp-3)">
        <button class="btn btn--primary" id="export-download">Download</button>
        <span class="field__hint" id="export-status"></span>
      </div>
    </div>
  `;
  const scope = host.querySelector("#export-scope");
  const format = host.querySelector("#export-format");
  const hint = host.querySelector("#export-format-hint");
  function syncHint() {
    if (format.value === "csv" && scope.value === "all") {
      format.value = "json";
    }
    const csvDisabled = scope.value === "all";
    Array.from(format.options).forEach((o) => {
      if (o.value === "csv") o.disabled = csvDisabled;
    });
    hint.textContent = csvDisabled
      ? "CSV is restricted to a single-table scope; switch to JSON for full backups."
      : "JSON keeps every field; CSV is one row per item, easier to open in spreadsheets.";
  }
  scope.addEventListener("change", syncHint);
  format.addEventListener("change", syncHint);
  syncHint();

  host.querySelector("#export-download").addEventListener("click", async () => {
    const params = new URLSearchParams({
      scope: scope.value,
      format: format.value,
    });
    const lang = host.querySelector("#export-lang").value;
    if (lang) params.set("lang", lang);
    const status = host.querySelector("#export-status");
    status.textContent = "Preparing…";
    try {
      if (format.value === "csv") {
        await downloadBlob(`/api/transfer/export?${params.toString()}`,
          filenameFor(scope.value, "csv"));
        status.textContent = "Downloaded.";
      } else {
        const res = await api.get(`/api/transfer/export?${params.toString()}`);
        if (!res.ok) {
          status.textContent = res.error || "Failed.";
          toast({ title: "Couldn't export", message: res.error, variant: "error" });
          return;
        }
        const blob = new Blob([JSON.stringify(res.data, null, 2)],
          { type: "application/json" });
        await downloadBlobUrl(blob, filenameFor(scope.value, "json"));
        status.textContent = "Downloaded.";
      }
      toast({ title: "Export ready", variant: "success", ttl: 2000 });
    } catch (e) {
      status.textContent = "Failed.";
      toast({ title: "Couldn't export", message: String(e), variant: "error" });
    }
  });
}

function filenameFor(scope, ext) {
  const stamp = new Date().toISOString().slice(0, 10);
  return `langlearn-${scope}-${stamp}.${ext}`;
}

async function downloadBlob(url, filename) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const blob = await r.blob();
  await downloadBlobUrl(blob, filename);
}

function downloadBlobUrl(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------- import tab ----------

function renderImport(host, langOptions) {
  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Import</h2>
      <div class="field">
        <label class="field__label" for="import-table">What you're importing</label>
        <select id="import-table" class="select">
          ${TABLES.map((t) => `<option value="${t.id}">${escapeHtml(t.label)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label class="field__label" for="import-format">Format</label>
        <select id="import-format" class="select">
          <option value="json">JSON</option>
          <option value="csv">CSV</option>
        </select>
      </div>
      <div class="field" id="import-lang-field">
        <label class="field__label" for="import-default-lang">Default language</label>
        <select id="import-default-lang" class="select">
          ${langOptions}
        </select>
        <span class="field__hint">Used when a row has no language column. (CSV files without a language column always use this.)</span>
      </div>
      <div class="field" id="import-header-field">
        <label class="field__label" for="import-has-header">
          <input type="checkbox" id="import-has-header" checked />
          CSV has a header row (auto-map column names)
        </label>
        <span class="field__hint">Uncheck this if your CSV is pure data with no titles — you'll map columns by position.</span>
      </div>
      <div class="field">
        <label class="field__label" for="import-file">File</label>
        <input type="file" id="import-file" class="input" accept=".csv,.json,text/csv,application/json" />
      </div>
      <div class="field">
        <label class="field__label" for="import-text">…or paste contents</label>
        <textarea id="import-text" class="input" rows="6" placeholder="Paste JSON or CSV here"></textarea>
      </div>
      <div class="row" style="margin-top: var(--sp-3); gap: var(--sp-3)">
        <button class="btn btn--primary" id="import-preview">Preview merge</button>
        <span class="field__hint" id="import-status"></span>
      </div>
    </div>

    <section id="import-mapping" hidden></section>
    <section id="import-merge" hidden></section>
  `;

  const formatSel = host.querySelector("#import-format");
  const headerField = host.querySelector("#import-header-field");
  const defaultLangSel = host.querySelector("#import-default-lang");
  formatSel.addEventListener("change", () => {
    headerField.style.display = formatSel.value === "csv" ? "" : "none";
  });

  host.querySelector("#import-preview").addEventListener("click", async () => {
    const status = host.querySelector("#import-status");
    status.textContent = "Parsing…";
    const table = host.querySelector("#import-table").value;
    const fmt = formatSel.value;
    const fileInput = host.querySelector("#import-file");
    const textInput = host.querySelector("#import-text").value;
    const hasHeader = host.querySelector("#import-has-header").checked;
    const defaultLang = defaultLangSel.value;
    let blob = null;
    let rawText = "";
    if (fileInput.files && fileInput.files[0]) {
      blob = fileInput.files[0];
      rawText = await blob.text();
    } else if (textInput.trim()) {
      rawText = textInput;
    } else {
      status.textContent = "Pick a file or paste contents.";
      toast({ title: "Nothing to import", message: "Choose a file or paste data.", variant: "info" });
      return;
    }
    if (!rawText.trim()) {
      status.textContent = "Empty input.";
      return;
    }
    await runPreview(host, {
      text: rawText, format: fmt, table, hasHeader, defaultLang, file: blob,
    });
    status.textContent = "Done.";
  });
}

async function runPreview(host, args) {
  const mappingSection = host.querySelector("#import-mapping");
  const mergeSection = host.querySelector("#import-merge");
  const form = new FormData();
  form.append("table", args.table);
  form.append("format", args.format);
  form.append("default_lang", args.defaultLang || "");
  form.append("has_header", args.hasHeader ? "1" : "0");
  if (args.file) {
    form.append("file", args.file, args.file.name || `import.${args.format}`);
  }
  // We deliberately send a raw multipart so the server can read either
  // a 'file' part or the raw body. Content-Disposition header is set
  // automatically by the browser based on the File object.
  const r = await fetch("/api/transfer/import/preview?" +
    new URLSearchParams({ table: args.table, format: args.format,
                          default_lang: args.defaultLang || "",
                          has_header: args.hasHeader ? "1" : "0" }),
    { method: "POST", body: form, credentials: "same-origin" });
  let payload;
  try {
    payload = await r.json();
  } catch (e) {
    toast({ title: "Preview failed", message: `HTTP ${r.status}`, variant: "error" });
    return;
  }
  if (!payload.ok) {
    toast({ title: "Preview failed", message: payload.error || "Unknown error", variant: "error" });
    return;
  }
  const data = payload.data;
  // If CSV without header, show mapping editor first.
  if (args.format === "csv" && !args.hasHeader) {
    renderMappingEditor(mappingSection, mergeSection, args, data);
    mergeSection.hidden = true;
    mappingSection.hidden = false;
    mappingSection.scrollIntoView({ block: "start", behavior: "smooth" });
    return;
  }
  // Otherwise, skip mapping editor (auto-guessed) and go straight to
  // merge preview. Mapping is recorded but not edited.
  renderMergePreview(mergeSection, args, data, /* mapping */ []);
  mappingSection.hidden = true;
  mergeSection.hidden = false;
  mergeSection.scrollIntoView({ block: "start", behavior: "smooth" });
}

function renderMappingEditor(host, mergeSection, args, data) {
  const fields = FIELDS_BY_TABLE[args.table];
  const required = new Set(REQUIRED_BY_TABLE[args.table]);
  const rows = data.rows || [];
  // The preview we just got is already a best-effort parse under the
  // headerless mapping (which is empty here). Show column inputs so the
  // user can correct column positions; we won't POST again until they
  // hit "Re-parse".
  const inputRows = fields.map((f) => {
    return `<tr>
      <td><code>${escapeHtml(f)}</code>${required.has(f) ? ' <span class="field__required" title="required">*</span>' : ""}</td>
      <td><input type="number" min="0" class="input mapping-col" data-field="${escapeAttr(f)}" placeholder="column index (0-based)" /></td>
    </tr>`;
  }).join("");

  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Column mapping</h2>
      <p class="field__hint" style="margin-bottom: var(--sp-3)">Tell the importer which CSV column maps to which field. Columns are 0-based — the first column is 0.</p>
      <table class="transfer-mapping">
        <thead>
          <tr><th>Target field</th><th>Column index</th></tr>
        </thead>
        <tbody>${inputRows}</tbody>
      </table>
      <div class="row" style="margin-top: var(--sp-3); gap: var(--sp-3)">
        <button class="btn btn--primary" id="reparse-btn">Re-parse with mapping</button>
        <span class="field__hint" id="reparse-status">${rows.length} rows currently parsed.</span>
      </div>
    </div>
  `;
  const status = host.querySelector("#reparse-status");
  host.querySelector("#reparse-btn").addEventListener("click", async () => {
    const mapping = [];
    host.querySelectorAll(".mapping-col").forEach((el) => {
      const idx = el.value.trim();
      if (idx !== "") {
        mapping.push({ field: el.dataset.field, index: Number(idx) });
      }
    });
    status.textContent = "Parsing…";
    const params = new URLSearchParams({
      table: args.table, format: "csv", default_lang: args.defaultLang || "",
      has_header: "0",
    });
    if (mapping.length) params.set("mapping", JSON.stringify(mapping));
    const form = new FormData();
    if (args.file) form.append("file", args.file, args.file.name);
    else form.append("file", new Blob([args.text], { type: "text/csv" }), "pasted.csv");
    const r = await fetch("/api/transfer/import/preview?" + params.toString(),
      { method: "POST", body: form, credentials: "same-origin" });
    let payload;
    try { payload = await r.json(); }
    catch (e) {
      status.textContent = "Failed.";
      toast({ title: "Parse failed", message: `HTTP ${r.status}`, variant: "error" });
      return;
    }
    if (!payload.ok) {
      status.textContent = "Failed.";
      toast({ title: "Parse failed", message: payload.error || "Unknown error", variant: "error" });
      return;
    }
    status.textContent = `${(payload.data.rows || []).length} rows parsed.`;
    renderMergePreview(mergeSection, args, payload.data, mapping);
    mergeSection.hidden = false;
    mergeSection.scrollIntoView({ block: "start", behavior: "smooth" });
  });
}

function renderMergePreview(host, args, data, mapping) {
  const rows = data.rows || [];
  const stats = data.stats || { new: 0, existing: 0, invalid: 0 };
  const decisions = rows.map((r) => {
    if (r.status === "invalid") return { index: r.index, action: "skip" };
    if (r.status === "existing") {
      // Default: overwrite if user-owned/llm, skip if built-in.
      const src = r.existing_source;
      return { index: r.index, action: src === "built-in" ? "skip" : "overwrite" };
    }
    return { index: r.index, action: "add" };
  });
  const tableHtml = rows.length === 0
    ? `<div class="empty-state"><div class="empty-state__title">Nothing parsed.</div></div>`
    : renderMergeTable(rows, decisions);

  host.innerHTML = `
    <div class="card">
      <h2 class="card__title">Merge preview</h2>
      <div class="transfer-stats" aria-label="Preview counts">
        <span class="badge badge--ok">New: ${stats.new}</span>
        <span class="badge badge--warn">Existing: ${stats.existing}</span>
        <span class="badge badge--muted">Invalid: ${stats.invalid}</span>
      </div>
      ${tableHtml}
      <div class="row" style="margin-top: var(--sp-4); gap: var(--sp-3)">
        <button class="btn btn--ghost btn--sm" id="bulk-add">Add all new</button>
        <button class="btn btn--ghost btn--sm" id="bulk-skip">Skip all existing</button>
        <div class="spacer"></div>
        <button class="btn btn--primary" id="apply-merge">Apply</button>
      </div>
    </div>
  `;

  function refreshActions() {
    host.querySelectorAll("select.row-action").forEach((sel, i) => {
      decisions[i].action = sel.value;
    });
  }

  host.querySelector("#bulk-add").addEventListener("click", () => {
    rows.forEach((r, i) => {
      if (r.status === "new") {
        decisions[i].action = "add";
        const sel = host.querySelector(`select.row-action[data-index="${i}"]`);
        if (sel) sel.value = "add";
      }
    });
  });
  host.querySelector("#bulk-skip").addEventListener("click", () => {
    rows.forEach((r, i) => {
      if (r.status === "existing") {
        decisions[i].action = "skip";
        const sel = host.querySelector(`select.row-action[data-index="${i}"]`);
        if (sel) sel.value = "skip";
      }
    });
  });
  host.querySelectorAll("select.row-action").forEach((sel) => {
    sel.addEventListener("change", refreshActions);
  });

  host.querySelector("#apply-merge").addEventListener("click", async () => {
    refreshActions();
    const applyBtn = host.querySelector("#apply-merge");
    applyBtn.disabled = true;
    const params = new URLSearchParams({ table: args.table, format: args.format });
    if (mapping && mapping.length) {
      params.set("mapping", JSON.stringify(mapping));
    }
    const res = await api.post(
      `/api/transfer/import/apply?${params.toString()}`,
      {
        table: args.table,
        rows: rows.map((r) => r.fields),
        decisions,
      },
    );
    applyBtn.disabled = false;
    if (!res.ok) {
      toast({ title: "Apply failed", message: res.error || "Unknown error", variant: "error" });
      return;
    }
    const { added, overwritten, skipped, builtin_protected, errors } = res.data;
    const parts = [];
    if (added) parts.push(`added ${added}`);
    if (overwritten) parts.push(`overwrote ${overwritten}`);
    if (skipped) parts.push(`skipped ${skipped}`);
    if (builtin_protected) parts.push(`built-in kept ${builtin_protected}`);
    if (errors) parts.push(`${errors} errors`);
    toast({
      title: "Import complete",
      message: parts.length ? parts.join(", ") : "Nothing applied.",
      variant: errors ? "warn" : "success",
      ttl: 4500,
    });
    if ((res.data.error_messages || []).length) {
      console.warn("import errors:", res.data.error_messages);
    }
    host.querySelector("#apply-merge").setAttribute("aria-disabled", "true");
  });
}

function renderMergeTable(rows, decisions) {
  const head = `<tr>
    <th>#</th><th>Status</th><th>Word / content</th><th>Language</th><th>Details</th><th>Action</th>
  </tr>`;
  const body = rows.map((r) => {
    const f = r.fields || {};
    const word = f.word || f.pattern || f.phrase || "(?)";
    const lang = f.language || "—";
    const statusBadge = r.status === "new"
      ? `<span class="badge badge--ok">New</span>`
      : r.status === "existing"
        ? `<span class="badge badge--warn">Exists</span>`
        : `<span class="badge badge--muted" title="${escapeAttr(r.reason || "")}">Invalid</span>`;
    const details = r.status === "invalid"
      ? `<span class="field__hint">${escapeHtml(r.reason || "invalid")}</span>`
      : `<span class="field__hint">${escapeHtml(f.glossary || f.explanation || "")}</span>`;
    const action = r.status === "invalid"
      ? `<span class="field__hint">—</span>`
      : `<select class="select row-action" data-index="${r.index}">
           ${ACTION_OPTIONS.map((o) =>
             `<option value="${o.id}"${o.id === decisions[r.index].action ? " selected" : ""}>${escapeHtml(o.label)}</option>`
           ).join("")}
         </select>`;
    return `<tr>
      <td>${r.index + 1}</td>
      <td>${statusBadge}</td>
      <td><strong>${escapeHtml(word)}</strong></td>
      <td>${escapeHtml(lang)}</td>
      <td>${details}</td>
      <td>${action}</td>
    </tr>`;
  }).join("");
  return `<div class="table-wrap"><table class="transfer-merge">${head}${body}</table></div>`;
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

function escapeAttr(s) {
  return escapeHtml(s);
}