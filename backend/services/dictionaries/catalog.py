"""Static catalog of available offline dictionaries.

Every entry here describes a dictionary that *could* be installed: provider
name (registry key), human display info, which languages it covers, how it
ships, and roughly how big the data is. The install state lives in the
``installed_dictionaries`` table; this file just declares what's available.

Adding a new offline dictionary is two changes:
  1. Add an entry to ``CATALOG`` below.
  2. Add a provider module under ``backend/services/dictionaries/`` and
     register it in ``registry.bootstrap()`` with the same name. The
     registry will only activate it for languages where the install row
     exists.

For v1 the only catalog entry is WordNet (English). Other languages start
with an empty offline set and rely on the LLM fallback; the user picks
more from the Settings UI later.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogEntry:
    """Metadata for one offline dictionary. Frozen so the catalog can't be
    mutated at runtime (callers copy fields out into plain dicts)."""

    provider: str            # registry key, e.g. "wordnet"
    display_name: str
    description: str
    languages: tuple[str, ...]   # catalog language codes it covers
    auto_install: bool          # install on first app boot
    source: str                  # "bundled" / "download" — informational
    size_hint: str = ""          # human-readable, e.g. "~30 MB"


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        provider="wordnet",
        display_name="WordNet",
        description=(
            "English lexical database. Downloads once on first install; "
            "fully offline afterwards."
        ),
        languages=("en",),
        auto_install=True,
        source="bundled",
        size_hint="~30 MB",
    ),
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
