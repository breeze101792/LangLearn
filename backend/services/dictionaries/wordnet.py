"""WordNet dictionary provider (English only).

Uses NLTK's bundled WordNet corpus. On first call it downloads wordnet if
missing; subsequent calls are offline.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .base import Definition, Sense, WordEntry

log = logging.getLogger(__name__)

WORDNET_POS = {
    "n": "noun",
    "v": "verb",
    "a": "adjective",
    "s": "adjective",   # satellite adjective
    "r": "adverb",
}


@lru_cache(maxsize=1)
def _wn():
    try:
        from nltk.corpus import wordnet as wn
    except ImportError:
        log.error("nltk is not installed; WordNet provider unavailable")
        return None
    try:
        wn.ensure_loaded()
    except LookupError:
        try:
            import nltk
            nltk.download("wordnet", quiet=True)
            nltk.download("omw-1.4", quiet=True)
        except Exception as e:
            log.error("failed to download wordnet: %s", e)
            return None
    return wn


def lookup(word: str, lang: str, **_ignored) -> WordEntry:
    if lang != "en":
        return WordEntry.empty(word, lang)
    wn = _wn()
    if wn is None:
        return WordEntry.empty(word, lang)
    synsets = wn.synsets(word)
    if not synsets:
        return WordEntry.empty(word, lang)
    senses: list[Sense] = []
    for syn in synsets[:5]:
        pos_code = syn.pos() or "n"
        pos_label = WORDNET_POS.get(pos_code, "noun")
        defs = syn.definition()
        examples = syn.examples() or []
        example = examples[0] if examples else None
        defn = Definition(glossary=defs, example=example)
        senses.append(Sense(pos=pos_label, definitions=[defn], source="wordnet"))
    return WordEntry(word=word, language=lang, senses=senses, source="wordnet")


def supports(lang: str) -> bool:
    return lang == "en"