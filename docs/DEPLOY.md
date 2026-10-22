# Deploying the demo (backend on Render, frontend on Vercel)

This demo is for **sample forms only, never real patient data**. The frontend says so in a banner on every screen.

**You (the repository owner) enter every key and secret yourself**, in the Render and Vercel dashboards. They are never written to the repository, to `render.yaml` or to this page. Nobody else needs to see them.

| Variable | Where | What it is |
|---|---|---|
| `ANTHROPIC_API_KEY` | Render (backend) | your Anthropic API key. Secret |
| `CE_DEMO_ACCESS_TOKEN` | Render (backend) | the demo access code, which you make up (step 1). Secret |
| `CE_ALLOWED_ORIGINS` | Render (backend) | the Vercel URL of the frontend, e.g. `https://<project>.vercel.app`. Not secret |
| `VITE_API_URL` | Vercel (frontend) | the Render URL of the backend, e.g. `https://<service>.onrender.com`. Not secret, and it ends up in the browser bundle |

The backend reads its settings with a `CE_` prefix (see the README "Settings" table). The access code is therefore `CE_DEMO_ACCESS_TOKEN`, and the allowed origins are `CE_ALLOWED_ORIGINS`. `ANTHROPIC_API_KEY` has no prefix: the Anthropic SDK reads that name.

## 0. Before you start

