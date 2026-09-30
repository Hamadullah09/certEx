# CertEx

A civil certificate register, and the machinery that fills it.

An office keeps birth, marriage and death records — and whatever else it issues.
CertEx holds them as a **register**: one entry per certificate, found by its
certificate number and by the names printed on it, linked to the scan it was read
from. Entries arrive three ways, and all three land in the same register:

* **Read from documents.** Upload a folder of certificates — PDFs, Word files, or
  image-only scans. The pipeline splits multi-certificate files, reads each one
  from its text layer or with OCR, extracts typed fields with a confidence score
  and a provenance tag, checks them against each other, and sends the uncertain
  ones to a reviewer.
* **Imported from a spreadsheet.** An office that already keeps its registers in
  Excel uploads the CSV; a worker reads it a row at a time and reports every row it
  could not file, by the line number the clerk will find.
* **Typed in.** One record at a time, when somebody is standing at the counter.

What the register is *for* is answering a question in thirty seconds: somebody has
a number, or a name and roughly a year, and needs their certificate.

### What it will not do

These are decisions, not gaps:

* **It never overwrites an entry.** A certificate number the register already holds
  produces a second entry linked to the first and a question for a person. There is
  no import policy that replaces a record.
* **It never merges people by name.** Four people really are called Muhammad Ahmed.
  Names bring candidates together for a clerk to judge; they never join records.
* **It never deletes history.** A correction keeps what the entry used to say. A
  cancelled certificate stays in the register, because whoever is holding it has to
  be told it was cancelled. A replaced scan is kept, because a value in the register
  was read from it.
* **It never serves a document from a public URL.** The bucket is private; scans are
  streamed through the API, which checks the session and records who looked.

---

## Build status

Two rounds of work. The extraction pipeline was built first, then the register was
built around it. **A phase is only marked done when its tests pass.**

### The register

| # | Phase | Status |
|---|-------|--------|
| 1 | Architecture review — what existed, what had to change | ✅ Done |
| 2 | Dynamic schemas — field roles, immutable versions, the registry API | ✅ Done |
| 3 | The register — certificates, names, dates, documents by relationship | ✅ Done |
| 4 | Bulk CSV — template, streaming import, streaming export | ✅ Done |
| 5 | Search & navigation — tiered search, register screens, secure scans | ✅ Done |
| 6 | Review — queue, entry history, configurable thresholds | ✅ Done |
| 7 | Versioning — document replacement, supersession | ✅ Done |
| 8 | Security — tenancy matrix, RBAC matrix, CSRF, rate limits, audit | ✅ Done |
| 9 | Scale — plan-asserted indexes, parameterised to 1M entries | ✅ Done |
| 10 | Production polish — documentation, deployment overlay, backup and recovery | ✅ Done |

### The extraction pipeline

| # | Phase | Status |
|---|-------|--------|
| 1 | Scaffold — monorepo, Compose, schema + migrations, auth, health | ✅ Done |
| 2 | Upload & storage — MinIO, content sniffing, dedupe, archives, upload UI | ✅ Done |
| 3 | Text extraction — native PDF text, DOCX, page-quality scoring | ✅ Done |
| 4 | OCR — rasterisation, OpenCV preprocessing, despeckling, Tesseract | ✅ Done |
| 5 | Splitting & classification — boundary detection, type classifier | ✅ Done |
| 6 | Rules extraction — synonyms, spatial rules, template engine | ✅ Done |
| 7 | Validation & confidence — normalisers, cross-field checks, routing | ✅ Done |
| 8 | Results UI — virtualised grid, filters, inline edit, source pane | ✅ Done |
| 9 | Export — streaming CSV, XLSX, JSON | ✅ Done |
| 10 | Template learning — save corrections as reusable templates | ✅ Done |
| 11 | Hardening — retries, DLQ, audit, retention, rate limits | ✅ Done |

Certificates are read with a text layer, OCR, label rules and learned templates, with
an optional LLM fallback for the fields none of those could find. Every value carries
the method that produced it, and a value the fallback returned is only kept if it
appears verbatim in what was read.

---

## Quick start

Requirements: Docker with Compose v2, and roughly 6 GB of free disk for the images.
Tesseract and LibreOffice are really installed, with the Urdu language data and the
fonts to render it - nothing in the reading path is stubbed.

```bash
cp .env.example .env
docker compose up
```

