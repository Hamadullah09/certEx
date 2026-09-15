# CertExtract

Batch extraction of structured data from certificate documents.

Upload a folder of birth, marriage and death certificates — PDFs, Word files, or
image-only scans — and the pipeline splits multi-certificate files into individual
certificates, reads each one with a native text layer or OCR, extracts typed
fields with a confidence score and a provenance tag, validates them, routes the
uncertain ones to a human, and exports one row per certificate as CSV, XLSX or
JSON.

---

## Build status

This repository is being built in the phase order below. **A phase is only marked
done when its tests pass.**

| # | Phase | Status |
|---|-------|--------|
| 1 | Scaffold — monorepo, Compose, schema + migrations, auth, health | ✅ Done |
| 2 | Upload & storage — MinIO, content sniffing, dedupe, archives, upload UI | ✅ Done — 265 backend + 5 frontend tests passing |
| 3 | Text extraction — native PDF text, DOCX, page-quality scoring | ⬜ Not started |
| 4 | OCR — rasterisation, OpenCV preprocessing, Tesseract, PaddleOCR | ⬜ Not started |
| 5 | Splitting & classification — boundary detection, type classifier | ⬜ Not started |
| 6 | Rules extraction — synonyms, spatial rules, template engine | ⬜ Not started |
| 7 | LLM fallback — client interface, strict schemas, verbatim check | ⬜ Not started |
| 8 | Validation & confidence — normalisers, cross-field checks, routing | ⬜ Not started |
| 9 | Results UI — virtualised grid, filters, inline edit, source pane | ⬜ Not started |
| 10 | Export — streaming CSV, XLSX, JSON | ⬜ Not started |
| 11 | Template learning — save corrections as reusable templates | ⬜ Not started |
| 12 | Hardening — retries, DLQ, audit, retention, rate limits, load test | ⬜ Not started |

---

## Quick start

Requirements: Docker with Compose v2, and roughly 8 GB of free disk for the
images (Tesseract, LibreOffice and PaddleOCR are all installed, not stubbed).

```bash
cp .env.example .env
docker compose up
```

That brings up Postgres, Redis, MinIO, the API, two classes of Celery worker, the
scheduler and the frontend; applies migrations; verifies the schema matches the
models; provisions the storage bucket; proves server-side encryption actually
works; and seeds a demo workspace.

| Service | URL | Credentials |
|---------|-----|-------------|
| Frontend | http://localhost:3000 | `admin@example.com` / `admin12345` |
| API docs | http://localhost:8000/docs | — |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |

Three seeded accounts exercise the role model: `admin@example.com`,
`operator@example.com`, `viewer@example.com` (passwords in `.env.example`).
Seeding refuses to run when `APP_ENV=production`.

### Images

Five services share two built images rather than building the same 1.6 GB image
five times:

| Image | Services |
|-------|----------|
| `certex-api` | `api`, `migrate` |
| `certex-worker` | `worker`, `worker-ocr`, `beat` |
| `certex-web` | `web` |

The worker image is the larger of the two because it adds PaddleOCR and
pre-downloads its models at build time, so the first scanned page does not block
on a model fetch and the container works with no outbound network.

### Ports already in use?

Every published port is configurable. Create a `.env` with, for example:

```bash
POSTGRES_PORT=55432
REDIS_PORT=56379
MINIO_PORT=59000
MINIO_CONSOLE_PORT=59001
API_PORT=58000
WEB_PORT=53000
```

`.env` is gitignored, so this stays local to your machine. `CORS_ORIGINS` and
`NEXT_PUBLIC_API_BASE_URL` default to the published ports, so moving `API_PORT`
or `WEB_PORT` does not silently leave the frontend calling the wrong host.

---

## Architecture

```
                          ┌──────────────┐
  Browser ───────────────►│  web         │  Next.js 15 · React 19 · TS strict
                          │  (Next.js)   │  TanStack Table/Query · Tailwind
                          └──────┬───────┘
                                 │ JSON over HTTP, httpOnly session cookies
                          ┌──────▼───────┐
                          │  api         │  FastAPI · Pydantic v2 · SQLAlchemy 2 async
                          │  (Gunicorn)  │  RFC 7807 errors · JWT + refresh rotation
                          └──┬────┬───┬──┘
              enqueue tasks  │    │   │  read/write
             ┌───────────────┘    │   └──────────────┐
             │                    │                  │
      ┌──────▼──────┐      ┌──────▼──────┐    ┌──────▼──────┐
      │   redis     │      │   minio     │    │  postgres   │
      │ broker+cache│      │ blobs (SSE) │    │  16         │
      └──────▲──────┘      └──────▲──────┘    └──────▲──────┘
             │                    │                  │
      ┌──────┴───────────────────-┴──────────────────┴──────┐
      │  worker (general queues)   │  worker-ocr (ocr queue) │
      │  ingest · normalize · text │  rasterise · preprocess │
      │  classify · extract        │  tesseract · paddleocr  │
      │  validate · export         │                         │
      └────────────────────────────┴─────────────────────────┘
                          ┌──────────────┐
                          │  beat        │  retention sweeps, reconciliation
                          └──────────────┘
```

