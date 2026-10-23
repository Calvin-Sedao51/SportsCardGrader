# Browser automations (headed playwright-cli)

These scripts drive the real app in a visible browser. They cover sign-up and sign-in, grading a card, publishing it to The Binder, and the sign-in rules around publishing. They use [`playwright-cli`](https://github.com/microsoft/playwright-cli), not `@playwright/test`. Each script prints `PASS` or `FAIL`, and a failure saves a screenshot to `e2e/artifacts/`.

```bash
npm install -g @playwright/cli@latest   # once; provides `playwright-cli`
make setup                              # once; server venv + web deps
bash e2e/run-all.sh                     # everything, token sign-in, mocked grading
bash e2e/run-all.sh --google --keep     # real Google sign-in; leave the window open
bash e2e/00-up.sh --down                # stop the servers it started
```

## Scripts

| Script | What it does |
|---|---|
| `00-up.sh` | Starts the API (:8000) and web app (:5173) with test settings and waits until both respond. Anything already listening is reused. |
| `10-google-signin.sh` | **Real Google sign-in.** Opens a persistent profile, clicks "Sign in", and pauses while you finish Google's prompt. Reports sign-up or sign-in (from `created` in `/api/auth/google`) and saves the session to `.auth/session.json`. |
| `11-token-signin.sh [--fresh]` | **Sign-in without Google.** Mints an app token (`server/scripts/mint_test_session.py`), and the server creates the user. `--fresh` makes a brand-new user, which is how this path covers sign-up. Seeds the session into the browser and checks the header. |
| `20-grade-card.sh` | Fills in the AI settings, uploads `fixtures/card-front.jpg`, enters an asking price, clicks "Scan card" and checks the results. |
| `30-publish.sh` | History → "Publish to The Binder" → consent → checks the request had a Bearer token and got 2xx → waits for the **Published** chip. |
| `40-auth-guards.sh` | A signed-out publish is refused before any request · a tampered token gets 401 and the "sign-in has expired" message · My Collection asks for sign-in · Sign out clears the session. With `E2E_RATE=1`: the hourly publish limit (429). |
| `run-all.sh` | `00 → 11 --fresh (or 10) → 20 → 30 → 40` in one browser. |

All scripts share one named browser session (`cardgrader`), so you can run them one at a time in any order once `00-up.sh` is running. Drive the same window by hand with `playwright-cli -s=cardgrader snapshot | click e12 | …`.

## Test environment (set by `00-up.sh`)

- `HIVE_DRY_RUN=true` with a placeholder account. **Nothing is ever broadcast to Hive.** The worker still reads the public chain, so a network connection is needed.
  - `HIVE_MIN_RC_PERCENT=0` and `HIVE_ROOT_POST_INTERVAL_SECONDS=0` stop jobs waiting on the placeholder account.
  - Dry-run cards never reach the chain, so they **never appear in the Community feed**. The publish check uses the job's Published status instead.
- `AUTH_USERS_FILE=e2e/.auth/users.json` and `HIVE_QUEUE_DIR=e2e/.auth/queue` keep test users and jobs out of `server/data/`.
- `AUTH_JWT_SECRET` is a local test secret, and `11-token-signin.sh` mints with the same value. Anyone with this secret can sign in as any user, so never reuse it in a deployment.
- `/api/scan` is mocked in the browser with `fixtures/scan-result.json`, so grading is instant and free.

Settings go in `e2e/.env` (copy `env.example`).

## Real Google sign-in

1. You need a Google OAuth **Web** client that lists `http://localhost:5173` under Authorized JavaScript origins (see `DEPLOY.md`, Google sign-in section).
2. Set `E2E_GOOGLE_CLIENT_ID=<client id>` in `e2e/.env`, then restart: `bash e2e/00-up.sh --down && bash e2e/00-up.sh`. It is passed to the server as `AUTH_GOOGLE_CLIENT_ID` and to Vite as `VITE_GOOGLE_CLIENT_ID`.
3. Run `bash e2e/10-google-signin.sh` and finish the prompt in the window.
   - Your Google login is kept in the persistent profile, so later runs are one click.
   - To test **sign-up**, use a Google account that hasn't signed in before, or delete it from `e2e/.auth/users.json`.

Google sometimes blocks sign-in in automated browsers ("This browser or app may not be secure").
- The script uses installed Google Chrome when it finds one; installing it helps (`npx playwright install chrome`).
- If Google still blocks it, sign in with your own Chrome and drive it through `playwright-cli attach --cdp=chrome`.
- Either way, token sign-in (`11`) covers everything after the Google step.

## Live grading (costs money)

```bash
E2E_LIVE_AI=1 E2E_AI_KEY=sk-ant-... bash e2e/20-grade-card.sh
E2E_LIVE_AI=1 E2E_AI_PROVIDER=openai E2E_AI_KEY=sk-... E2E_CARD_IMAGE=~/card.jpg bash e2e/20-grade-card.sh
```

The bundled fixture is a synthetic card, so a real model may answer "Photo problem". That still counts as a complete round trip. Use `E2E_CARD_IMAGE` for a real photo. (`sample_images/*.jpg` are text placeholders, not images.)

## Rate-limit check

```bash
bash e2e/00-up.sh --down && PUBLISH_RATE_BUDGET=1 bash e2e/00-up.sh
E2E_RATE=1 bash e2e/40-auth-guards.sh
bash e2e/00-up.sh --down && bash e2e/00-up.sh     # back to the normal budget
```
