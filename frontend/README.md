# AssignmentHand web frontend

Next.js (App Router, TypeScript, Tailwind CSS v4). All API calls go to the same origin
(`/api/v1/...`) and are proxied to the FastAPI backend by a rewrite in `next.config.ts`.
Auth is the backend's httpOnly `ah_session` cookie; nothing is stored in localStorage.

## Run locally

```bash
# backend on :8000 first (see ../backend), then:
npm install
npm run dev            # http://localhost:3000
```

Environment (read when the dev server starts / at build time):

| Variable        | Default                 | Purpose                                                        |
| --------------- | ----------------------- | -------------------------------------------------------------- |
| `BACKEND_URL`   | `http://localhost:8000` | Where `/api/*` is proxied to. Baked in at build time.          |
| `MAX_UPLOAD_MB` | `25`                    | Match the backend limit; the proxy buffers bodies up to +5 MB. |

## Scripts

- `npm run lint`, `npm run typecheck`, `npm run build`
- `npm run gen:api` regenerates `src/lib/api-schema.d.ts` from `../docs/openapi.json`

## Docker

```bash
docker build --build-arg BACKEND_URL=http://backend:8000 -t assignmenthand-web .
docker run -p 3000:3000 assignmenthand-web
```

Rewrites are resolved at build time, so rebuild the image to change `BACKEND_URL`.

## Layout

- `src/lib/api.ts` typed client (errors normalised to `ApiError`, 401 redirects to `/login`)
- `src/lib/types.ts` API types (generated schema plus concrete shapes for free-form fields)
- `src/lib/hooks.ts` `useResource`, `useJob` (polls `/jobs/{id}`), `useDebounced`
- `src/components/workspace/*` the seven-step assignment workspace
- `src/components/profiles/*` handwriting profile workspace
