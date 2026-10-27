# API overview

Base path `/api/v1`. Interactive docs at `/api/docs`; machine-readable schema in [`openapi.json`](openapi.json)
(regenerate: `cd backend && python -c "import json; from app.main import app; json.dump(app.openapi(), open('../docs/openapi.json','w'), indent=1)"`).

Errors always look like `{"error": {"code": "...", "message": "...", "details": ...}}`. Long-running work returns a
`ProcessingJob` (202); poll `GET /jobs/{id}` until `status` is `succeeded` or `failed`.

| Step | Endpoint |
|---|---|
| Account | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET/DELETE /auth/me` |
| Template sheet | `GET /profiles/template.pdf` |
| Profile | `POST /profiles`, `GET/PATCH/DELETE /profiles/{id}` |
| Samples | `POST /profiles/{id}/samples` (multipart: file, kind, transcript?, template_page?), `PATCH/DELETE .../samples/{sid}`, `GET .../samples/{sid}/image?variant=original\|corrected\|processed`, `POST .../samples/{sid}/transcribe` |
| Build profile | `POST /profiles/{id}/process` → job |
| Preview | `POST /profiles/{id}/preview` {text, style?, paper?} → PNG (+ `X-Render-Warnings`), `GET /profiles/{id}/glyphs.png` |
| Upload assignment | `POST /assignments` (multipart: file, title?, subject?) → extraction job starts |
| Review questions | `GET /assignments/{id}`, `PATCH /questions/{qid}`, `POST /assignments/{id}/questions`, `.../questions/merge`, `/questions/{qid}/split`, `.../questions/reorder`, `DELETE /questions/{qid}`, `POST .../extract`, `POST .../confirm-extraction` |
| Source pages | `GET /assignments/{id}/documents/{did}/pages/{n}.png`, `.../source.pdf`, `GET /assignments/{id}/figures?key=` |
| Preferences & references | `PATCH /assignments/{id}` {preferences, subject, profile_id}, `POST /assignments/{id}/references`, `GET /assignments/{id}/estimate` |
| Answers | `POST /assignments/{id}/generate` → job, `PUT /questions/{qid}/answer`, `POST /answers/{id}/approve\|unapprove`, `POST /answers/{id}/restore/{rev}`, `POST /assignments/{id}/answers/approve-all` |
| Render | `GET/PUT /assignments/{id}/render-config`, `POST /assignments/{id}/render` {preview} → job |
| Output | `GET /rendered/{rid}`, `GET /rendered/{rid}/pages/{n}.png`, `GET /rendered/{rid}/download`, `POST /assignments/{id}/export` {kind: digital\|review} → job, `GET /exports` |
| Status | `GET /jobs/{id}`, `GET /jobs?assignment_id=&active=`, `POST /jobs/{id}/retry`, `GET /dashboard`, `GET /health`, `GET /health/ready`, `GET /config/public` |

Answer markup accepted by the renderer: paragraphs, `- ` / `1. ` lists, pipe tables, `$inline$` and
`$$display$$` LaTeX, `**underline**`, `## subheading`, `[[figure:N]]` (embeds the question's N-th figure).
