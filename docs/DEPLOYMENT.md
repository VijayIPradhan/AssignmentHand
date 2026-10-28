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

## Logs

In Docker the app writes no log files: everything goes to stdout as one JSON object per line. Read it with
`docker compose logs -f api worker`, or in Dokploy's **Logs** tab. (Outside Docker, `LOG_DIR` keeps rotating JSON
files; leave it empty to disable them.)

## Backups

- Database: `docker compose exec db pg_dump -U assignmenthand assignmenthand > backup.sql`
- Files: back up the `filedata` volume (uploaded samples, PDFs, glyph libraries, exports), or with R2/S3
  enable bucket versioning or a scheduled copy of the bucket.
Both contain private user data - encrypt backups and restrict access.

## Scaling

- Run more workers: `docker compose up -d --scale worker=3` (job claiming is safe on PostgreSQL). Each worker
  runs `WORKER_CONCURRENCY` jobs at once (default 2; always 1 on SQLite).
- API: `WEB_CONCURRENCY` processes per container, and any number of containers behind a load balancer. Rate
  limits (including login attempts) are stored in the database, so they hold across all of them.
- Several servers need shared file storage: use Cloudflare R2 (or another S3-compatible store), see below.
- No GPU is required. Rendering a 10-page document takes a few seconds of CPU; AI generation dominates latency.

### Cloudflare R2

1. In the Cloudflare dashboard: R2 → create a bucket (keep it private; no public access or custom domain is
   needed, all downloads go through the API).
2. R2 → Manage API tokens → create a token with **Object Read & Write** for that bucket only. Note the Access Key
   ID, Secret Access Key and your account ID.
3. Set `STORAGE_BACKEND=r2`, `S3_BUCKET`, `R2_ACCOUNT_ID`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (EU
   jurisdiction buckets: set `S3_ENDPOINT_URL=https://<account_id>.eu.r2.cloudflarestorage.com` instead of the
   account ID). The `filedata` volume is then no longer needed for files.
4. Moving an existing install: stop the app, then copy the files once (re-runnable; it skips what is already
   there): `docker compose run --rm worker python -m app.storage.copy --from /data/storage` (add `--dry-run` to
   count first). Start the app; `GET /api/v1/health/ready` reports `storage: ok` when the bucket is reachable.

## Moving an existing install to a server (e.g. Dokploy)

Files live in the storage (R2), so a server using the same bucket already has them. The database moves once:

1. Upload a snapshot of the old database to the bucket (from the old machine, with the app stopped):
   `python -c "import sqlite3; s=sqlite3.connect('data/app.db'); d=sqlite3.connect('snap.db'); s.backup(d)"`, then
   put `snap.db` at `migration/app.db` in the bucket (any S3 tool, or `get_storage().put(...)`).
2. Dokploy: create a **Compose** service from this repository (compose file `docker-compose.yml`), paste the
   settings into its **Environment** tab, and add a domain on service `web`, port `3000` (HTTPS on).
3. Deploy. The API applies the schema to the new PostgreSQL on start.
4. In Dokploy, open a terminal in the `api` container and import:
   `python -m app.db_copy --from-storage migration/app.db`. It refuses to run if the database already has users
   (`--replace` empties it first) and checks every table's row count afterwards.
5. Log in with the same email and password, then delete `migration/app.db` from the bucket.

## Without Docker

Python 3.12+, PostgreSQL 14+, Node 20+. Install `tesseract-ocr` and a Unicode font, then:

```bash
cd backend && pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
python -m app.jobs.worker          # separate process (systemd service)
cd frontend && npm ci && npm run build && BACKEND_URL=http://127.0.0.1:8000 npm start
```
