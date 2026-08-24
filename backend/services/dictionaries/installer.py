"""Install / uninstall flow for offline dictionaries.

The install row in ``installed_dictionaries`` is the single source of
truth for "is provider X available for language Y?". The chain executor
asks this module which providers are installed and skips the rest.

WordNet for English is the one auto-install: on first app boot we ensure
the row exists so the lookup code path is identical between fresh
installs and existing users. Other languages start uninstalled and the
user pulls them in through the Settings UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ... import config
from ...db import get_conn
from ...util import is_valid_lang
from . import catalog as catalog_mod

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstallResult:
    provider: str
    language: str
    installed: bool        # True when the install row exists after this call
    already: bool          # True when no change happened (idempotent re-run)
    source: str            # mirrors catalog entry, e.g. "bundled"


def is_installed(provider: str, language: str) -> bool:
    if not is_valid_lang(language):
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM installed_dictionaries WHERE provider=? AND language=?",
            (provider, language),
        ).fetchone()
    return row is not None


def installed_providers(language: str | None = None) -> set[str] | dict[str, set[str]]:
    """Return either ``{provider}`` (when ``language`` is given) or a map
    ``{language: {provider, ...}}`` (when None). The dict form is what the
    chain executor and the catalog endpoint consume."""
    with get_conn() as conn:
        if language is None:
            rows = conn.execute(
                "SELECT provider, language FROM installed_dictionaries"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT provider, language FROM installed_dictionaries WHERE language=?",
                (language,),
            ).fetchall()
    if language is not None:
        return {r["provider"] for r in rows}
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["language"], set()).add(r["provider"])
    return out


def auto_install_defaults() -> list[InstallResult]:
    """Install every catalog entry marked ``auto_install=True`` that the
    user hasn't already installed. Idempotent — safe to call every boot.

    Returns the list of newly installed rows. Used by ``app.create_app``
    before the chain executor registers providers, so a fresh DB still
    has WordNet registered on first request.
    """
    results: list[InstallResult] = []
    for entry in catalog_mod.CATALOG:
        if not entry.auto_install:
            continue
        for lang in entry.languages:
            if not is_valid_lang(lang):
                continue
            if is_installed(entry.provider, lang):
                continue
            results.append(_insert(entry.provider, lang))
            log.info("auto-installed %s for %s", entry.provider, lang)
    return results


def install(provider: str, language: str) -> InstallResult:
    """Mark ``provider`` as installed for ``language``. Idempotent.

    Raises ``ValueError`` for unknown catalog pairs (no silent success).
    The actual data fetch / disk write is delegated to the provider's own
    install hook in the future — for v1 WordNet is the only provider and
    it's already downloaded by NLTK at first lookup, so the install is
    purely the marker row.
    """
    entry = catalog_mod.find(provider, language)
    if entry is None:
        raise ValueError(f"unknown dictionary {provider!r} for language {language!r}")
    if is_installed(provider, language):
        return InstallResult(
            provider=provider, language=language,
            installed=True, already=True, source=entry.source,
        )
    return _insert(provider, language)


def uninstall(provider: str, language: str) -> bool:
    """Drop the install row. Returns True if a row was removed.

    WordNet for English is *not* uninstalled through this path — the
    catalog marks it as a default and the UI hides the Uninstall button.
    Defense in depth lives in the API blueprint.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM installed_dictionaries WHERE provider=? AND language=?",
            (provider, language),
        )
    return cur.rowcount > 0


def _insert(provider: str, language: str) -> InstallResult:
    entry = catalog_mod.find(provider, language)
    source = entry.source if entry is not None else "unknown"
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO installed_dictionaries (provider, language)"
            " VALUES (?, ?)",
            (provider, language),
        )
    return InstallResult(
        provider=provider, language=language,
        installed=True, already=False, source=source,
    )


def catalog_view(language: str | None = None) -> list[dict]:
    """Catalog entries annotated with ``installed`` for the current user.

    When ``language`` is given, each entry exposes ``installed`` for that
    language only. When None, ``installed_languages`` lists which of the
    catalog entry's languages have it installed.
    """
    by_lang: dict[str, set[str]] = installed_providers()  # type: ignore[assignment]
    out: list[dict] = []
    for entry in catalog_mod.CATALOG:
        item = {
            "provider": entry.provider,
            "display_name": entry.display_name,
            "description": entry.description,
            "languages": list(entry.languages),
            "auto_install": entry.auto_install,
            "source": entry.source,
            "size_hint": entry.size_hint,
        }
        if language is not None:
            item["installed"] = entry.provider in by_lang.get(language, set())
            item["supported"] = language in entry.languages
        else:
            item["installed_languages"] = sorted(
                lang for lang in entry.languages
                if entry.provider in by_lang.get(lang, set())
            )
        out.append(item)
    return out


def is_protected(provider: str, language: str) -> bool:
    """True when this provider is a default and shouldn't be uninstallable
    through the UI. WordNet-for-English is the only protected entry today."""
    entry = catalog_mod.find(provider, language)
    return entry is not None and entry.auto_install


__all__ = [
    "InstallResult",
    "auto_install_defaults",
    "catalog_view",
    "install",
    "installed_providers",
    "is_installed",
    "is_protected",
    "uninstall",
]