### The pipeline

Each stage is a separate Celery task that reads its input from, and writes its
output to, the database. That is what makes the pipeline **resumable** after a
crash and lets an individual stage be re-run without redoing the whole document.

```
Ingest → Normalize & Split → Text Extraction → Classify
      → Field Extraction (template → rules → LLM) → Validate → Confidence & Routing
```

OCR runs on its own queue with its own concurrency ceiling. It is CPU-bound and
can hold a core for seconds per page; on a shared queue it would starve
everything else.

### Why two database stacks

The API uses **asyncpg** (`DATABASE_URL`). Celery workers and Alembic use
**psycopg** synchronously (`DATABASE_URL_SYNC`) because pipeline stages do
blocking CPU work — OCR, rasterisation — where an event loop buys nothing and
complicates prefork semantics. Both read the same models and the same migrations,
and a test asserts the two never drift.

---

## Repository layout

```
api/                  FastAPI app + the shared `certex` package
  certex/
    config.py           every tunable, typed and validated at boot
    enums.py            domain enums (persisted values — renaming one is a migration)
    logging_setup.py    structlog with mandatory PII redaction
    queue.py            Celery app + queue topology (shared with the worker)
    cli.py              seed / ensure-bucket / create-user / check
    core/               auth, errors, middleware, auditing, rate limiting
    db/                 declarative base, ORM models, session factories
    schemas/            Pydantic request/response models
    services/           orchestration between the API layer and the database
    storage/            S3/MinIO adapter
    api/v1/             routers
  tests/
    unit/               pure logic, no external services
    integration/        needs Postgres
worker/               Celery entry point and task wrappers
web/                  Next.js frontend
infra/
  docker-compose.yml    the canonical stack definition
  alembic/              migrations
  docker/               Dockerfiles and entrypoints
```

The shared logic lives in `api/certex/` and both the API and the worker images
install it, so there is exactly one copy of the models, the configuration and the
pipeline code.

---

## Development

### Backend

```bash
cd api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
```

```bash
docker compose up -d postgres redis minio
cd api && .venv/Scripts/python -m pytest
```

Integration tests **skip rather than fail** when Postgres is unreachable, so the
pure-logic suite runs on a machine with nothing else installed. They use a
dedicated `certex_test` database and wrap every test in a transaction that is
rolled back, so tests share a schema without sharing rows.

Quality gates — all three must pass:

```bash
cd api
.venv/Scripts/python -m ruff check certex tests
.venv/Scripts/python -m ruff format --check certex tests
.venv/Scripts/python -m mypy certex
```

`mypy` runs in **strict** mode. `Any` does not cross a module boundary; JSONB
columns are typed `dict[str, object]` so a reader has to narrow before use.

### Frontend

```bash
cd web
npm install
npm run dev
```

```bash
npm run typecheck   # tsc --noEmit, strict + noUncheckedIndexedAccess
npm run lint
npm run build
```

### Migrations

```bash
cd infra
alembic revision --autogenerate -m "what changed"
alembic upgrade head
alembic check                      # fails if models and migrations disagree
```

`alembic.ini` deliberately carries no `sqlalchemy.url`: `env.py` resolves the
target from `certex.config`, so a migration cannot drift onto a different
database than the application talks to. Passing `sqlalchemy.url` explicitly
overrides that, which has to be a deliberate act.

Two gotchas the initial migration already handles, both worth knowing before you
write the next one:

- `templates.created_from_extraction_id` and `extractions.template_id` form a
  **cycle**. The first is declared `use_alter=True`, which keeps it out of the
  inline `CREATE TABLE` — so the migration adds it as an explicit
  `op.create_foreign_key` once both tables exist. Autogenerate does not emit that
  pair; it is maintained by hand.
- Enums are stored as checked `VARCHAR`, not native Postgres types, so adding a
  member is an ordinary constraint swap.

---

## Security model

This system handles personal identity documents. The following are requirements,
not preferences, and each one has tests.

**Authentication.** Short-lived JWT access tokens carry the workspace and role,
so an authenticated request needs no database round trip to authorise. Refresh
tokens are opaque random strings; only their SHA-256 digest is stored. Every
refresh rotates the token within a family — presenting an already-consumed token
means a stolen cookie was replayed, so the whole family is revoked and the event
is audited. All tokens travel in `httpOnly`, `SameSite` cookies and never appear
in a response body.

