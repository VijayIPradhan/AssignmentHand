# Deployment

## Single server with Docker Compose

1. Install Docker Engine + Compose plugin on a Linux host.
2. `cp .env.example .env` and set at least:
   - `SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `POSTGRES_PASSWORD`
   - `APP_ENV=production`, `COOKIE_SECURE=true` (behind HTTPS)
   - AI provider (`LLM_PROVIDER`, `LLM_MODEL` and its credentials), or `LLM_PROVIDER=none`
3. `docker compose up -d --build`
4. Put a TLS reverse proxy (Caddy, nginx, Traefik) in front of `web:3000`. Only the web service needs to be
   public; `api`, `worker` and `db` stay on the internal network.

The API container runs `alembic upgrade head` before starting. Health checks:
`GET /api/v1/health` (liveness) and `GET /api/v1/health/ready` (database, storage, AI and OCR status).

## Configuration notes

- **Bedrock**: set `LLM_PROVIDER=bedrock`, `AWS_REGION`, `LLM_MODEL` to an inference-profile id available in that
  region (e.g. `global.anthropic.claude-opus-5-5`), and either `AWS_BEARER_TOKEN_BEDROCK` (Bedrock API key) or
  standard AWS credentials/role. Bedrock pricing differs from Anthropic list prices, so set
  `LLM_INPUT_PRICE_PER_MTOK` / `LLM_OUTPUT_PRICE_PER_MTOK` if you want cost estimates.
- **OCR**: the image includes Tesseract. Without it, scanned pages are transcribed by the AI model (if
  configured) or flagged for manual entry.
- **Fonts**: the image ships DejaVu (Unicode fallback). To use a handwriting-style fallback for characters
  missing from profiles, mount a font that permits embedding and set `FALLBACK_FONT_PATH`.
- **Secrets** come only from the environment; never bake them into images or commit `.env`.

## Backups

- Database: `docker compose exec db pg_dump -U assignmenthand assignmenthand > backup.sql`
- Files: back up the `filedata` volume (uploaded samples, PDFs, glyph libraries, exports).
Both contain private user data - encrypt backups and restrict access.

## Scaling

- Run more workers: `docker compose up -d --scale worker=3` (job claiming is safe on PostgreSQL).
- API replicas behind a load balancer need shared storage (implement the `Storage` interface for S3 or mount a
  shared volume) and, if strict limits matter, a shared rate-limit store (current limiter is per process).
- No GPU is required. Rendering a 10-page document takes a few seconds of CPU; AI generation dominates latency.

## Without Docker

Python 3.12+, PostgreSQL 14+, Node 20+. Install `tesseract-ocr` and a Unicode font, then:

```bash
cd backend && pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
python -m app.jobs.worker          # separate process (systemd service)
cd frontend && npm ci && npm run build && BACKEND_URL=http://127.0.0.1:8000 npm start
```
