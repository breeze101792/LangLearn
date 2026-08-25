"""Static catalog of available dictionaries.

Every entry here describes a dictionary that *could* be installed: provider
name (registry key), human display info, which languages it covers, how it
ships, and roughly how big the data is. The install state lives in the
``installed_dictionaries`` table; this file just declares what's available.

Providers come in two flavors, expressed by the ``client_side`` flag:

  - ``client_side=False`` (default): the provider is registered in
    ``registry.bootstrap()`` on the server, and the chain executor on
    the server runs it. The data lives on the server. Example: WordNet
    (English), LLM.

  - ``client_side=True``: the provider runs **in the user's browser**.
    The server still has a catalog entry so the user can install/enable
    it from Settings, but the server's chain executor does not call
    it. The client-side JS layer is responsible for running the
    provider's lookup and merging the result into the chain. Example:
    Wiktionary (per-language, browser-side).

Adding a new server-side dictionary is two changes:
  1. Add an entry to ``CATALOG`` below (``client_side=False``).
  2. Add a provider module under ``backend/services/dictionaries/``
     and register it in ``registry.bootstrap()`` with the same name.
     The registry will only activate it for languages where the
     install row exists.

Adding a new client-side dictionary is one change here (an entry with
``client_side=True``) plus a client-side service under
``frontend/static/js/services/`` that exports a ``lookup(lang, word)``
function. The chain executor in the dictionary page consults
``catalog_view(lang)`` to learn which providers are client-side and
dispatches accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    """Metadata for one dictionary. Frozen so the catalog can't be
    mutated at runtime (callers copy fields out into plain dicts).

    ``source`` is informational (``"bundled"`` or ``"online"``) and
    drives the "Online" / "Offline" badge in the Settings UI.
    ``client_side`` drives which side actually runs the provider's
    lookup and is consumed by both the dictionary page (to dispatch
    client-side steps) and the server (to skip them in
    ``lookup_via_chain``)."""

    provider: str            # registry key, e.g. "wordnet"
    display_name: str
    description: str
    languages: tuple[str, ...]   # catalog language codes it covers
    auto_install: bool          # install on first app boot
    source: str                  # "bundled" or "online" — for the UI badge
    size_hint: str = ""          # human-readable, e.g. "~30 MB"
    client_side: bool = False    # True for browser-side providers


# Languages the Wiktionary client-side service covers. Every code
# below corresponds to a live `xx.wiktionary.org` edition.
_WIKTIONARY_LANGS: tuple[str, ...] = (
    "en", "es", "fr", "de", "ja", "pt", "zh",
)


def _wiktionary_entries() -> tuple[CatalogEntry, ...]:
    out: list[CatalogEntry] = []
    for lang in _WIKTIONARY_LANGS:
        out.append(CatalogEntry(
            provider="wiktionary",
            display_name=f"Wiktionary ({lang})",
            description=(
                "Online (browser-side). The browser fetches definitions from "
                "the corresponding Wiktionary edition; results are cached "
                "locally for repeat lookups. The server never makes the "
                "network call."
            ),
            languages=(lang,),
            auto_install=False,
            source="online",
            client_side=True,
        ))
    return tuple(out)


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        provider="wordnet",
        display_name="WordNet",
        description=(
            "Offline. English lexical database bundled with the app via NLTK. "
            "After the one-time NLTK download, every lookup runs on the "
            "server with no network."
        ),
        languages=("en",),
        auto_install=True,
        source="bundled",
        size_hint="~30 MB",
    ),
    *_wiktionary_entries(),
)


def find(provider: str, language: str) -> CatalogEntry | None:
    """Return the catalog entry that covers ``provider`` for ``language``,
    or None if that pair isn't in the catalog."""
    for entry in CATALOG:
        if entry.provider != provider:
            continue
        if language in entry.languages:
            return entry
    return None


def providers_for_language(language: str) -> list[CatalogEntry]:
    """All catalog entries that cover ``language``."""
    return [e for e in CATALOG if language in e.languages]


__all__ = ["CATALOG", "CatalogEntry", "find", "providers_for_language"]
