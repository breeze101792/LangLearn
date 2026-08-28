// Unit tests for the browser-side Wiktionary service
// (frontend/static/js/services/wiktionary.js). Network calls are
// stubbed via globalThis.fetch with canned MediaWiki payloads shaped
// like the live editions' responses (captured from es/de/ja/en).
//
// Run with:
//   node --test tests/wiktionary.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { lookup } from "../frontend/static/js/services/wiktionary.js";

function wikiPage(title, extract) {
  return {
    batchcomplete: "",
    query: { pages: { "123": { pageid: 123, ns: 0, title, extract } } },
  };
}

function stubFetch(payload) {
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => payload,
  });
}

// --- canned extracts (trimmed but structurally faithful) -------------

const ES_CASA = wikiPage("casa", `
== Español ==

=== Etimología 1 ===
Del latín casa ('choza'), de origen incierto.

==== Sustantivo femenino ====
1 Vivienda
Edificación destinada a vivienda.
2 Domicilio.
`);

const EN_HOSPITAL = wikiPage("hospital", `
== Translingual ==

=== Noun ===
Hospital (plural Hospitals)

== English ==

=== Etymology ===
From Middle English hospital.

=== Pronunciation ===
(Received Pronunciation) IPA(key): /ˈhɒs.pɪ.tl̩/

=== Noun ===
hospital (countable and uncountable, plural hospitals)
A large medical facility.
A building housing such a facility.

=== Verb ===
To make someone into an invalid.
`);

const DE_HAUS = wikiPage("Haus", `
== Haus ({{Sprache|Deutsch}}) ==
`.replace("{{Sprache|Deutsch}}", "Deutsch") + `

=== Substantiv, n ===
Bedeutungen:
[1] zu einem bestimmten Zweck erbautes Gebäude
[2] zum Wohnen dienendes Gebäude
`);

const JA_MIZU = wikiPage("水", `
== 漢字 ==

異体字 : 氵（部首の変形）, 氺（部首の変形）

=== 名詞 ===
水。みず。
`);

const MULTI_LANG_EN = wikiPage("uno", `
== Spanish ==

=== Numeral ===
One.

== English ==

=== Noun ===
A card with one pip.
`);

// pt.wiktionary nests language sections at heading level 1.
const PT_CASA = wikiPage("casa", `
= Português =
== Substantivo ==
ca.sa, feminino

construção que serve de moradia
domicílio, residência
`);

// es.wiktionary mixes container sections at level 3 with POS at level 4.
const ES_MIXED = wikiPage("casa", `
== Español ==

=== Etimología 1 ===
Del latín casa.

==== Sustantivo femenino ====
casa ¦ plural: casas

1 Vivienda
Edificación destinada a vivienda.

==== Locuciones ====
a casa

=== Forma flexiva ===

==== Forma verbal ====
forma de casar
`);

// --- tests ------------------------------------------------------------

test("es edition: localized 'Español' section parsed, numbering stripped", async () => {
  stubFetch(ES_CASA);
  const res = await lookup("casa", "es");
  assert(res.entry, "expected a hit");
  assert.equal(res.source, "wiktionary");
  assert(res.entry.senses.length > 0, "at least one sense group");
  const nouns = res.entry.senses.find((s) => s.pos.includes("sustantivo"));
  assert(nouns, "POS heading 'Sustantivo femenino' recognized");
  const glosses = nouns.definitions.map((d) => d.glossary);
  assert(glosses.includes("Vivienda"), `got: ${JSON.stringify(glosses)}`);
  assert(glosses.includes("Domicilio."));
});

test("en edition: etymology/pronunciation excluded, noun+verb kept", async () => {
  stubFetch(EN_HOSPITAL);
  const res = await lookup("hospital", "en");
  assert(res.entry, "expected a hit");
  const posList = res.entry.senses.map((s) => s.pos);
  assert(posList.includes("noun"), `got: ${JSON.stringify(posList)}`);
  assert(posList.includes("verb"), `got: ${JSON.stringify(posList)}`);
  assert(!posList.some((p) => p.includes("etymolog")), "etymology skipped");
  assert(!posList.some((p) => p.includes("pronunciation")), "pronunciation skipped");
  // Translingual section must not leak into the English entry.
  assert(!res.entry.senses.some((s) =>
    s.definitions.some((d) => /plural Hospitals/.test(d.glossary))),
  "translingual content excluded");
  const noun = res.entry.senses.find((s) => s.pos === "noun");
  assert(noun.definitions.some((d) => d.glossary.startsWith("A large medical facility")));
});

test("de edition: 'Haus (Deutsch)' heading matched, [1] refs stripped", async () => {
  stubFetch(DE_HAUS);
  const res = await lookup("Haus", "de");
  assert(res.entry, "expected a hit for de edition");
  const glosses = res.entry.senses.flatMap((s) => s.definitions.map((d) => d.glossary));
  assert(glosses.some((g) => g.startsWith("zu einem bestimmten Zweck")),
         `got: ${JSON.stringify(glosses)}`);
  assert(!glosses.some((g) => g.startsWith("[1]")), "bracket ref stripped");
});

test("ja edition: kanji '漢字' section matched", async () => {
  stubFetch(JA_MIZU);
  const res = await lookup("水", "ja");
  assert(res.entry, "expected a hit for ja kanji entry");
  assert(res.entry.senses.length > 0);
});

