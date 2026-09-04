"""Seed document_categories from config/taxonomy.py (spec §24).

Idempotent: existing slugs are updated, never duplicated.

    py scripts\seed_taxonomy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.documents.models import DocumentCategory  # noqa: E402
from config.taxonomy import DEFAULT_TAXONOMY  # noqa: E402


def main() -> int:
    db = SessionLocal()
    created = updated = 0
    try:
        for entry in DEFAULT_TAXONOMY:
            slug = str(entry["slug"])
            existing = db.execute(
                select(DocumentCategory).where(DocumentCategory.slug == slug)
            ).scalar_one_or_none()

            if existing is None:
                db.add(DocumentCategory(**entry))  # type: ignore[arg-type]
                created += 1
            else:
                existing.name = str(entry["name"])
                existing.description = entry.get("description")  # type: ignore[assignment]
                existing.sort_order = int(entry["sort_order"])  # type: ignore[arg-type]
                updated += 1

        db.commit()
        print(f"Taxonomy seeded: {created} created, {updated} updated.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
