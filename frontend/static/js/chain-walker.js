// Client-side chain walker for the dictionary page.
//
// The chain stored in settings mixes server-side providers (WordNet,
// LLM) with client-side providers (Wiktionary). The server-side chain
// executor runs on the server, so the client must dispatch the
// client-side steps itself before falling through to the server.
//
// This module owns the policy:
//   - Walk the chain in order.
//   - For each step, look up whether it's client-side (the catalog
//     returns a ``client_side: bool`` per provider). If so, run the
//     matching JS service; on hit, render and stop.
//   - On miss (or for a server-side step), send the rest of the chain
//     to the server, which runs its chain executor over the
//     server-side portion. Stale client-side entries in the body are
//     ignored because the server's registry has no callable for them.
//
// The walker is intentionally provider-agnostic: the only built-in
// client-side service today is Wiktionary, but the dispatch table is
// open to any future browser-side dictionary.

import * as wiktionaryService from "./services/wiktionary.js";

// Map of client-side provider name → { lookup(word, lang) → {entry, ...} }.
// New client-side providers register here.
const CLIENT_SIDE_SERVICES = {
  wiktionary: wiktionaryService,
};

/**
 * @returns `true` if the catalog entry for the given provider is
 * marked client-side, false otherwise. Reads from a previously-loaded
 * catalog (the dictionary page populates this map at load time).
 */
export function isClientSideProvider(providerName, catalogClientSideMap) {
  if (!providerName || !catalogClientSideMap) return false;
  return Boolean(catalogClientSideMap[providerName]);
}

/**
 * Build a lookup-result envelope in the same shape the dictionary
 * page expects from the server. Lets the page treat client-side hits
 * and server-side hits uniformly.
 */
function envelopeFromClientHit(provider, lookupResult) {
  return {
    entry: lookupResult.entry,
    source: lookupResult.source || provider,
    auto_added: false,
    in_vocab: false,
    leitner_box: null,
    vocab_id: null,
    provider_errors: [],
    providers_in_chain: 0,
  };
}

function envelopeFromClientError(provider, word, lang, error) {
  return {
    entry: null,
    source: "",
    auto_added: false,
    in_vocab: false,
    leitner_box: null,
    vocab_id: null,
    provider_errors: [{ provider, error: String(error) }],
    providers_in_chain: 0,
  };
}

/**
 * Run the chain. For client-side steps, calls the JS service
 * directly. For server-side steps, sends the remaining chain to
 * `/api/dictionary/lookup` and returns its payload.
 *
 * An empty chain still results in a server call so the page sees the
 * same "no providers configured" payload as before the client-side
 * dispatch was added — keeps tests and the page's "no result"
 * branch behaving identically.
 *
 * @param {object}   args
 * @param {string}   args.word
 * @param {string}   args.lang
 * @param {Array}    args.chain             - User chain (full order)
 * @param {object}   args.api               - The shared `api` helper
 * @param {object}   args.clientSideMap     - { providerName: true }
 * @returns {Promise<{entry, source, ...}>}
 */
export async function runChain({ word, lang, chain, api, clientSideMap }) {
  if (!Array.isArray(chain) || chain.length === 0) {
    const res = await api.post("/api/dictionary/lookup", { lang, word, chain: [] });
    return res.data || { entry: null, source: "", provider_errors: [], providers_in_chain: 0 };
  }

  for (let i = 0; i < chain.length; i++) {
    const step = chain[i];
    if (!step || !step.name) continue;
    if (step.enabled === false) continue;

    if (isClientSideProvider(step.name, clientSideMap)) {
      const service = CLIENT_SIDE_SERVICES[step.name];
      if (!service) {
        // Provider is marked client-side but no JS service is
        // registered for it. Skip rather than crash.
        continue;
      }
      try {
        const result = await service.lookup(word, lang);
        if (result && result.entry) {
          return envelopeFromClientHit(step.name, result);
        }
        // Miss — try the next step in the chain.
      } catch (e) {
        return envelopeFromClientError(step.name, word, lang, e);
      }
      continue;
    }

    // Server-side step: send the rest of the chain. The client has
    // already attempted every client-side step before it, so the
    // server only needs the remaining server-side portion.
    const rest = chain
      .slice(i)
      .filter((s) => s && s.name && !isClientSideProvider(s.name, clientSideMap))
      .map((s) => ({ name: s.name, enabled: s.enabled !== false }));
    if (rest.length === 0) {
      return { entry: null, source: "", provider_errors: [], providers_in_chain: 0 };
    }
    const res = await api.post("/api/dictionary/lookup", { lang, word, chain: rest });
    if (!res.ok) {
      return envelopeFromClientError(rest[0].name, word, lang, res.error || "lookup failed");
    }
    return res.data || { entry: null, source: "" };
  }

  return { entry: null, source: "", provider_errors: [], providers_in_chain: 0 };
}

/**
 * Force a specific provider. Used by the per-source switcher in the
 * result card. Client-side providers run in the browser; server-side
 * providers call /api/dictionary/lookup with the override.
 */
export async function runProvider({ word, lang, providerName, api, clientSideMap }) {
  if (isClientSideProvider(providerName, clientSideMap)) {
    const service = CLIENT_SIDE_SERVICES[providerName];
    if (!service) {
      return { entry: null, source: "", provider_errors: [{ provider: providerName, error: "not implemented" }] };
    }
    try {
      const result = await service.lookup(word, lang);
      return result.entry
        ? envelopeFromClientHit(providerName, result)
        : { entry: null, source: "", provider_errors: [], providers_in_chain: 0 };
    } catch (e) {
      return envelopeFromClientError(providerName, word, lang, e);
    }
  }
  const res = await api.post("/api/dictionary/lookup", {
    lang, word, provider: providerName,
  });
  return res.data || { entry: null, source: "" };
}