- The repository is pushed to GitHub (or GitLab / Bitbucket), and you can log in to Render and Vercel with that account.
- In the [Anthropic console](https://console.anthropic.com), set a monthly spend limit on the key you will use. Every uploaded page costs several model calls.

## 1. Choose the access code

Make up a long random code, at least 16 characters. The backend refuses to start with a shorter one. For example, run this locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Keep it somewhere safe, such as a password manager. You will paste it into Render in step 2 and give it to the demo's users separately from the link.

## 2. Backend on Render (Blueprint)

1. In the Render dashboard: **New → Blueprint**, then pick the repository. Render finds `render.yaml` at the repository root and shows one web service, `commitments-extractor-api` (Docker, free plan, health check `/health`).
2. Render asks for the three values marked `sync: false`:
   - `ANTHROPIC_API_KEY`: paste your key.
   - `CE_DEMO_ACCESS_TOKEN`: paste the code from step 1.
   - `CE_ALLOWED_ORIGINS`: **leave it empty for now**. The Vercel URL is not known yet (step 4). While it is empty, no browser page can call the backend.
3. Click **Apply** (or **Deploy Blueprint**). The first build installs Tesseract and the Python packages and takes several minutes.
4. When the service is live, copy its URL from the top of the service page: `https://<service>.onrender.com`.
5. Check it:
   - `https://<service>.onrender.com/health` in a browser should show `{"status":"ok"}`.
   - `https://<service>.onrender.com/senders` should show `{"detail":"access token missing or invalid"}` (401). That means the access check is on.

To change a value later: open the service → **Environment** → edit the variable → **Save Changes**. Render redeploys with the new value. The other settings in `render.yaml` (limits, TTLs, `CE_STORAGE=memory`) can be changed on the same tab.

`CE_REQUIRE_ACCESS_TOKEN=true` is set in `render.yaml`. If `CE_DEMO_ACCESS_TOKEN` is missing, the service fails at startup instead of running open. The deploy log then names the missing variable (never its value).

*Without a Blueprint:* **New → Web Service** → the repository → **Language: Docker**, **Root Directory** `backend`, **Health Check Path** `/health` (under Advanced). Then add every variable from `render.yaml` by hand on the **Environment** tab.

## 3. Frontend on Vercel

1. In the Vercel dashboard: **Add New… → Project** → import the same repository.
2. **Root Directory**: `frontend`. The framework is Vite, and `frontend/vercel.json` already sets the build command, the output directory and the single-page fallback.
3. Open **Environment Variables** on the same screen and add `VITE_API_URL`. Its value is the Render URL from step 2.4, with no trailing slash and no `/api`.
4. Click **Deploy**. Without `VITE_API_URL` the build fails on purpose with "VITE_API_URL must be set…". The frontend calls the backend directly, the progress stream included; no Vercel rewrite or proxy is involved.
5. Copy the production URL: `https://<project>.vercel.app`.

`VITE_API_URL` is read at build time. After changing it (**Settings → Environment Variables**), redeploy: **Deployments** → the latest one → **⋯ → Redeploy**.

## 4. Allow the frontend on the backend

Back on Render: the service → **Environment** → set `CE_ALLOWED_ORIGINS` to the Vercel URL from step 3.5, for example `https://<project>.vercel.app`.

- Use exactly the scheme and host: no path and no trailing slash. The backend refuses `*`, paths and trailing slashes at startup.
- Several origins are comma-separated. Vercel preview deployments have their own URLs and stay blocked unless you add them.

Save; Render redeploys.

## 5. Try it

Open the Vercel URL. First you see the banner and the access screen. Enter the code, then upload a sample form. The code is kept in the tab's `sessionStorage` only: it is gone when the tab closes, and a wrong or changed code brings the access screen back.

To revoke access, change `CE_DEMO_ACCESS_TOKEN` on Render. Everyone who has the old code is asked again.

## Things to know

### Cold start
On the free plan, Render stops the service after about 15 minutes without incoming requests. The next request starts it again, which takes about a minute (longer right after a deploy). Meanwhile the access screen shows "מתחבר לשרת… בהפעלה ראשונה זה עשוי להימשך עד דקה". If the browser gives up first, it shows a retry button. A paid instance type does not sleep.

### Memory (512 MB on the free plan)
Every page of an upload is held in memory as several image copies while the job runs: the full-resolution original, the copy for the model and the OCR copy. A rough estimate, not a measurement: an A4 scan at 200–300 DPI takes 25–45 MB. That is why `render.yaml` keeps the demo small: `CE_MAX_PAGES=6` and `CE_MAX_JOB_PAGES=6` pages per upload, `CE_MAX_FILES=3`, `CE_MAX_UPLOAD_MB=10`, `CE_MAX_IMAGE_PIXELS=12000000` and `CE_MAX_CONCURRENT_JOBS=1`. If the instance runs out of memory, Render restarts it (the **Events** tab shows it) and the running job is lost. Lower the limits, or move to an instance type with more memory before raising them.

### Temporary storage, deleted on restart
- `CE_STORAGE=memory`: jobs and their extracted fields live in the process memory only. A restart loses them, and so does a deploy, a sleep (cold start) or an out-of-memory restart. No database is used.
- Uploaded files and page images are on the container's disk under `/tmp/commitments-extractor`. They are deleted at startup and at shutdown, and Render's disk is wiped on every restart anyway.
- While the service runs, jobs and files are deleted 30 minutes after creation (`CE_JOB_TTL_S`, `CE_FILE_TTL_S`). A background purge checks every 2 minutes (`CE_PURGE_INTERVAL_S`). Download the Excel file before then.
- "מחיקת העבודה מהשרת" in the UI deletes a job at once.

### Limits and abuse protection
- Every endpoint except `/health` needs the access code. One code is shared by everyone; there are no per-user accounts.
- Per client IP: `CE_RATE_LIMIT_PER_MINUTE=120` requests per minute and `CE_UPLOAD_LIMIT_PER_HOUR=10` uploads per hour. Above that the answer is 429, and the UI shows "יותר מדי בקשות". The counters are in memory and reset on a restart.
- The client IP comes from `X-Forwarded-For` (`CE_TRUSTED_PROXY_HOPS=1`, the address Render's proxy appended). If all users seem to share one limit, or the limit never triggers, this assumption about Render's proxy is wrong. Check the header and adjust the value.
- The progress stream sends the code in the URL (`?access_token=`), because the browser's EventSource cannot send headers. The backend masks it in its own logs. Render's request logs may still record full URLs.

### Optional: keep jobs in Postgres
For a demo that keeps jobs across restarts: create a Render Postgres in the same region, set `DATABASE_URL` on the service to its **Internal Database URL**, and remove `CE_STORAGE` (or set it to `postgres`). The extracted fields then stay in the database until `CE_JOB_TTL_S`; uploaded files and page images are still deleted on restart. The URL holds the password, so never share it.

## Local check of the demo set-up

```bash
cd frontend && PLAYWRIGHT_CHANNEL=chrome npm run e2e:demo
```

This builds the frontend with `VITE_API_URL` and serves it on a different origin from a fake-provider backend with an access code, CORS and rate limits on. It then checks the banner, a wrong and a right code, an upload with the progress stream, and that the code stays in `sessionStorage`. It uses a dummy code and dummy data only.
