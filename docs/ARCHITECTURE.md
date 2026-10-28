# Architecture

```
Browser ──> Next.js (frontend/, :3000) ──/api proxy──> FastAPI (backend/app, :8000) ──> PostgreSQL
                                                          │  enqueue                      ▲
                                                          ▼                               │
                                                  processing_jobs table <── worker (python -m app.jobs.worker)
                                                          │
                                                  object storage (local volume; S3-ready interface)
```

## Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `api/` | REST routers (auth, profiles, assignments/questions/answers/render, system). Ownership checks in `api/deps.py`. |
| `handwriting/` | Template sheet, preprocessing, ruled-line removal, segmentation, cursive slicing, AI verification, glyph library. |
| `ingestion/` | PDF validation, text/table/figure extraction (pdfplumber + pypdfium2), OCR providers, question segmentation. |
| `ai/` | Provider abstraction (Anthropic, Bedrock, OpenAI-compatible), prompts, generation + validation, retrieval, pricing. |
| `render/` | Markup + LaTeX parsing, handwriting engine interface, math layout, page layout/pagination, PDF output, typed exports. |
| `jobs/` | DB-backed queue, handlers, worker loop (retries, backoff, stale-job recovery). |
| `services/` | Domain operations shared by API and worker (profiles, assignments pipeline, retention). |
| `storage/` | Storage interface + local implementation (path-traversal-safe keys). |

## Data model

`User` 1─* `HandwritingProfile` 1─* `HandwritingSample`; `User` 1─* `Assignment` 1─* `SourceDocument`
(assignment PDF + reference PDFs), `ExtractedQuestion` 1─1 `GeneratedAnswer`, 1─1 `RenderConfiguration`,
1─* `RenderedDocument`; `ProcessingJob` references a user and optionally an assignment or profile. Every
user-owned row carries `user_id`; all lookups go through ownership checks, and other users' rows are
indistinguishable from missing rows (404). Indexes cover per-user listing, assignment children and job claiming.
Schema migrations: Alembic (`backend/alembic`).

### Assignment states

```
uploaded → extracting → awaiting_review → (confirm) → generating_answers → awaiting_approval → rendering → completed
                 ↘ extraction_failed (retry/re-extract)                                   ↘ render_failed (retry)
```

Answer states: `pending → generating → generated → edited → approved` (or `failed`, with the error kept).
Editing an approved answer un-approves it; regeneration never touches approved answers unless asked, and the
previous version is kept in the answer history (restorable).

### Jobs

`queued → running → succeeded | failed`. Claiming uses a conditional `UPDATE ... WHERE status='queued'`, safe for
several workers on PostgreSQL. Transient errors (rate limits, timeouts, provider outages) retry with exponential
backoff up to `JOB_MAX_ATTEMPTS`; a retry of answer generation only regenerates the answers that failed. Jobs
whose worker died are re-queued after `JOB_STALE_AFTER_S`. Failure hooks put the entity in a recoverable state.

## AI integration

- `LLMProvider.complete_json(system, user, schema, images)` with JSON-schema structured output; responses are
  re-validated with Pydantic; empty or malformed output is retried once, then the answer is marked failed.
- Anthropic: adaptive thinking, `output_config.effort`, server-side refusal fallback. Bedrock: the SDK's Bedrock
  client (Bedrock API key or AWS credentials) with client-side refusal fallback middleware.
- Task-based routing (`get_provider(task)`): `answer` uses `LLM_MODEL` (best quality; adaptive effort - medium
  for small questions); `transcribe` (handwriting transcripts, OCR of scans) and `verify` (letter checks) can use
  lighter models (`LLM_MODEL_TRANSCRIBE`, `LLM_MODEL_VERIFY`) at `LLM_EFFORT_LIGHT`, and are retried once on
  `LLM_MODEL` if the light model returns unusable output or declines. Measured on a real joined-handwriting sample:
  Sonnet 5.5 matched Opus 5.5 transcription (97% word agreement, identical line structure) at about half the
  time, so transcription defaults to Sonnet; for letter verification Sonnet rejected 39 of 114 letters Opus
  accepted (and Haiku let 12 bad ones through), so verification stays on Opus - it runs once per profile and
  decides final handwriting quality.
- Prompt-injection posture: uploaded text is wrapped in tags with `<`/`>` escaped, the system prompt tells the
  model to treat it as material only, and the model has **no tools** - injected text cannot cause actions.
  Everything is reviewed by the user before rendering.
- No fabricated verification: the prompt forbids invented citations and requires flags (`needs_verification`,
  `requires_current_data`, `citation_unavailable`, `ambiguous_question`, `requires_diagram`). There is no web
  research layer; reference PDFs uploaded by the user are the only retrieval source (BM25 over chunks).

## Security & privacy

- Auth: scrypt password hashes, HS256 session JWT in an httpOnly SameSite=Lax cookie (Bearer also accepted).
- Uploads: size cap while streaming, type from magic bytes (not the client), PDF validation (encrypted,
  malformed, page limit), image decompression-bomb guard, sanitised display filenames, opaque storage keys.
- Files are only served through authenticated, ownership-checked endpoints with `Cache-Control: private`.
- Logs are structured JSON and contain ids, paths and timings - never document text or answers.
- Rate limits on generation, rendering, previews, login and registration (per process).
- Deletion: profile, assignment and account deletion remove database rows and all stored files.
  Optional retention purge via `RETENTION_DAYS`.

## Observability

- **Logs** (`app/logging_setup.py`): every record carries the bound context - `request_id`, `user_id`, `job_id`,
  `job_type`, `assignment_id`, `profile_id`, `task` - so one request or job can be followed end to end (context is
  copied into worker threads). Console output is readable in development and JSON in production; rotating JSON
  files `LOG_DIR/app.jsonl` and `LOG_DIR/errors.jsonl`. A redaction filter masks sensitive fields (passwords,
  tokens, keys, document/answer/transcript text) even if passed by mistake.
- **What is logged**: every HTTP request (method, path, status, duration, user, client; 4xx as warnings, 5xx as
  errors, slow requests flagged), structured API errors, job lifecycle (queued, started with queue wait, each
  step with the previous step's duration, retry with reason and time, failure with traceback, success with
  duration and result), every AI call (task, model requested and served, tokens, latency, cost, outcome,
  escalations), extraction per page and per flagged question, handwriting per sample (lines, letters aligned /
  rejected / sliced) and profile summary, generation per answer (words, flags, fit retries), rendering
  (pages, glyphs placed, warnings, layout and PDF time), and browser errors reported by the frontend
  (`POST /client-log`: uncaught errors, unhandled rejections, 5xx and unreachable API).
- **Job timeline**: `ProcessingJob.events` keeps the steps, retries and errors of each job; the UI shows it
  under "Show steps".
- **AI usage**: `llm_usage` records each provider call; `GET /usage` aggregates by task, model, day and
  assignment with pricing in effect and the monthly budget (`LLM_MONTHLY_BUDGET_USD`, enforced before new AI
  work). Letter-verification verdicts are cached per profile so re-processing only sends new letters.
