# Risk Report Data Extraction -- Working Prototype

A runnable implementation of the pipeline described in `UVIMCO_project_proposal.pdf`:
an agentic model that extracts text from PDF risk reports, uses an LLM to pull out
structured fields, validates them deterministically, stores everything with an
append-only audit trail, and routes anything uncertain to a human reviewer whose
feedback improves the system over time.

## How this maps to the proposal

| Proposal step | Implementation |
|---|---|
| Step 1 -- Text extraction (no LLM) | `extraction/text_extractor.py`: `pypdf` for text, `camelot` for tables (falls back to `pdfplumber` if Ghostscript isn't installed), `pytesseract` OCR for pages with no extractable text. |
| Step 2 -- Text processing (LLM) | `llm/fund_analyst_agent.py`: a "Fund Analyst Agent" whose prompt lives in the database (`prompt_templates` table, editable from the dashboard) rather than a markdown file. Calls the real Claude API if `ANTHROPIC_API_KEY` is set; otherwise falls back to a deterministic mock extractor so the whole pipeline still runs. |
| Step 3 -- Validation & reconciliation (no LLM) | `validation/validators.py`: type checks, static sanity bounds, and a *dynamic* range check (z-score against a `ground_truth` table of prior human-approved values, standing in for the "database of ground truth" + SEC Form ADV data mentioned in the proposal). |
| Step 4 -- Loading & dissemination | `db/models.py` (structured store, SQLite standing in for Postgres) + `vectorstore/store.py` (Chroma, local/offline embedding function) + `api/main.py` (FastAPI for downstream applications). |
| Human-in-the-loop | `review/notifier.py` (simulated email task) + `dashboard/app.py` (Streamlit review queue, approve/correct/reject, prompt editing) + `pipeline/orchestrator.record_review_decision` (feeds corrections back into `ground_truth` and, optionally, into the agent's prompt). |
| Audit log | `audit.py` + `AuditLogEntry` model: every step in every component writes an append-only row (timestamp, actor, document, step, action, result, JSON details). Nothing in the codebase updates or deletes a log row. |

## What's simplified for a local prototype (and how to upgrade it)

This runs entirely on your machine with no external services, which meant a few
substitutions for the production technologies named in the proposal:

- **Postgres -> SQLite.** `db/database.py` reads `DATABASE_URL` from `config.py`.
  Point it at a Postgres URL and the SQLAlchemy models need no changes.
- **Chroma's default embeddings -> a local hashing embedder.** Chroma's built-in
  embedding function downloads an ONNX model from the internet on first use; that
  network call is blocked in this sandbox. `vectorstore/embeddings.py` implements a
  small offline hashing-trick embedder instead, so the vector store works without
  network access. It's good enough to demonstrate chunking/indexing/retrieval, but
  it is *not* a real semantic embedding model -- swap in OpenAI/Voyage/a cached
  sentence-transformer for real RAG quality.
- **AWS -> local filesystem.** Uploaded PDFs live in `storage/uploaded_pdfs/`;
  swap for S3 behind the same `config.UPLOAD_DIR` seam.
- **Real email -> simulated.** `review/notifier.py` prints `[SIMULATED EMAIL]` and
  logs an `EmailTask` row instead of calling SMTP/SES. The task queue is real; only
  the transport is stubbed.
- **SEC Form ADV / external reference data -> not wired up.** The `ground_truth`
  table's `source` column already distinguishes `human_verified` / `learned` /
  `form_adv` rows; a real Form ADV fetch (e.g. from `data.sec.gov`) just needs to
  insert rows with `source="form_adv"`.
- **Camelot table extraction** needs Ghostscript for some PDF types; it isn't
  installed in this sandbox, so the extractor automatically falls back to
  `pdfplumber` for tables. Both paths are exercised and logged (see
  `extraction_method` on each document).

None of these are deep architectural changes -- each one is a single module with a
clearly marked seam (`config.py` for storage locations/credentials,
`vectorstore/embeddings.py` for the embedding function, `review/notifier.py` for
the send step).

## Setup

```bash
cd uvimco_pdf_pipeline
pip install -r requirements.txt
cp .env.example .env   # optional -- only needed for real Claude calls
```

The pipeline runs with **no API key** out of the box, using the mock extractor.
To use real Claude extraction, put your key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Quickstart

```bash
# 1. Create the database and seed default prompts / ground truth history
python cli.py init-db

# 2. Generate a couple of synthetic sample risk reports to try it on
python cli.py generate-samples

# 3. Run the full pipeline (extraction -> LLM -> validation -> storage)
python cli.py ingest sample_data/pdfs

# 4. See what needs a human's attention
python cli.py review-queue

# 5. See everything the pipeline did, step by step
python cli.py audit-log
```

Once you have your own risk report PDFs, drop them anywhere and run:

```bash
python cli.py ingest /path/to/your/reports/
```

## Interactive dashboard (human review + prompt editing)

```bash
streamlit run dashboard/app.py
```

Tabs: upload/ingest a PDF and watch its status; work the review queue
(approve / approve-with-correction / reject, with a feedback box); edit the
Fund Analyst Agent's system prompt directly, or append an SME correction note
to it; browse the full audit log.

## API (for downstream applications)

```bash
python cli.py serve-api
# then open http://localhost:8000/docs
```

Key endpoints: `POST /upload`, `GET /documents`, `GET /documents/{id}`,
`GET /review-queue`, `POST /review/{field_id}`, `GET /audit-log`,
`GET /query?q=...` (RAG-style retrieval over indexed report text), and
`GET/PUT /prompts/...`.

## Tests

```bash
pip install pytest   # if not already installed
pytest -q
```

Covers extraction, the mock LLM extractor, validation (both a clean value and a
deliberately-planted out-of-range one), the full pipeline end to end on both
sample documents, the review-decision -> ground-truth feedback loop, prompt
feedback, and the vector store.

## Project layout

```
config.py                    # all settings/seams (DB URL, LLM key/model, thresholds)
audit.py                     # append-only audit log helper
db/models.py                 # SQLAlchemy schema (documents, fields, audit log,
                              #   ground truth, prompts, email tasks)
db/seed.py                   # default agent prompt + bootstrap ground-truth values
extraction/text_extractor.py # Step 1: pypdf + camelot/pdfplumber + tesseract OCR
llm/fund_analyst_agent.py    # Step 2: Claude API or mock extractor
validation/validators.py     # Step 3: type/range/dynamic-statistical checks
vectorstore/                 # Step 4 (vector half): Chroma + offline embedder
review/notifier.py           # simulated SME email notifications
pipeline/orchestrator.py     # wires Steps 1-4 together + review feedback loop
api/main.py                  # FastAPI app for downstream consumers
dashboard/app.py             # Streamlit SME review + prompt-editing UI
sample_data/generate_sample_pdfs.py  # synthetic demo PDFs (one clean, one flagged)
cli.py                       # command-line entry point
tests/test_pipeline.py       # pytest suite
```

## Extending the field schema

Fields to extract live in `config.FIELD_SCHEMA` (name -> type). Add a field there,
add a regex to `llm/fund_analyst_agent._PATTERNS` for the mock extractor (the real
Claude prompt in `db/seed.DEFAULT_SYSTEM_PROMPT` already asks it to extract
"exactly these fields," so update the field list there too), and, if it's numeric,
optionally add a bootstrap range to `validation/validators.STATIC_BOUNDS`.
