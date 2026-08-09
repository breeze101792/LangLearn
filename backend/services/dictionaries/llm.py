"""LLM dictionary provider.

Calls the LLM service to generate a normalized WordEntry.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from .. import llm as llm_svc
from .base import Definition, Sense, WordEntry

log = logging.getLogger(__name__)


def lookup(word: str, lang: str, *,
           explanation_primary: str | None = None,
           explanation_secondary: str | None = None) -> WordEntry:
    try:
        data = llm_svc.lookup_word_via_llm(
            lang=lang,
            word=word,
            explanation_primary=explanation_primary,
            explanation_secondary=explanation_secondary,
        )
    except llm_svc.LLMError as e:
        log.warning("LLM dict lookup failed for %s/%s: %s", lang, word, e)
        return replace(WordEntry.empty(word, lang), source="llm")
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