**Authorisation.** Three roles: `ADMIN` (settings, users, delete), `OPERATOR`
(upload, review, export), `VIEWER` (read and export). Workspace isolation is
enforced in the query layer via a `WorkspaceScope` that handlers must pass to
repository calls — not in the UI.

**PII in logs.** Field *values* are personal data and must never reach a log
sink. The redaction policy is an **allowlist**: a key in `SAFE_KEYS` passes
through, a key matching the sensitive pattern is replaced wholesale, and
everything else is pattern-scrubbed and length-capped. Code that invents a new
key carrying document content is scrubbed by default rather than leaking until
someone notices.

Event messages get a second rule, because pattern scrubbing cannot recognise an
address or a person's name. Events from first-party loggers must be **static
event names with no whitespace** — `extraction.completed`, not a sentence.
Anything else is suppressed and replaced with a stable digest. Data belongs in
structured fields where the allowlist governs it.

```python
logger.info("extraction.completed", document_id=doc.id, field_name="date_of_birth")   # good
logger.info(f"extracted {name} from {path}")                                          # suppressed
```

The same filter is installed on stdlib logging, so SQLAlchemy, botocore and
Celery records cannot bypass it. `tests/unit/test_logging_redaction.py` renders
real records and asserts a corpus of realistic field values — Latin and Urdu
names, national IDs, addresses, printed dates — appears nowhere in the output.

**Audit trail.** Every upload, view, correction, export and delete writes an
`audit_log` row with the actor and their IP, inside the same transaction as the
action itself, so the trail cannot disagree with what happened. Audit metadata is
filtered through the same allowlist: a caller that tries to audit a field value
gets an empty object and a warning.

**Encryption at rest.** Every object write carries server-side encryption
parameters. MinIO rejects SSE-S3 unless a KMS is configured, so the dev stack
ships a built-in key — encryption is genuinely exercised locally rather than
silently disabled. Boot verifies this by writing and deleting a probe object, and
says so loudly if the backend refuses:

```
[storage] server-side encryption verified (AES256)
```

**Storage keys** are derived from UUIDs, never from filenames. A filename cannot
influence a key, so path traversal is structurally impossible and a person's name
never lands in object-storage metadata or access logs.

**CSRF.** `SameSite` blocks the classic cross-site form post, but it is a
browser-side control. Cookie-authenticated state changes must additionally echo
the readable `certex_csrf` cookie in an `X-CertEx-CSRF` header. Bearer-token
requests are exempt — a bearer token is not attached ambiently, so there is
nothing to forge.

---

## Configuration

Every variable the stack reads is documented in [`.env.example`](.env.example).
Settings are validated at boot, and incoherent combinations refuse to start
rather than misbehave later:

- `CONFIDENCE_REVIEW_FLOOR` above `CONFIDENCE_AUTO_APPROVE`
- `CELERY_TASK_SOFT_TIME_LIMIT` at or above the hard limit
- `COOKIE_SAMESITE=none` without `COOKIE_SECURE`
- `S3_SSE=aws:kms` without a key id
- `LLM_PROVIDER=local` without a base URL

With `APP_ENV=production` the guards tighten further: a default or short
`SECRET_KEY`, `COOKIE_SECURE=false`, `DB_ECHO=true` or `SEED_ENABLED=true` each
prevent boot. `DB_ECHO` is on that list because echoed statements carry bind
parameters, and bind parameters are field values.

---

## Health checks

| Endpoint | Purpose |
|----------|---------|
| `GET /health/live` | Process can serve. Touches no dependency. Restart signal. |
| `GET /health/ready` | Dependencies reachable. Returns 503 when a required one is down. Traffic signal. |

Readiness probes run concurrently with individual timeouts, so one wedged
dependency cannot make the probe itself hang.

---

## Operational CLI

```bash
python -m certex.cli seed           # demo workspace and role accounts
python -m certex.cli ensure-bucket  # create the bucket, verify encryption
python -m certex.cli check          # database, storage and broker connectivity
python -m certex.cli create-user --email a@b.com --password ... --role ADMIN
```

Every command is idempotent, so container entrypoints run them on each boot.

---

## Errors

Every error is an [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807) problem
document carrying a stable machine `code` and a `remediation` sentence saying
what to do next — an error that only says "processing failed" is not actionable
for someone holding a thousand scans.

```json
{
  "type": "https://certextract.invalid/problems/document_encrypted",
  "title": "Document is password protected",
  "status": 422,
  "code": "document_encrypted",
  "detail": "This PDF is encrypted and cannot be read without its password.",
  "remediation": "Supply the document password with the upload, or remove the protection before uploading.",
  "instance": "/api/v1/batches/2f1a.../files",
  "request_id": "9c3f1e..."
}
```

The `request_id` is echoed in the `X-Request-ID` response header and appears on
every log record for that request, so a user reporting a failure gives support an
exact key into the logs without anyone quoting document contents.
