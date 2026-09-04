"""No secret reaches the browser bundle (spec §40, docs/testing/test-plan.md).

Next.js inlines any `NEXT_PUBLIC_*` variable into the client bundle at build
time. That makes the leak a *build* artefact rather than a code one — reading
the source proves nothing, because the value is substituted after the source is
written. So this scans what is actually shipped.

Skipped when `frontend/.next` is absent: an empty scan passing would be worse
than an honest skip.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_ROOT = BACKEND_ROOT.parent / "frontend"
BUILD_DIR = FRONTEND_ROOT / ".next"

# What ships to a browser. Server chunks legitimately hold server-side config,
# so scanning them would produce a false positive on BACKEND_URL.
CLIENT_DIRS = ("static", "server/app")
CLIENT_SUFFIXES = (".js", ".mjs", ".css", ".html", ".json", ".txt")

# Directories that are build bookkeeping, not shipped code.
SKIP_PARTS = {"cache", "types"}

SECRET_SHAPES: list[tuple[re.Pattern[str], str]] = [
    # Google API keys: "AIza" then 35 more characters.
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google/Gemini API key"),
    # A JWT signing secret would appear as a long random assignment.
    (re.compile(r"JWT_SECRET\s*[:=]\s*[\"'][^\"']{16,}"), "JWT secret"),
    # postgres://user:password@host
    (re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']*:[^\s\"'@]+@"), "database URL with password"),
    (re.compile(r"redis://[^\s\"']*:[^\s\"'@]+@"), "Redis URL with password"),
]

FORBIDDEN_NAMES = ("GEMINI_API_KEY", "JWT_SECRET", "DATABASE_URL", "QDRANT_API_KEY")


def client_files() -> list[Path]:
    files: list[Path] = []
    for relative in CLIENT_DIRS:
        root = BUILD_DIR / relative
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in CLIENT_SUFFIXES:
                continue
            if SKIP_PARTS & set(path.parts):
                continue
            files.append(path)
    return files


pytestmark = pytest.mark.skipif(
    not BUILD_DIR.exists(),
    reason="frontend/.next is absent — run `npm run build` first",
)


def test_the_scan_actually_covers_something():
    """Guards the guard: a scan over zero files proves nothing."""
    files = client_files()
    assert len(files) > 5, f"only found {len(files)} client files under {BUILD_DIR}"


def test_no_secret_shaped_string_is_in_the_client_bundle():
    offenders: list[str] = []

    for path in client_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in SECRET_SHAPES:
            if pattern.search(text):
                offenders.append(f"{label} in {path.relative_to(BUILD_DIR)}")

    assert offenders == [], offenders


def test_no_secret_variable_name_is_inlined():
    """A `NEXT_PUBLIC_GEMINI_API_KEY` would appear here by name."""
    offenders: list[str] = []

    for path in client_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in FORBIDDEN_NAMES:
            if f"NEXT_PUBLIC_{name}" in text:
                offenders.append(f"NEXT_PUBLIC_{name} in {path.relative_to(BUILD_DIR)}")

    assert offenders == [], offenders


def test_the_env_files_declare_nothing_public():
    """The cheap check that stops the expensive one ever firing."""
    for name in (".env.example", ".env.local"):
        path = FRONTEND_ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            assert not stripped.startswith("NEXT_PUBLIC_"), (
                f"{name} declares {stripped.split('=')[0]}; anything NEXT_PUBLIC_ is "
                f"inlined into the browser bundle"
            )


def test_no_backend_bearer_token_is_stored_client_side():
    """Tokens live in httpOnly cookies; no client module may read them."""
    source = FRONTEND_ROOT / "src"
    offenders: list[str] = []

    for path in source.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not text.lstrip().startswith('"use client"'):
            continue
        for marker in ("localStorage", "sessionStorage", "document.cookie"):
            if marker in text:
                offenders.append(f"{marker} in {path.relative_to(FRONTEND_ROOT)}")

    assert offenders == [], offenders
