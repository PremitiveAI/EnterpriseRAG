"""Chunking (spec §26, docs/features/document-processing.md stage 11).

Every chunk traces back to its document, page and index — a chunk that cannot
be traced cannot be cited.

The binding constraint is the encoder, not readability: all-mpnet-base-v2
truncates beyond 384 word-pieces **silently**, producing a vector for the first
part of the text with no error. A 4,000-character chunk would embed roughly its
first quarter and quietly lose the rest. 1,000 characters keeps every chunk
inside the window with margin (ADR-003).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.logging import get_logger
from app.modules.documents.services.extraction_service import Page
from config.settings import settings

logger = get_logger(__name__)

# Split preference: paragraph, then sentence, then word. Never mid-word.
_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    index: int
    text: str
    page_number: int | None
    section: str | None

    @property
    def char_count(self) -> int:
        return len(self.text)


def _split_long(block: str, size: int) -> list[str]:
    """Break an over-long block on sentence, then word boundaries."""
    if len(block) <= size:
        return [block]

    out: list[str] = []
    current = ""

    for sentence in _SENTENCE.split(block):
        if len(sentence) > size:
            # A single sentence longer than the window: fall back to words.
            if current:
                out.append(current.strip())
                current = ""
            words: list[str] = []
            length = 0
            for word in sentence.split():
                if length + len(word) + 1 > size and words:
                    out.append(" ".join(words))
                    words, length = [], 0
                words.append(word)
                length += len(word) + 1
            if words:
                current = " ".join(words)
            continue

        if len(current) + len(sentence) + 1 > size and current:
            out.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current.strip():
        out.append(current.strip())
    return out


def chunk_pages(
    pages: list[Page],
    *,
    size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    size = size or settings.CHUNK_SIZE_CHARS
    overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP_CHARS

    chunks: list[Chunk] = []
    index = 0

    # Chunks never span pages. Slightly smaller chunks at page boundaries are a
    # fair price for citations that point at the right page.
    for page in pages:
        text = (page.text or "").strip()
        if not text:
            continue

        blocks: list[str] = []
        for paragraph in _PARAGRAPH.split(text):
            paragraph = paragraph.strip()
            if paragraph:
                blocks.extend(_split_long(paragraph, size))

        current = ""
        for block in blocks:
            if len(current) + len(block) + 1 > size and current:
                chunks.append(Chunk(index, current.strip(), page.number, page.section))
                index += 1
                # Carry the tail forward so a sentence spanning a boundary stays
                # retrievable from either side.
                tail = current[-overlap:] if overlap else ""
                current = f"{tail} {block}".strip() if tail else block
            else:
                current = f"{current}\n{block}".strip() if current else block

        if current.strip():
            chunks.append(Chunk(index, current.strip(), page.number, page.section))
            index += 1

    oversized = [c.index for c in chunks if c.char_count > size * 1.2]
    if oversized:
        # Worth knowing about: these are the chunks at risk of silent truncation.
        logger.warning("Chunks exceed the target size", extra={"chunk_indexes": oversized[:10]})

    logger.info(
        "Chunked",
        extra={
            "chunks": len(chunks),
            "avg_chars": sum(c.char_count for c in chunks) // len(chunks) if chunks else 0,
        },
    )
    return chunks
