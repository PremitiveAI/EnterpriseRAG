"""AI enrichment: classification, metadata and tags (spec §22 stages 8-10).

Every stage here DEGRADES rather than fails. A document that cannot be
classified is still chunked, embedded and indexed — losing a category label is
a metadata gap; losing the vectors would make the document invisible
(docs/features/document-processing.md §9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.modules.ai import llm_client
from config.taxonomy import UNCATEGORISED_SLUG

logger = get_logger(__name__)

# The window shown to the model. Enough to classify without sending the corpus.
CONTEXT_CHARS = 4000
MAX_TAGS = 10
MIN_TAGS = 3


@dataclass
class Enrichment:
    category_slug: str | None = None
    document_type: str | None = None
    title: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    degraded: bool = False


def _window(text: str) -> str:
    return text[:CONTEXT_CHARS]


def _clean_tag(raw: str) -> str | None:
    tag = re.sub(r"[^\w\s&-]", "", str(raw)).strip().lower()[:64]
    return tag or None


def classify(text: str, taxonomy: list[dict[str, str]]) -> tuple[str | None, str | None]:
    """Return ``(category_slug, document_type)``.

    The taxonomy comes from the database, never a hard-coded list (§24), and the
    model must return one of the supplied slugs — anything else is discarded.
    """
    if not taxonomy or not text.strip():
        return None, None

    options = "\n".join(f"- {t['slug']}: {t['name']} — {t.get('description') or ''}" for t in taxonomy)
    prompt = f"""Classify this document into exactly one category.

Categories:
{options}

Respond with JSON only:
{{"category_slug": "<one slug from the list above>", "document_type": "<short snake_case type, e.g. leave_policy, pan_card, invoice>"}}

Document:
\"\"\"
{_window(text)}
\"\"\""""

    result = llm_client.generate_json(prompt)
    if not isinstance(result, dict):
        return None, None

    valid = {t["slug"] for t in taxonomy}
    slug = result.get("category_slug")
    slug = slug if slug in valid else UNCATEGORISED_SLUG

    doc_type = result.get("document_type")
    if isinstance(doc_type, str):
        doc_type = re.sub(r"[^\w]", "_", doc_type.strip().lower())[:64] or None
    else:
        doc_type = None

    return slug, doc_type


def extract_metadata(text: str, fallback_title: str) -> tuple[str, str | None] | None:
    """Return ``(title, description)``, or None if the model could not answer.

    None and ``(fallback_title, None)`` used to be the same value, and that cost
    a real document its description with nothing to show for it: the model was
    rate-limited, this returned the filename as the title, and the caller could
    not tell that apart from a document the model had genuinely read and found
    nothing to say about. The caller applies the fallback now, so it knows.
    """
    if not text.strip():
        return fallback_title, None

    prompt = f"""Summarise this document.

Respond with JSON only:
{{"title": "<a concise descriptive title, max 12 words>", "description": "<one or two sentences>"}}

Document:
\"\"\"
{_window(text)}
\"\"\""""

    result = llm_client.generate_json(prompt)
    if not isinstance(result, dict):
        return None

    title = result.get("title")
    title = title.strip()[:512] if isinstance(title, str) and title.strip() else fallback_title

    description = result.get("description")
    description = description.strip() if isinstance(description, str) and description.strip() else None

    return title, description


def generate_tags(text: str) -> list[str] | None:
    """Tags, or None if the model could not answer.

    Same distinction as ``extract_metadata``: an empty list means "the model
    read it and produced nothing usable", None means "the model never spoke".
    """
    if not text.strip():
        return []

    prompt = f"""Generate {MIN_TAGS}-{MAX_TAGS} short topical tags for this document.

Respond with JSON only:
{{"tags": ["tag one", "tag two"]}}

Rules: lowercase, one to three words each, no punctuation, no duplicates.

Document:
\"\"\"
{_window(text)}
\"\"\""""

    result = llm_client.generate_json(prompt)
    if not isinstance(result, dict):
        return None

    raw = result.get("tags")
    if not isinstance(raw, list):
        return []

    seen: list[str] = []
    for item in raw:
        tag = _clean_tag(item)
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:MAX_TAGS]


def enrich(text: str, *, taxonomy: list[dict[str, str]], fallback_title: str) -> Enrichment:
    """Run all three AI stages, degrading independently."""
    if not llm_client.is_configured():
        logger.info("No LLM provider active — enrichment skipped, document still indexed")
        return Enrichment(title=fallback_title, degraded=True)

    enrichment = Enrichment(title=fallback_title)
    degraded = False

    try:
        enrichment.category_slug, enrichment.document_type = classify(text, taxonomy)
        degraded = degraded or enrichment.category_slug is None
    except Exception:
        logger.exception("Classification failed; continuing")
        degraded = True

    # The three stages fail INDEPENDENTLY, so each one is asked separately
    # whether it actually ran. They used to be judged by their output, which
    # made a rate-limited call look identical to a successful one that had
    # nothing to add - and on a real upload, classification succeeded while
    # metadata and tags were both refused with 429, so the document was recorded
    # as fully enriched while missing its description and every tag.
    try:
        metadata = extract_metadata(text, fallback_title)
        if metadata is None:
            degraded = True
        else:
            enrichment.title, enrichment.description = metadata
    except Exception:
        logger.exception("Metadata extraction failed; continuing")
        degraded = True

    try:
        tags = generate_tags(text)
        if tags is None:
            degraded = True
        else:
            enrichment.tags = tags
    except Exception:
        logger.exception("Tag generation failed; continuing")
        degraded = True

    if degraded:
        # At WARNING, not INFO: this is the line someone greps when a document
        # comes out with a filename for a title and no tags.
        logger.warning(
            "Enrichment degraded - the document is indexed and searchable, but "
            "some metadata is missing. Reprocess it once the model is available.",
        )

    enrichment.degraded = degraded
    return enrichment
