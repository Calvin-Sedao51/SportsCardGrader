# Deploying Card Scanner

Two pieces: the FastAPI server (`server/`) and the static web app (`web/dist`). The server is stateless — no database, no volumes — so any container host works.

## 1. Deploy the server (Fly.io or Railway)

> **Request timeout:** a scan holds the request open for the whole AI pipeline.
> The server self-limits at 55 seconds, so the platform/load-balancer request
> timeout must exceed **60 seconds** — otherwise the LB cuts the scan mid-flight
> and the client sees a generic gateway error instead of the server's 504.
> (Fly.io and Railway defaults are fine; check `idle_timeout`/proxy settings if
> you front the server with your own nginx/ALB.)

The server just needs Python 3.12 and this start command:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> **Hive publishing (optional):** set `HIVE_ACCOUNT` and `HIVE_POSTING_KEY`
> (posting key only) to enable publishing to The Binder — see
> [`docs/hive-setup.md`](docs/hive-setup.md). The publish queue persists to
> `data/publish_queue/` on local disk, so give the server a persistent volume
> and run a SINGLE instance — replicas would double-post to the chain.

### Fly.io

```bash
cd server
fly launch --no-deploy        # generates fly.toml; pick a name/region
fly secrets set EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=...
fly deploy
```

If `fly launch` doesn't detect the app, use a minimal Dockerfile in `server/`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Railway

1. New project → Deploy from GitHub repo, set the root directory to `server/`.
2. Build: `pip install .` — Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Add variables `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET`.

The eBay variables are optional — without them scans work but have no market prices.

### Sign-in (optional) — Google identity for "My Collection"

Sign-in is additive: with these unset the server answers 503 on
`/api/auth/*` and the app runs as the anonymous scan-and-publish app. Design:
[`docs/plans/2026-09-16-binder-identity-v1.md`](docs/plans/2026-09-16-binder-identity-v1.md).

| Variable | Where | Meaning |
|---|---|---|
| `AUTH_GOOGLE_CLIENT_ID` | server `.env` | OAuth 2.0 **Web** client ID the app signs in with; the server rejects tokens issued for any other `aud` |
| `AUTH_JWT_SECRET` | server `.env` | HS256 secret for the 30-day app tokens. Generate: `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Rotating it signs everyone out. Never logged. |
| `AUTH_USERS_FILE` | server `.env` | user store path, default `data/users.json` — put it on the same persistent volume as the publish queue |
| `HIVE_ACCOUNT_MODE` | server `.env` | leave at `shared` (default). `per_user` is a dormant shim and refuses to start. |
| `VITE_GOOGLE_CLIENT_ID` | web build env | the **same** client ID, baked in at `npm run build`; without it the sign-in button is a documented no-op |

The server verifies Google ID tokens through Google's `tokeninfo` endpoint,
so it needs outbound HTTPS to `oauth2.googleapis.com`.

**Google / Firebase setup checklist (do this in your own consoles — nothing
here is done for you):**

1. [ ] Google Cloud console → *APIs & Services → OAuth consent screen*: create
       the app (External), add the app's domains, publish it (Testing mode
       caps you at 100 test users and expires tokens after 7 days).
2. [ ] *Credentials → Create credentials → OAuth client ID → Web application*.
       Authorized JavaScript origins: your web app origin(s), e.g.
       `https://binder.example.com` and `http://localhost:5173` for dev. No
       redirect URI is needed for Google Identity Services one-tap/popup.
3. [ ] Copy the client ID into **both** `AUTH_GOOGLE_CLIENT_ID` (server) and
       `VITE_GOOGLE_CLIENT_ID` (web build). They must match or every sign-in
       is a 401 ("issued for a different app").
4. [ ] Generate `AUTH_JWT_SECRET` (command above) and set it on the server.
5. [ ] *(Only if you want Firebase instead of raw Google Identity Services)*
       Firebase console → create project → Authentication → Sign-in method →
       enable **Google**, pick the OAuth client from step 2 as the web client
       ID. Then `npm install firebase` in `web/` and replace the export in
       `web/src/auth/firebaseStub.ts` (instructions in that file). The server
       accepts the Firebase-issued Google credential unchanged.
6. [ ] Redeploy the server, rebuild the web app, open it, tap **Sign in**,
       confirm the header shows your Google name and *Binder → My Collection*
       loads (empty is fine). `POST /api/auth/hive` should have created
       `data/users.json` with one user.
7. [ ] Hive: the shared app account still does all posting. Check
       `GET /api/hive/status` `rc_percent` after the first week of signed-in
       users; top up Hive Power if it trends below `HIVE_MIN_RC_PERCENT`
       (see `docs/hive-setup.md`).

## 2. Build and host the web app

Build with the API URL baked in:

```bash
cd web
VITE_API_URL=https://your-server.fly.dev VITE_GOOGLE_CLIENT_ID=<client id> npm run build
```

(`VITE_GOOGLE_CLIENT_ID` is optional — omit it and the app builds signed-out only.)

Then host `web/dist` on any static host — Netlify, Vercel, Cloudflare Pages, GitHub Pages. It's plain static files; no server-side rendering, no functions.

Example (Netlify CLI):

```bash
npx netlify deploy --prod --dir=dist
```

## Alternative: one service

You can skip the static host and serve the built web app from FastAPI itself. Build the web app **without** `VITE_API_URL` (same-origin), copy `web/dist` into the server image, and add to `server/app/main.py`:

```python
from fastapi.staticfiles import StaticFiles

# after all /api routes are registered:
app.mount("/", StaticFiles(directory="dist", html=True), name="web")
```

One deploy, one URL, no CORS concerns. The tradeoff: web-only changes require redeploying the server.