That brings up Postgres, Redis, MinIO, the API, two classes of Celery worker, the
scheduler and the frontend; applies migrations; verifies the schema matches the
models; provisions the storage bucket; proves server-side encryption actually
works; and seeds a demo workspace.

| Service | URL (default ports) | Credentials |
|---------|---------------------|-------------|
| Frontend | http://localhost:3000 | `admin@example.com` / `admin12345` |
| API docs | http://localhost:8000/docs | — |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |

Every port is configurable and a `.env` may well have moved them — see *Ports already
in use?* below.

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

The two Python images are the same dependency layer with different entrypoints; the
worker adds only the `worker` package. Tesseract, its Urdu language data and the Noto
fonts are installed at build time, so a scanned page never blocks on a download and the
container reads certificates with no outbound network at all.

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
      │  classify · extract        │  despeckle · tesseract  │
      │  validate · finalize       │                         │
      │  export · import          │                          │
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
      → Field Extraction (template → rules → model fallback)
      → Validate → Confidence & Routing → Finalize → the register
```

The last step is what separates a reading from a record: once every unit of a
document has finished, its rows are published as register entries — found by
certificate number, searchable by the names on them, linked to the pages they were
read from. Publishing there rather than per unit is deliberate: until the whole
document is settled, a later unit could still fail and take it with it.

OCR runs on its own queue with its own concurrency ceiling. It is CPU-bound and can
hold a core for seconds per page; on a shared queue it would starve everything
else. CSV import has its own queue for the opposite reason: one file occupies a
worker for minutes, and behind the export queue it would hold up every download in
the office.

### Why two database stacks

The API uses **asyncpg** (`DATABASE_URL`). Celery workers and Alembic use
**psycopg** synchronously (`DATABASE_URL_SYNC`) because pipeline stages do
blocking CPU work — OCR, rasterisation — where an event loop buys nothing and
complicates prefork semantics. Both read the same models and the same migrations,
and a test asserts the two never drift.

---

## The register

An **extraction** is what one upload of one file appeared to say. A **certificate** is
what the office holds. The difference is the whole design: an entry outlives the batch,
the document and even the schema version it was first read under, and it has to keep
answering for itself long after all three are gone.

```
certificates              one entry per certificate the office holds
  ├── values_jsonb          the whole row, under whatever field names the schema defines
  ├── indexed columns       filled by field *role*, not by field name
  ├── certificate_names     the roles that repeat - both parties, every parent
  ├── certificate_dates     likewise, so a date-range search sees all of them
  ├── certificate_documents the scans, linked by relationship and never by filename
  └── certificate_revisions everything that has ever happened to this entry