test("multi-language en entry: only the English slice is parsed", async () => {
  stubFetch(MULTI_LANG_EN);
  const res = await lookup("uno", "en");
  assert(res.entry, "expected a hit");
  const allGlosses = res.entry.senses.flatMap((s) => s.definitions.map((d) => d.glossary));
  assert(allGlosses.includes("A card with one pip."), `got: ${JSON.stringify(allGlosses)}`);
  assert(!allGlosses.includes("One."), "spanish slice excluded");
});

test("pt edition: level-1 language headings parsed", async () => {
  stubFetch(PT_CASA);
  const res = await lookup("casa", "pt");
  assert(res.entry, "expected a hit for pt edition");
  const glosses = res.entry.senses.flatMap((s) => s.definitions.map((d) => d.glossary));
  assert(glosses.includes("construção que serve de moradia"),
         `got: ${JSON.stringify(glosses)}`);
});

test("es edition: level-3 containers don't hide level-4 POS glosses", async () => {
  stubFetch(ES_MIXED);
  const res = await lookup("casa", "es");
  assert(res.entry, "expected a hit");
  const allGlosses = res.entry.senses.flatMap((s) => s.definitions.map((d) => d.glossary));
  assert(allGlosses.includes("Vivienda"), `got: ${JSON.stringify(allGlosses)}`);
  assert(!allGlosses.some((g) => g.includes("casar")), "forma flexiva excluded");
  assert(!allGlosses.includes("casa ¦ plural: casas"), "inflection row dropped");
});

test("missing word: clean miss, never throws (chain can fall through)", async () => {
  stubFetch({
    batchcomplete: "",
    query: { pages: { "-1": { ns: 0, title: "qqzzxx", missing: "" } } },
  });
  const res = await lookup("qqzzxx", "en");
  assert.equal(res.entry, null, "no entry");
  assert.equal(res.error, null, "not flagged as provider error");
});

test("empty extract: clean miss", async () => {
  stubFetch(wikiPage("x", ""));
  const res = await lookup("x", "en");
  assert.equal(res.entry, null);
});

test("language absent from entry: clean miss", async () => {
  // Entry exists but has no German section.
  stubFetch(MULTI_LANG_EN);
  const res = await lookup("uno", "de");
  assert.equal(res.entry, null);
});

test("HTTP failure: clean miss, not a throw", async () => {
  globalThis.fetch = async () => ({ ok: false });
  const res = await lookup("word", "en");
  assert.equal(res.entry, null);
});

test("unsupported language: miss without any network call", async () => {
  let called = false;
  globalThis.fetch = async () => { called = true; return { ok: true }; };
  const res = await lookup("word", "it");
  assert.equal(res.entry, null);
  assert(!called, "fetch must not be called for unsupported langs");
});

// --- CORS / chain fall-through regressions ---------------------------

test("request includes origin=* so Wikimedia sends CORS headers", async () => {
  let seenUrl = "";
  globalThis.fetch = async (url) => {
    seenUrl = String(url);
    return { ok: true, json: async () => wikiPage("word", "== English ==\n\n=== Noun ===\nA word.") };
  };
  await lookup("word", "en");
  assert(seenUrl.includes("origin=%2A") || seenUrl.includes("origin=*"),
         `expected origin=* in request URL, got ${seenUrl}`);
});

test("runChain continues to server steps when client fetch throws", async () => {
  const { runChain } = await import("../frontend/static/js/chain-walker.js");
  // Simulate the browser-level network failure (offline / CORS).
  globalThis.fetch = async () => { throw new TypeError("NetworkError when attempting to fetch resource."); };
  const api = {
    post: async (path, body) => {
      assert.equal(path, "/api/dictionary/lookup");
      // Only the server-side remainder should be sent.
      assert.deepEqual(body.chain.map((s) => s.name), ["llm"]);
      return {
        ok: true,
        data: {
          entry: { word: "casa", language: "es", source: "llm", senses: [] },
          source: "llm",
          provider_errors: [],
          providers_in_chain: 1,
        },
      };
    },
  };
  const res = await runChain({
    word: "casa", lang: "es",
    chain: [{ name: "wiktionary" }, { name: "llm" }],
    api,
    clientSideMap: { wiktionary: true },
  });
  assert.equal(res.source, "llm", "server provider must still answer");
  assert(Array.isArray(res.provider_errors) && res.provider_errors.length === 1,
         "wiktionary network error must ride along");
  assert.equal(res.provider_errors[0].provider, "wiktionary");
});

test("runChain carries earlier client errors onto a later client hit", async () => {
  const { runChain } = await import("../frontend/static/js/chain-walker.js");
  globalThis.fetch = async () => { throw new TypeError("NetworkError"); };
  const res = await runChain({
    word: "hund", lang: "en",
    chain: [{ name: "wiktionary" }],
    api: { post: async () => ({ ok: false }) },
    clientSideMap: { wiktionary: true },
  });
  // No server step configured → terminal miss envelope keeps the error.
  assert.equal(res.entry, null);
  assert(res.provider_errors.length === 1 && res.provider_errors[0].provider === "wiktionary",
         JSON.stringify(res.provider_errors));
});
