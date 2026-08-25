// Unit tests for the word-card renderer (frontend/static/js/components/word-card.js).
//
// Pins the visual structure so future refactors don't regress the
// designed layout (POS groupings, per-POS numbering, example
// blockquotes, language chip).
//
// Run with:
//   node tests/word_card.test.mjs
// Exits 0 on pass, 1 on first failure.

import { renderWordCard } from "../frontend/static/js/components/word-card.js";

let failures = 0;
let passed = 0;

function test(name, fn) {
  try {
    fn();
    console.log("ok  -", name);
    passed++;
  } catch (e) {
    console.log("FAIL -", name);
    console.log("       ", e && e.message ? e.message : String(e));
    failures++;
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

function countMatches(str, regex) {
  return (str.match(regex) || []).length;
}

// ---------- helpers ---------------------------------------------------------

function wiktionaryEntry() {
  return {
    word: "perro",
    language: "es",
    source: "wiktionary",
    senses: [
      {
        pos: "noun",
        source: "wiktionary",
        definitions: [
          {
            glossary: "(Canis lupus familiaris) Variedad doméstica del lobo.",
            example: "El perro ladra por la noche.",
          },
          {
            glossary: "Sándwich de salchicha de Viena en un pan largo.",
            example: null,
          },
        ],
      },
      {
        pos: "adjective",
        source: "wiktionary",
        definitions: [
          { glossary: "Desdichado, indigno, muy malo.", example: null },
        ],
      },
    ],
  };
}

function wordnetEntry() {
  return {
    word: "test",
    language: "en",
    source: "wordnet",
    senses: [
      {
        pos: "noun",
        source: "wordnet",
        definitions: [
          { glossary: "A challenge, trial.", example: null },
        ],
      },
    ],
  };
}

// ---------- tests -----------------------------------------------------------

test("head includes the word, speak button, and meta row", () => {
  const html = renderWordCard(wiktionaryEntry());
  assert(/<h2 class="word-card__headword">perro<\/h2>/.test(html), "headword missing");
  assert(/<button[^>]+data-action="speak"[^>]+data-word="perro"[^>]+data-lang="es"/.test(html),
    "speak button missing or wrong attrs");
  assert(/class="word-card__meta"/.test(html), "meta row missing");
  // No standalone POS chip in the head — POS belongs to senses.
  assert(!/<span class="word-card__pos">es<\/span>/.test(html),
    "language code should not appear as a POS chip in the head");
});

test("meta row shows the source label and the language code", () => {
  const html = renderWordCard(wiktionaryEntry());
  assert(/class="word-card__source-badge word-card__source-badge--wiktionary"[^>]*>Wiktionary</.test(html),
    "wiktionary source badge missing");
  assert(/class="word-card__lang"[^>]*>es</.test(html), "language chip missing");
});

test("senses are grouped by POS", () => {
  const html = renderWordCard(wiktionaryEntry());
  const groupMatches = html.match(/<section class="word-card__pos-group"/g) || [];
  assert(groupMatches.length === 2, `expected 2 POS groups, got ${groupMatches.length}`);
  // The two POSes in the entry: noun, adjective.
  assert(/data-pos="noun"/.test(html), "noun group missing");
  assert(/data-pos="adjective"/.test(html), "adjective group missing");
});

test("numbering restarts at 1 for each POS", () => {
  const html = renderWordCard(wiktionaryEntry());
  // The two noun senses should be "1." and "2."; the adjective sense
  // should also be "1." (not "3.").
  const nums = [...html.matchAll(/<span class="word-card__def__num"[^>]*>(\d+)\.<\/span>/g)]
    .map((m) => Number(m[1]));
  assert(nums.join(",") === "1,2,1",
    `expected per-POS numbering 1,2,1; got ${nums.join(",")}`);
});

test("each definition renders as its own numbered row", () => {
  const html = renderWordCard(wiktionaryEntry());
  const defMatches = html.match(/<li class="word-card__def">/g) || [];
  assert(defMatches.length === 3, `expected 3 rows, got ${defMatches.length}`);
});

test("examples render inside a blockquote with italic styling hook", () => {
  const html = renderWordCard(wiktionaryEntry());
  assert(/<blockquote class="word-card__example">"El perro ladra por la noche\."<\/blockquote>/.test(html),
    "example blockquote missing or wrong content");
});

test("POS heading carries an uppercase tag chip", () => {
  const html = renderWordCard(wiktionaryEntry());
  assert(/<span class="word-card__pos-tag">noun<\/span>/.test(html), "noun POS tag missing");
  assert(/<span class="word-card__pos-tag">adjective<\/span>/.test(html), "adjective POS tag missing");
});

test("definitions without examples render without an empty blockquote", () => {
  const html = renderWordCard(wiktionaryEntry());
  // Only one blockquote should be present (one example in the entry).
  const exCount = countMatches(html, /<blockquote class="word-card__example">/g);
  assert(exCount === 1, `expected 1 example, got ${exCount}`);
});

test("compact mode hides the head and meta row", () => {
  const html = renderWordCard(wiktionaryEntry(), { compact: true });
  assert(!/class="word-card__head"/.test(html), "head should be hidden in compact mode");
  assert(!/class="word-card__meta"/.test(html), "meta row should be hidden in compact mode");
  // But the senses should still render.
  assert(/<section class="word-card__pos-group"/.test(html), "senses missing in compact mode");
});

test("source badge style varies per provider", () => {
  const wn = renderWordCard(wordnetEntry());
  assert(/word-card__source-badge--wordnet/.test(wn), "wordnet badge modifier missing");
  const wk = renderWordCard(wiktionaryEntry());
  assert(/word-card__source-badge--wiktionary/.test(wk), "wiktionary badge modifier missing");
});

test("empty entry renders an empty-state hint", () => {
  const html = renderWordCard({ word: "x", language: "en", source: "wiktionary", senses: [] });
  assert(/No sense data available/.test(html), "empty-state hint missing");
});

test("single-definition entry renders exactly one row", () => {
  const html = renderWordCard(wordnetEntry());
  const defMatches = html.match(/<li class="word-card__def">/g) || [];
  assert(defMatches.length === 1, `expected 1 row, got ${defMatches.length}`);
});

test("flat (vocab-row) entries normalize to a single row", () => {
  // A flat row from the vocab table — no senses, just glossary/pos/example.
  const flat = {
    word: "banana",
    language: "en",
    source: "user",
    pos: "noun",
    glossary: "An elongated curved fruit.",
    example: "She ate a banana.",
  };
  const html = renderWordCard(flat);
  const defMatches = html.match(/<li class="word-card__def">/g) || [];
  assert(defMatches.length === 1, `expected 1 row from flat entry, got ${defMatches.length}`);
  assert(/An elongated curved fruit\./.test(html), "glossary missing");
});

test("source badge class names are safe across providers", () => {
  const html = renderWordCard({
    word: "x",
    language: "en",
    source: "weird-source-name",
    senses: [{ pos: "noun", source: "weird-source-name", definitions: [{ glossary: "abc", example: null }] }],
  });
  // An unknown source gets the "default" badge style and shows the raw name.
  assert(/word-card__source-badge--default/.test(html), "default badge modifier missing");
  assert(/weird-source-name/.test(html), "raw source name should still appear");
});

test("definitions array length drives row count, not senses array length", () => {
  const html = renderWordCard({
    word: "x",
    language: "en",
    source: "wiktionary",
    senses: [
      {
        pos: "noun",
        source: "wiktionary",
        definitions: [
          { glossary: "a", example: null },
          { glossary: "b", example: null },
          { glossary: "c", example: null },
        ],
      },
    ],
  });
  const rows = html.match(/<li class="word-card__def">/g) || [];
  assert(rows.length === 3, `expected 3 rows from 3 definitions, got ${rows.length}`);
});

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures === 0 ? 0 : 1);