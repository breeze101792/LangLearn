"""Dictionary data shapes.

A WordEntry is the normalized result returned by every provider. The chain
executor returns the first non-empty WordEntry; an empty WordEntry signals
"no result" without raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Definition:
    glossary: str
    example: str | None = None


@dataclass
class Sense:
    pos: str
    definitions: list[Definition]
    explanations: dict = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "pos": self.pos,
            "definitions": [asdict(d) for d in self.definitions],
            "explanations": self.explanations,
            "source": self.source,
        }


@dataclass
class WordEntry:
    word: str
    language: str
    senses: list[Sense] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "language": self.language,
            "source": self.source,
            "senses": [s.to_dict() for s in self.senses],
        }

    @property
    def is_empty(self) -> bool:
        return not self.senses

    @classmethod
    def empty(cls, word: str, language: str) -> "WordEntry":
        return cls(word=word, language=language, senses=[], source="")