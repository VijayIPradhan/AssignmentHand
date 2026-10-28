# AssignmentHand

Turn an assignment PDF into a completed, handwritten-looking assignment, written in **your own
letterforms**.

1. Upload samples of your handwriting → a reusable **handwriting profile** is built from them.
2. Upload the assignment PDF → questions, marks, sub-parts, tables, formulas and figures are extracted
   for you to review and correct.
3. An AI model drafts answers at your chosen level and length → you edit and approve each one.
4. The approved answers are rendered with your glyphs onto ruled/blank/grid paper, **or directly onto
   the original answer-sheet pages, in the space under each question**, and exported as a vector PDF.

> **What the handwriting engine is (and is not).** It does *not* train a machine-learning model and it
> does not "imagine" your handwriting. It cuts your actual letters out of your samples, vectorises them,
> and composes them with measured spacing, baseline drift and per-letter variation. Characters that are
> not in your samples are drawn from a fallback font (clearly reported), or approximated from your own
> similar letters (also reported). See [docs/HANDWRITING.md](docs/HANDWRITING.md).

## Quick start (local development)

Requirements: Python 3.12+ (tested on 3.14), Node 20+ (tested on 24).

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Windows (use .venv/bin/pip on Linux/macOS)
cp ../.env.example .env                                  # then edit: see "Configuration"
.venv/Scripts/python -m uvicorn app.main:app --port 8000
#   SQLite + local storage + in-process worker by default for development:
#   DATABASE_URL=sqlite:///./data/app.db  STORAGE_ROOT=./data/storage  RUN_WORKER_IN_PROCESS=true

# Frontend (second terminal)
cd frontend
npm install
npm run dev            # http://localhost:3000 (proxies /api to BACKEND_URL, default http://127.0.0.1:8000)
# From a phone on the same Wi-Fi: ALLOWED_DEV_ORIGINS=<your PC's Wi-Fi IP> npm run dev -- -H 0.0.0.0
# then open http://<your PC's Wi-Fi IP>:3000 on the phone
```

API docs (OpenAPI/Swagger): http://localhost:8000/api/docs - also exported to `docs/openapi.json`.

## Docker (production-like)

```bash
cp .env.example .env    # set SECRET_KEY, POSTGRES_PASSWORD, AI provider settings
docker compose up --build
# web: http://localhost:3000   (api, worker and postgres run on the internal network)
```

Services: `db` (PostgreSQL 17), `api` (FastAPI; runs `alembic upgrade head` on start), `worker`
(background jobs: OCR, profile processing, AI generation, rendering), `web` (Next.js). Uploaded files live
in the `filedata` volume; nothing is served from public URLs. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Configuration

All settings are environment variables (see [.env.example](.env.example)). The important ones:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing key, 32+ random characters (required in production). |
| `DATABASE_URL` | PostgreSQL in production; SQLite is fine for local use. |
| `LLM_PROVIDER` | `bedrock`, `anthropic`, `openai_compatible` (Ollama, vLLM...) or `none`. |
| `LLM_MODEL` | e.g. `global.anthropic.claude-opus-5-5` (Bedrock) or `claude-opus-5-5` (Anthropic API). |
| `LLM_MODEL_TRANSCRIBE`, `LLM_MODEL_VERIFY` | Task-based routing: lighter models for transcription/OCR and letter checks (unset = `LLM_MODEL`). Failures escalate to `LLM_MODEL`. |
| `LLM_ADAPTIVE_EFFORT` | Medium thinking effort for small questions (<= 3 marks or concise), `LLM_EFFORT` for the rest. |
| `AWS_REGION`, `AWS_BEARER_TOKEN_BEDROCK` | Bedrock region and API key (or standard AWS credentials). |
| `ANTHROPIC_API_KEY` | Anthropic API key when `LLM_PROVIDER=anthropic`. |
| `LLM_PRICING`, `LLM_MONTHLY_BUDGET_USD` | Your real per-model prices (else list-price estimates) and an optional monthly AI budget per user. Usage page: *AI usage*. |
| `LOG_LEVEL`, `LOG_FORMAT`, `LOG_DIR` | Logging: readable console in development, JSON in production; rotating `app.jsonl` / `errors.jsonl` files. |
| `OCR_PROVIDER` | `auto` (Tesseract if installed, else the AI model), `tesseract`, `llm`, `none`. |
| `FALLBACK_FONT_PATH` | Font for characters missing from a profile (a handwriting-style font is picked automatically when available). |
| `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `RATE_LIMIT_*` | Upload limits and per-user rate limits. |
| `RETENTION_DAYS` | Auto-delete assignments untouched for N days (0 = never). |

With `LLM_PROVIDER=none` (or missing credentials) everything except AI generation still works: you can
type answers yourself and the UI explains how to enable AI.

## Using it well

- **Best letter shapes:** download the template sheet (*Handwriting → Download template sheet*), write one
  character per box, scan or photograph it flat with all four corner squares visible, and upload it.
  Two sheets cover a-z (3 variants each), A-Z, digits, punctuation and common maths symbols.
- **Free-form pages** (a page of your normal writing, ruled or unruled) also work: add a transcript (or let
  the AI draft one), and letters are cut out automatically. Joined writing is cut approximately; with an AI
  provider configured, each cut-out letter is checked by the model and wrong cuts are discarded.
- **Answer sheets with blank space:** when the assignment PDF leaves room under each question, the app
  writes the answer there (paper style "Original pages"), sizes the AI answer to the space measured with
  your handwriting, shrinks the writing slightly if needed, and warns if anything would not fit.

## Development

```bash
cd backend
.venv/Scripts/python -m pytest -q          # 105 tests: handwriting, ingestion, AI, rendering, security, e2e
.venv/Scripts/ruff check app tests && .venv/Scripts/ruff format --check app tests
DATABASE_URL=sqlite:///./data/check.db .venv/Scripts/alembic upgrade head && .venv/Scripts/alembic check
cd ../frontend && npm run lint && npm run typecheck && npm run build
```

Tests use synthetic handwriting (fonts degraded with rotation, perspective, shadows and noise) and
generated PDFs - never real user samples - and a scripted fake LLM, so they need no network or API key.
CI: [.github/workflows/ci.yml](.github/workflows/ci.yml) (lint, PostgreSQL migrations, tests, frontend build).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - components, data model, job states, security model
- [docs/HANDWRITING.md](docs/HANDWRITING.md) - how profiles are built and rendered; limitations
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - production deployment, backups, scaling
- [docs/API.md](docs/API.md) - API overview and workflow (full schema in `docs/openapi.json`)

## Responsible use

This tool produces text in your handwriting from AI-drafted answers. Many institutions prohibit
submitting AI-generated work or require disclosure. You are responsible for following your
institution's academic-integrity rules; review, understand and edit every answer you approve. The app
never adds signatures, seals or fabricated annotations.
