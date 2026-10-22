# Deploying the demo (backend on Render, frontend on Vercel)

This page lists where each setting goes. It contains no values: take them from your Render and Vercel dashboards and never commit them.

## 1. Postgres on Render

1. In the Render dashboard: **New → Postgres**. Pick the same region as the backend service.
2. When the database is ready, open it and copy the **Internal Database URL** from its **Connections** section (`postgresql://…`). Use the internal URL: it only works from services in the same Render region, and its traffic does not leave Render's network. The *External* URL is only for connecting from your own machine.

The database holds patient data (the extracted fields). Do not share the URL. Anyone who has it can read every job.

## 2. Backend web service on Render

Create a **Web Service** from the repository with **Root Directory** `backend` and runtime **Docker**. The image listens on `$PORT`, which Render sets itself.

Under the service's **Environment** tab, add these variables:

| Key | Where the value comes from |
|---|---|
| `DATABASE_URL` | the **Internal Database URL** from step 1. Paste it as is: it holds the password, and the app never logs it. |
| `CE_PROVIDER` | `claude` or `gemini` |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | your provider key (only the one for `CE_PROVIDER`) |
| `CE_ALLOWED_ORIGINS` | the Vercel URL of the frontend (step 3), e.g. `https://<project>.vercel.app`; comma-separate several |

Leave `CE_STORAGE` unset: when `DATABASE_URL` is set the backend uses Postgres. Setting `CE_STORAGE` overrides this choice (for example `memory` for a throwaway demo).

Optional: `CE_JOB_TTL_S` (how long jobs stay in the database, default one hour), `CE_FILE_TTL_S` (how long uploads and page images stay on the container disk), `CE_DB_POOL_SIZE`, `CE_DB_TIMEOUT_S`. All settings are listed in the README "Settings" table.

At startup the backend creates or migrates its tables (the version is recorded in `schema_version`). This is safe to repeat and needs no manual step. To check the service, open `GET /health`.

### What survives a restart or redeploy

- **Jobs and their extracted data** are kept: they are in Postgres. This includes fields, flags, corrections and times.
- **Uploaded files and page images** are lost. They live on the container's disk, which Render wipes on every restart and deploy. The crop endpoint then answers 404 `image_gone`, and the UI shows "התמונה כבר לא זמינה". The Excel export still works, because it is built from the database.
- A job that was still processing during a restart is marked `failed` (`Interrupted`). Upload the file again.

## 3. Frontend on Vercel

Import the repository with **Root Directory** `frontend`. `frontend/vercel.json` already sets the build. Under **Settings → Environment Variables**, add:

| Key | Value from |
|---|---|
| `VITE_API_URL` | the backend's Render URL (`https://<service>.onrender.com`), without `/api` |

`VITE_API_URL` is read at build time, so redeploy the frontend after changing it. Then make sure the Vercel URL is listed in the backend's `CE_ALLOWED_ORIGINS`.

## Local check before deploying

Point a local backend at any Postgres you control:

```bash
cd backend
DATABASE_URL=<local postgres URL> ../.venv/Scripts/uvicorn app.main:app --reload
TEST_DATABASE_URL=<local postgres URL> ../.venv/Scripts/python -m pytest   # every test schema is created and dropped
```