```

### Field roles

A schema says a field is called `child_full_name`; a **role** says it is the
`subject_name`. Everything generic keys off the role, which is why one implementation
serves birth, marriage, death and a type an administrator invents this afternoon:

| Role | What generic code does with it |
|------|-------------------------------|
| `identifier` | the certificate number: indexed, quoted at the counter, checked for duplicates |
| `subject_name`, `party_name` | who the entry is filed under |
| `father_name`, `mother_name`, `spouse_name` | searchable, but not who it is filed under |
| `birth_date`, `death_date`, `marriage_date`, `event_date` | which date the certificate is *about* |
| `registration_date`, `issue_date` | ordering checks: a certificate cannot be issued before it was registered |

**A role may appear more than once.** A marriage certificate has two parties and two
dates of birth. Every role lookup therefore returns a tuple, and a caller that genuinely
wants one value asks for the first — a lookup that quietly returned one of two would skip
half the checks and nobody would see it happen.

### Schema versions are immutable

A published version is never edited. A certificate records the exact version it was read
under, so a field renamed next year cannot change what last year's entry means. A new
version is additive and gets a new number; the old one keeps serving the entries that
point at it. Immutability is also what makes the resolved-schema cache safe.

---

## Finding a certificate

Search answers in **tiers**, most certain first, and reports which tier answered:

| Tier | When it answers |
|------|-----------------|
| `certificate_number` | the number, exactly — separators, case and Urdu digits folded away |
| `number_prefix` | three characters or more of a half-remembered number |
| `name` | the name exactly as it normalises, in any role on the certificate |
| `similar_name` | trigram similarity, for another transliteration or a spelling nobody agrees on |
| `filtered` | no search text: the filters alone, newest event first |

A tier that finds nothing falls through; a tier that finds something wins. Mixing exact
number matches with fuzzy name guesses in one list buries the answer, and the reported
tier is what lets the screen be honest: *"no certificate with that number, but here are
three people with that name"* is a different answer from *"here is the certificate"*.

Names are not unique and the register does not pretend otherwise. Every result carries
how many entries share its name, alongside the date and the number, because four people
really are called Muhammad Ahmed and telling them apart is a clerk's job.

Ranked results page by **offset**; the plain browse pages by **cursor**. A keyset needs a
total order that does not depend on the query, and similarity does.

---

## Loading an existing register

Offices already keep their registers in spreadsheets. The path in is a CSV:

```
GET  /api/v1/certificate-types/{id}/import-template   the columns this type expects
POST /api/v1/imports                                   upload; returns 202 and an import to poll
GET  /api/v1/imports/{id}                              counts, updated while the file is read
GET  /api/v1/imports/{id}/errors                       every row that could not be filed
```

The request hashes the file, writes it to object storage and queues it. A worker reads it
back a row at a time, so a three-hundred-megabyte file never lands in memory and no
request is held open for the minutes it takes.

**Three rules the loader will not break.**

*It will not overwrite.* A certificate number the register already holds is either
skipped with an error naming the entry that holds it, or recorded as a second entry
linked to the first — whichever the operator chose. There is deliberately no third
option.

*It will not stop on a bad row.* Row 4,182 having an unreadable date is a fact about row
4,182. It is recorded against the line number a clerk will find in their spreadsheet,
with the offending value, and the other 299,999 rows are loaded.

*It will not half-load a bad file.* Unknown columns, or a missing certificate-number
column, are decided before the first row is written. An office cannot un-import a
register.

Export streams the other way — `GET /api/v1/certificates/export` — one certificate type
per file, because their columns differ. Rows go from the database into the download a
page at a time, so memory is flat however large the register is.

---

## Review, correction and history

Everything that happens to an entry is a person's recorded decision.

```
POST  /api/v1/certificates/{id}/approve              accept as it stands
PATCH /api/v1/certificates/{id}                      correct a value, with a reason
POST  /api/v1/certificates/{id}/void                 cancel without removing (admin)
POST  /api/v1/certificates/{id}/resolve-duplicate    settle whether two entries are one
GET   /api/v1/certificates/{id}/history              everything that has ever happened
```

* **A correction states its reason.** A changed value with no explanation is
  indistinguishable from a mistake six months later. The previous values are kept, and
  the indexed columns and name rows are re-derived so the entry is findable by what it
  says *now*.
* **Approving is not correcting.** A reviewer accepting a reading leaves the values alone
  and the history says so, because the two mean different things.
* **Duplicates are resolved, not merged.** Confirming two entries are one certificate
  marks the later one superseded and points it at the earlier. Nothing is deleted and no
  values are combined: the office may have to explain either entry to whoever is holding
  a copy of it.
* **Voiding keeps the record.** A cancelled certificate stays in the register and stays
  findable by its number, because somebody holding it has to be told it was cancelled and
  a missing record cannot tell them anything.

What goes to review is the office's decision, set in **Settings**: the confidence at
which a reading is accepted without a person, the confidence below which it counts as
unread rather than doubtful, and whether imports and duplicate numbers queue work at all.
An archive digitising fifty-year-old registers and an office issuing certificates today
want different answers.

---

## Extraction layers

Cheapest first, each filling only what the last one left:

```
template  →  rules  →  (optional) model fallback
```

Every value carries the method that produced it, its confidence, and where on the page it
was found. The merge ranks a template above a rule above the fallback, and anything a
person typed above all three.

The **model fallback** is off unless a deployment switches it on and supplies a key,
because enabling it sends the text of a certificate — somebody's name, their parents'
names, their identity number — to a third party. That is an office's decision to take
knowingly, not a default to inherit.

What makes it safe to have in a register is the verification. A model asked to read a
certificate will sometimes produce a value that is plausible, well-formed and printed
nowhere on the document. So every value it returns is looked for in the text that was
sent — loosely about form, because a model legitimately reformats an identity number, and
strictly about substance, because a word the document does not contain is a word somebody
invented. A value that fails is kept and flagged `VALUE_UNVERIFIED`, so it reaches a
reviewer with a warning rather than looking like any other reading, and it is scored too
low to carry a row over any threshold.

It also cannot overwrite a better layer, cannot run when nothing was read, and cannot
fail a document: an absent, slow or rate-limited model is a flag on the row and the other
layers' values.

---

## Scale

The register is built for hundreds of thousands of entries and tested at a million.

| What | How it stays fast |
|------|-------------------|
| certificate number | B-tree on `(workspace_id, certificate_number_key)` |
| name, exactly | B-tree on `(workspace_id, primary_name_key)`, plus the side table for other roles |
| name, similar | GIN trigram on `primary_name_key` and on `certificate_names.value_key` |
| browse | keyset cursor over `(created_at, id)` — an offset would skip or repeat rows as the register is written |
| type + date window | composite index on `(workspace_id, certificate_type_id, event_date)` |
| duplicate queue | partial index, so it reads only the rows with a question against them |
| export | streamed a page at a time; memory is flat |

`tests/load/` asserts the **query plan** rather than only the clock, because a wall-clock
number on one machine says little and a sequential scan over the register is a defect
whatever the clock says:

```bash
pytest tests/load -m load                              # 10,000 entries
CERTEX_SCALE_ROWS=100000 pytest tests/load -m load     # 100,000
CERTEX_SCALE_ROWS=1000000 pytest tests/load -m load    # a million
```

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
    fields/             what a certificate contains: specs, roles, schemas
    certificates/       turning a read row into a register entry; the search keys
    imports/            reading an operator's CSV and the template it fills
    llm/                the optional model fallback, behind a provider protocol
    pipeline/           extraction: text, ocr, split, classify, extract, validate
    export/             streaming CSV, XLSX and JSON writers
    schemas/            Pydantic request/response models
    services/           orchestration between the API layer and the database
    storage/            S3/MinIO adapter
    api/v1/             routers
  tests/
    unit/               pure logic, no external services
    integration/        needs Postgres, Redis and MinIO
    golden/             field accuracy against hand-labelled fixtures
    load/               the register at 10k / 100k / 1M entries
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
cd api && .venv/Scripts/python -m pytest -m "not load"
```

