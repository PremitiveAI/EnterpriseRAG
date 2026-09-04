# ADR-007 — Native Windows Services, No Docker

**Status:** Accepted · **Date:** 2026-08-21 · **Decides:** spec §11, deployment

## Decision

Every service runs natively on Windows, started from its own terminal. **Docker is not used.**

Verified during Phase 0 on the target host (Windows 10 Pro, Python 3.11.0, Node 20.9.0).

| Service | How | Port | Terminal |
| ------- | --- | ---- | -------- |
| PostgreSQL | Already running as a Windows service ✅ | 5432 | — |
| Redis (**Memurai**) | Windows service | 6379 | — |
| **Qdrant** | `qdrant.exe` from the official release zip | 6333 / 6334 | 1 |
| FastAPI | `uvicorn app.main:app --reload --port 8000` | 8000 | 2 |
| Celery worker | `celery -A app.workers.celery_app worker -l info --pool=solo` | — | 3 |
| Flower | `celery -A app.workers.celery_app flower --port=5555` | 5555 | 4 |
| Next.js | `npm run dev` | 3000 | 5 |

## Verified findings

**Qdrant ships an official Windows binary.** Release v1.19.0 includes
`qdrant-x86_64-pc-windows-msvc.zip` — a single `qdrant.exe`. Server mode with the dashboard, no
Docker and no WSL. This matters: the Python client's *embedded* mode takes an exclusive lock on
its storage directory, which would break the moment FastAPI and the Celery worker both used it.
Server mode removes that problem entirely and satisfies §27's expectation of a dashboard.

**Redis on Windows means Memurai.** Memurai is Redis's official Windows partner and installs as
a native service. The free **Developer Edition** carries real restrictions:

> Usage in a production environment is prohibited · restart required every 10 days · capped at
> 50% of system memory · maximum 10 unique client IPs · community support only.

Redis API 7.4.7 on the stable build — sufficient as a Celery broker and for §36 chat context.

**🔴 This is a development-only licence.** Production requires Linux, WSL2, or a Memurai
Enterprise licence. Recorded now so it does not surface at deployment time.

**Celery is not officially supported on Windows** since v4. `--pool=solo` is correct but
processes one task at a time. `--pool=threads -c 4` gives concurrency and suits this pipeline,
which is I/O- and subprocess-bound (Tesseract, file reads, HTTP to Qdrant) rather than
CPU-bound in Python. Start with `solo`; move to `threads` when throughput matters.

## Consequences

- Five terminals plus two services. `scripts/` will carry `.bat` launchers.
- No single-command bring-up. A `docker-compose.yml` may be added later for production without
  changing application code — nothing depends on the runtime being native.
- Startup order matters: PostgreSQL and Memurai (services) → Qdrant → FastAPI → worker → Flower.
- Tesseract must also be installed and on `PATH` ([ADR-004](ADR-004-tesseract-ocr.md)).

## Sources

- [Qdrant releases](https://github.com/qdrant/qdrant/releases) · [Qdrant installation](https://qdrant.tech/documentation/installation/)
- [Memurai](https://www.memurai.com/) · [Download / editions](https://www.memurai.com/get-memurai) · [Redis on Memurai](https://redis.io/blog/use-redis-natively-on-windows-with-memurai/)
