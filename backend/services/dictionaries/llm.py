"""LLM dictionary provider.

Calls the LLM service to generate a normalized WordEntry.
"""

from __future__ import annotations

import logging

from .. import llm as llm_svc
from .base import Definition, Sense, WordEntry

log = logging.getLogger(__name__)


def lookup(word: str, lang: str, *,
           explanation_primary: str | None = None,
           explanation_secondary: str | None = None,
           level: str | None = None, **_ignored) -> WordEntry:
    # Re-raise on failure: the chain executor catches our exception and
    # records it in ChainResult.errors so the UI can tell the user "AI is
    # unreachable" instead of silently presenting an empty result.
    data = llm_svc.lookup_word_via_llm(
        lang=lang,
        word=word,
        explanation_primary=explanation_primary,
        explanation_secondary=explanation_secondary,
        level=level,
    )
    senses: list[Sense] = []
    for raw in data.get("senses", []):
        pos = (raw.get("pos") or "").strip()[:32]
        defs: list[Definition] = []
        for d in raw.get("definitions", []):
            g = (d.get("glossary") or "").strip()
            if not g:
                continue
            ex = d.get("example")
            defs.append(Definition(glossary=g[:1000], example=(ex[:1000] if ex else None)))
        if not defs:
            continue
        explanations = raw.get("explanations") or {}
        senses.append(Sense(
            pos=pos or "—",
            definitions=defs,
            explanations={
                "primary": (explanations.get("primary") or "")[:1000] or None,
                "secondary": (explanations.get("secondary") or "")[:1000] or None,
            },
            source="llm",
        ))
    return WordEntry(word=word, language=lang, senses=senses, source="llm")


def supports(lang: str) -> bool:
    return True