`./scripts/check.sh` runs every gate the project enforces - ruff, ruff format, mypy
strict, the backend suite, then tsc and eslint - and is what a change has to pass. The
load tests are excluded from it and run deliberately; see [Scale](#scale).

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

**Refusal is "not found".** Every register route is scoped to one workspace inside the
same statement that looks the row up. A real id belonging to another office answers 404,
not 403, because 403 confirms the id exists — which is itself the leak. A single
table-driven test walks every route with a borrowed id, so a route added without
authorisation fails an existing test rather than waiting for somebody to write a new one.

**Scans are never served from a URL.** The bucket is private and stays private. A
document is streamed through an API route that checks the session, checks the document is
attached to an entry in *this* workspace, records who looked, and names the download after
the certificate number rather than after the uploaded filename. There is no signed link,
because a link that works without a session works for whoever finds it.

**The audit trail holds no field values.** A correction records *which* fields changed and
never what they now say. `"fields"` is on the logging denylist for exactly this reason —
it almost always means field values — so the correction audit uses the allowlisted
`field_names` and carries names only. One test is stricter than the redaction policy
itself: it captures what the register passes to the logger and asserts no field value is
passed at all, because redaction scrubs patterns and a person's name has no pattern.

**Rate limits are keyed to the user, not the address.** An office shares one address, and
keying on it would let one clerk's script lock out the counter. Searching and exporting
are bounded per minute — one export can read the whole register — and imports per *hour*,
because what is worth bounding there is how much worker time one account can queue.

**The model fallback is off unless an office switches it on.** Enabling it sends the text
of a certificate to a third party, so it is opt-in, `LLM_ENABLED=true` without a key
refuses to boot rather than silently doing nothing, and sending the page *image* is a
second, separate decision. Nothing about the content is ever logged — counts, hashes,
token usage and error types only.

---

## Configuration

Every variable the stack reads is documented in [`.env.example`](.env.example).
Settings are validated at boot, and incoherent combinations refuse to start
rather than misbehave later:

- `CONFIDENCE_REVIEW_FLOOR` above `CONFIDENCE_AUTO_APPROVE`
- `CELERY_TASK_SOFT_TIME_LIMIT` at or above the hard limit
- `OCR_HIGH_DPI` below `OCR_DPI`
- `COOKIE_SAMESITE=none` without `COOKIE_SECURE`
- `S3_SSE=aws:kms` without a key id
- `LLM_ENABLED=true` without `LLM_API_KEY` or without a base URL

With `APP_ENV=production` the guards tighten further: a default or short
`SECRET_KEY`, `COOKIE_SECURE=false`, `DB_ECHO=true` or `SEED_ENABLED=true` each
prevent boot. `DB_ECHO` is on that list because echoed statements carry bind
parameters, and bind parameters are field values.

---

## Deployment

The Compose stack is the deployment. There is no second, divergent set of manifests to
keep in step with it: the same images, the same entrypoints and the same environment
contract run locally and in production. What changes is the environment file and one
overlay.

```bash
cp .env.example .env          # then edit it - see the checklist below
export COMPOSE_FILE=docker-compose.yml:infra/docker-compose.prod.yml
docker compose build
docker compose run --rm migrate
docker compose up -d
```

[`infra/docker-compose.prod.yml`](infra/docker-compose.prod.yml) is only the
differences, and each one is a development convenience that is wrong in production:

| The base stack does this | The overlay undoes it because |
|---|---|
| mounts `api/`, `worker/` and `web/` over the images | a container would run whatever is on the host's filesystem rather than what was built, tested and tagged — a release stops being reproducible, and a stray edit on the server is a silent production change |
| publishes the API and the frontend on host ports | a reverse proxy terminates TLS and forwards to the service; it is also what makes `--scale api=N` possible, since a fixed host port collides on the second replica |
| publishes Postgres, Redis and MinIO on host ports | in production that is the database and every scan on a host port. Maintenance goes through `docker compose exec`, which is how the backup and restore commands below already work |

Build with `INSTALL_DEV=false` for a release image: the base stack ships the test
tooling so the suite runs against the same image the services run, which a deployed
image does not need.

### Before the first production boot

| Set this | Why |
|----------|-----|
| `APP_ENV=production` | Turns on the guards below and hides the interactive API docs |
| `SECRET_KEY` | 32+ random bytes. The default prefix refuses to boot in production |
| `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `MINIO_KMS_SECRET_KEY` | The shipped values are development defaults and are public |
| `COOKIE_SECURE=true`, `COOKIE_DOMAIN` | Session cookies must not travel in the clear |
| `CORS_ORIGINS` | The web origin, exactly. Not `*` |
| `SEED_ENABLED=false` | The demo accounts have known passwords |
| `DB_ECHO=false` | Echoed statements carry bind parameters, and bind parameters are field values |

`APP_ENV=production` refuses to boot on a default or short `SECRET_KEY`,
`COOKIE_SECURE=false`, `DB_ECHO=true` or `SEED_ENABLED=true`. The point of failing at boot
is that each of those is silent at runtime and expensive afterwards.

### Scaling the pieces

Each process is horizontal. What to add depends on the queue that is behind:

```bash
docker compose up -d --scale api=4          # request throughput
docker compose up -d --scale worker=6       # extraction, validation, import
docker compose up -d --scale worker-ocr=3   # scans, which are CPU-bound
```

Scaling `api` needs the production overlay, or the second replica collides on the
published port. Within one container, `GUNICORN_WORKERS` is the other dial.

`worker-ocr` serves only the `ocr` queue and has its own concurrency ceiling, because a
page of OCR holds a core for seconds and on a shared queue it would starve everything
else. `beat` is the exception: exactly one, or the retention sweeps run twice.

Behind a reverse proxy, forward `X-Forwarded-For` — the rate limiter and the audit trail
both record the client address, and without it every request appears to come from the
proxy.

### What to watch

| Signal | Where | Means |
|--------|-------|-------|
| `GET /health/ready` returns 503 | probe | a dependency is down; stop sending traffic |
| `certex_csrf` failures rising | logs, 403s | a client is not echoing the header, or something is forging |
| `dead_letter_tasks` rows | database | a task exhausted its retries; the document needs attention |
| imports stuck in `RUNNING` | `GET /api/v1/imports` | a worker was killed mid-file; see the recovery note below |
| review backlog growing | `GET /api/v1/certificates/review-summary` | either the thresholds are too strict or the scans got worse |

Every log record carries the `request_id` that the API also returns in `X-Request-ID` and
in every error body, so a user reporting a failure gives support an exact key into the logs
without anyone having to quote document contents.

---

## Backup and recovery

Three stores, and they are not equally replaceable.

**Postgres is the system of record.** The register, the review history, the audit trail and
the schema versions live only here. Losing it loses the office's records even if every scan
survives.

**MinIO holds the scans and the uploaded CSVs.** Losing it loses the evidence behind the
values but not the values: entries keep their provenance — the method, the page and the
verbatim snippet each value was read from — because that is copied onto the entry rather
than left in the document.

**Redis holds nothing that matters.** Queued tasks, rate-limit counters and the answer
cache. A lost Redis means some in-flight documents need re-queuing, which the pipeline is
built for: every stage reads its input from the database and writes its output there, so a
re-run is safe.

### Backing up

```bash
# Postgres - a consistent logical dump, compressed, from outside the container
docker compose exec -T postgres pg_dump -U certex -Fc certex > certex-$(date +%F).dump

# MinIO - the image ships `mc`, but two things need supplying. Its bundled `local`
# alias carries no credentials, and the container does not see S3_BUCKET, so pass
# the bucket in. /backup is wherever the office's backups are mounted.
docker compose exec -T -e BUCKET=certex-documents minio sh -c \
  'mc alias set backup http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" \
   && mc mirror --overwrite "backup/$BUCKET" /backup'
```

Test the restore, not the backup. A dump nobody has restored is a hypothesis.

### Restoring

```bash
docker compose down                  # nothing may hold a connection to the database
docker compose up -d postgres minio

# Connected to `postgres`, not to `certex`: a database cannot drop itself.
docker compose exec -T postgres psql -U certex -d postgres \
  -c 'DROP DATABASE IF EXISTS certex' -c 'CREATE DATABASE certex'
docker compose exec -T postgres pg_restore -U certex -d certex --no-owner < certex-2026-09-29.dump

docker compose run --rm migrate      # brings a dump from an older release up to date
docker compose up -d
docker compose exec -T api python -m certex.cli check   # database, storage, broker
```

`migrate` is safe to run against a restored dump: every migration in this repository has
been applied, reversed and re-applied against a live database, and `alembic check` is
clean, so a dump taken at any release can be brought forward.

### Recovering from a killed import

A worker killed mid-file leaves an import marked `RUNNING` with partial counts. This is
reported rather than repaired automatically, because the repair is a judgement:

```sql
SELECT id, original_filename, total_rows, created_rows, failed_rows, started_at
  FROM certificate_imports
 WHERE status = 'RUNNING' AND started_at < now() - interval '2 hours';
```

The rows it already filed are in the register and are correct. Re-running the same file is
safe — every row it already created is detected as a duplicate of itself and skipped — so
the usual repair is to upload the file again and let the duplicate policy do the rest.

### What cannot be recovered by restoring

A restored database is consistent with itself but may be behind object storage, so a scan
uploaded after the dump exists with no entry pointing at it. Those are visible as documents
with no `certificate_documents` row:

```sql
SELECT d.id, d.created_at
  FROM documents d
  LEFT JOIN certificate_documents cd ON cd.document_id = d.id
 WHERE cd.id IS NULL AND d.status = 'COMPLETED';
```

Re-processing the batch reads them again. Nothing is lost that a re-read cannot replace,
which is the reason documents are kept rather than deleted after extraction.

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

---

## Known limitations

Stated rather than discovered later.

**The trigram index is not yet proven at a million rows.** At ten thousand entries
Postgres measurably prefers the workspace B-tree and applies the similarity as a filter,
which is a reasonable cost decision on a small table. The composite GIN index exists and
`tests/load` asserts that, but whether the planner switches to it at a million rows has
not been verified here. Run `CERTEX_SCALE_ROWS=1000000 pytest tests/load -m load` on
hardware resembling the deployment before promising a similarity search at that size.

**Imports are not resumable.** A worker killed mid-file leaves the import marked
`RUNNING`; the recovery is to upload the same file again, which is safe because every row
already filed is detected as a duplicate of itself and skipped. Resuming from the last
committed batch would be better and is not built.

**Retention does not reach the register.** The sweep deletes documents and page images
past `RETENTION_DAYS`. Register entries are never swept, which is correct — they are the
records — but it means an entry can outlive the scan it cites, and the detail screen shows
the provenance without being able to show the image.

**Two office-level settings are still per batch.** OCR languages and expected certificate
types are chosen when a batch is created, not in Settings. The review thresholds moved to
the workspace; these have not.
