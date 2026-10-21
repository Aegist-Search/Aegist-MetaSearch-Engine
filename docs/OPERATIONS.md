# Operations

How to configure, start, crawl, test and troubleshoot an Aegis Search deployment.

---

## 1. Requirements

- **Docker** with Docker Compose v2 (Meilisearch, Tor, backend).
- **Node.js 20+** and npm (frontend).
- **Python 3.12+** only to run the backend or the ingestion outside Docker; dependencies are in `backend/requirements.txt`.
- **Katana** for crawls: `go install github.com/projectdiscovery/katana/cmd/katana@latest` (Go 1.21+) or the `projectdiscovery/katana:latest` image. The backend image already ships Katana for on-demand crawls.
- At least one search provider key (the local index works without one): [Brave](https://api.search.brave.com/app/keys), [Exa](https://dashboard.exa.ai/api-keys), [Serper](https://serper.dev), [Tavily](https://app.tavily.com).

### Windows 11 command equivalents

POSIX commands work in **Git Bash**. From **PowerShell**:

| POSIX | PowerShell |
| --- | --- |
| `cp .env.example .env` | `Copy-Item .env.example .env` |
| `curl URL` | `curl.exe URL` (plain `curl` is an alias for `Invoke-WebRequest`) |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `npm ...` | `npm.cmd ...` if PowerShell blocks `npm.ps1` |

If the execution policy blocks venv activation or local scripts:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Make sure **Docker Desktop** is running before `docker compose`.

---

## 2. Configure

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

All three at once and without overwriting existing files, on any platform:

```bat
scripts\setup.bat
```

```bash
./scripts/setup.sh
```

Set at least `MEILI_MASTER_KEY` (16+ characters) and one provider key in `.env`; `frontend/.env.local` only needs `SEARCH_API_URL`. The complete reference, including every provider, crawl, proxy and Tor variable, is in [CONFIGURATION.md](CONFIGURATION.md).

---

## 3. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

The first start also builds the `tor` image from Debian; the container exposes SOCKS5 only on the Compose network, without publishing port `9050` on the host.

Check that the services are ready:

```powershell
curl.exe http://127.0.0.1:8000/api/v1/health
```

```json
{
  "status": "ok",
  "meilisearch": "up",
  "brave": "configured",
  "fallbacks": { "exa": "configured", "serper": "disabled", "tavily": "disabled" },
  "index": "pages",
  "version": "1.0.0"
}
```

- Meilisearch: <http://127.0.0.1:7700> (loopback only).
- Backend: <http://127.0.0.1:8000>, OpenAPI at <http://127.0.0.1:8000/docs>.
- Logs: `docker compose logs -f backend` and `docker compose logs -f tor`.

Meilisearch and backend ports are published on `127.0.0.1`, not on every interface; the Tor proxy is not published at all, so the stack is not reachable from the local network.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. For a production build use `npm run build` then `npm run start`.

### Helper scripts (.bat / .sh)

The `scripts/` folder ships two flavours of the same four helpers: `.bat` for
Windows and `.sh` for Linux/macOS. Nothing else is required — no PowerShell, no
ExecutionPolicy, no extra runtime.

| Task | Windows | Linux / macOS |
| --- | --- | --- |
| Create `.env` files and `data/` | `scripts\setup.bat` | `./scripts/setup.sh` |
| Start stack + frontend | `scripts\start.bat` | `./scripts/start.sh` |
| Stop frontend + stack | `scripts\stop.bat` | `./scripts/stop.sh` |
| Crawl (and optionally ingest) | `scripts\crawl.bat -Url …` | `./scripts/crawl.sh --url …` |

What `start` does on both platforms:

- creates `data/` and `logs/` if missing;
- runs `docker compose up -d --build` (Meilisearch + Tor + FastAPI, detached);
- waits for `/api/v1/health`;
- starts `npm run dev` on `http://127.0.0.1:3000` in the background, with output in `logs/frontend.out.log` and `logs/frontend.err.log`;
- waits for the frontend port, then prints the URLs.

The frontend is recorded differently per platform: `start.sh` writes the PID to
`logs/frontend.pid`, while `start.bat` opens a minimised window titled
`Aegis frontend`. Both `stop` scripts are idempotent and only end a `node`
process listening on the chosen port, so they never kill an unrelated service.

Options:

| Windows | Linux / macOS | Effect |
| --- | --- | --- |
| `-Port 3001` | `--port 3001` | frontend port (default `3000`) |
| `-Timeout 180` | `--timeout 180` | seconds to wait for backend and frontend (default `120`) |
| `-NoBuild` | `--no-build` | do not rebuild the backend image |
| `-Stop` | `--stop` | stop the frontend and exit (same as `stop`) |

```bat
:: skip the image rebuild, different port
scripts\start.bat -NoBuild -Port 3001
```

```bash
# skip the image rebuild, different port
./scripts/start.sh --no-build --port 3001
```

Restarting the backend rebuilds the image only without `-NoBuild`/`--no-build`;
crawl data stays in `data/` and the index in `meili_data`.

### Stop

`stop` ends the frontend started by `start` (PID file on POSIX, window title and
listening port on Windows) and then runs `docker compose stop`. With nothing
running it prints a warning and exits cleanly.

| Windows | Linux / macOS | Effect |
| --- | --- | --- |
| `-Port 3001` | `--port 3001` | port used for the fallback on the `node` process |
| `-Down` | `--down` | `docker compose down`: removes containers and network, keeps the `meili_data` volume |
| `-Volumes` | `--volumes` | `docker compose down --volumes`: also deletes the index, which must be rebuilt with a new ingestion |

---

## 4. Crawl

### 4.1 Katana on the host

Katana includes the response body in the JSONL by default: **do not use `-omit-body`**, otherwise the pipeline cannot extract title, `h1` and snippet.

```powershell
katana -u https://example.com -d 3 -jc -kf all -rl 20 -timeout 10 -retry 2 -jsonl -o data/katana.jsonl
```

The command is on a single line so it also works in PowerShell (no `\` continuation).

- `-d 3` limits depth, `-rl 20` limits requests per second: keep the crawl polite.
- Only crawl sites you are authorised to crawl, and respect `robots.txt` and the terms of use.
- For a quick test, use `--limit` at ingestion time instead of a huge crawl.
- The file lands in `data/`, mounted read-write in the backend at `/data`.

### 4.2 Katana with Docker

The official image avoids installing Go, but it must run **from the project root** with the right volume; otherwise the crawl stays inside the temporary container and disappears with `--rm`.

The mount must point at `data/`, not at the project root: with `-v "%cd%:/data"` the root ends up at `/data`, so `-o /data/katana.jsonl` writes `katana.jsonl` in the project root and the backend (which mounts `./data`) never sees it. `%cd%` **only works in `cmd.exe`**; in PowerShell and Git Bash the text stays literal and Docker mounts nothing.

```powershell
New-Item -ItemType Directory -Force data | Out-Null
```

**cmd.exe**

```bat
docker run --rm -v "%cd%/data:/data" projectdiscovery/katana:latest -u https://example.com -d 1 -jsonl -o /data/katana.jsonl
```

**PowerShell**

```powershell
docker run --rm -v "${PWD}/data:/data" projectdiscovery/katana:latest -u https://example.com -d 1 -jsonl -o /data/katana.jsonl
```

**Git Bash / macOS / Linux**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD/data:/data" projectdiscovery/katana:latest -u https://example.com -d 1 -jsonl -o /data/katana.jsonl
```

- `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data/katana.jsonl` into a Windows path; alternatively write `-o //data/katana.jsonl`.
- The file must appear in `data/katana.jsonl`. If it appears in the project root, the mount pointed at the root: move it with `mv katana.jsonl data/`.
- `example.com` has a single page, so it is only a smoke test; use `-d 3 -rl 20` for a real crawl.
- `data/` is writable by the backend: on Linux the container runs as uid `10001`, so run `sudo chown -R 10001:10001 data`.

### 4.3 Crawl and ingestion in one command

`scripts/crawl.bat` and `scripts/crawl.sh` wrap the Docker command: they mount `data/`, run Katana, validate the JSONL and, with `-Ingest`/`--ingest`, run the ingestion pipeline immediately.

```bat
:: crawl only, file in data\katana.jsonl
scripts\crawl.bat -Url https://developer.mozilla.org -Depth 2

:: crawl + full index ingestion
scripts\crawl.bat -Url https://developer.mozilla.org -Depth 2 -Ingest -Replace
```

```bash
# crawl only, file in data/katana.jsonl
./scripts/crawl.sh --url https://developer.mozilla.org --depth 2

# crawl + full index ingestion
./scripts/crawl.sh --url https://developer.mozilla.org --depth 2 --ingest --replace
```

| Parameter | Default | Notes |
| --- | --- | --- |
| `-Url` | required | must start with `http://` or `https://` |
| `-Depth` | `3` | `katana -d` depth |
| `-RateLimit` | `20` | `katana -rl` requests per second |
| `-Timeout` | `10` | seconds per request |
| `-Retry` | `2` | attempts per request |
| `-Output` | `data/katana.jsonl` | must stay inside `data/` |
| `-Headless` | off | `-system-chrome -headless` for JavaScript sites |
| `-KatanaArgs` | empty | extra arguments, e.g. `-KatanaArgs "-jc","-kf","all"` |
| `-Ingest` | off | runs `python -m app.ingestion.cli` in the backend |
| `-Replace` | off | with `-Ingest`, passes `--replace` |

The script deletes the previous output file, checks that the crawl is not empty and that the first record contains `response.body`: without a body it warns that title and text cannot be extracted (which happens with `-omit-body`).

### 4.4 On-demand crawl from the backend

The backend can crawl a site by itself without slowing the search down. The logic lives in `backend/app/services/crawler_service.py`:

1. `/api/v1/search` responds normally (local index + external shield);
2. if the query is an `http(s)` URL or `meta.local_hits == 0`, it tries to **reserve** a crawl for that domain: in-memory checks only;
3. if the reservation succeeds, `BackgroundTasks` runs `katana -u <url> -d <depth> -jsonl -o <file>` with `asyncio.create_subprocess_exec` **after** the response;
4. when it finishes, the same file goes through `app/ingestion/pipeline.py`, the pipeline used by the CLI: no duplicated logic;
5. documents land in Meilisearch and are available from the next search.

Guardrails:

- a global per-process token bucket limits crawl **starts** (`CRAWL_MAX_STARTS_PER_MINUTE`, burst `CRAWL_START_BURST`); above the limit the crawl is skipped with `rate_limited`, never queued;
- `CRAWL_MAX_CONCURRENCY` concurrent crawls per process; when the server is busy the crawl is **skipped**;
- one crawl per domain at a time, with `CRAWL_DOMAIN_COOLDOWN_S` after a success and `CRAWL_FAILURE_COOLDOWN_S` after a failure;
- only `http(s)` URLs resolving to public addresses: loopback, private ranges, link-local and `169.254.169.254` are rejected (same guard as `/api/v1/preview`, in `app/utils/net.py`); the check runs after the response;
- `CRAWL_TIMEOUT_S` and `CRAWL_INGEST_TIMEOUT_S` terminate the process or the upload when they expire;
- only the first results page is a seed: paginating never starts a crawl;
- `engine=local` never crawls: it is an explicit request to stay offline;
- JSONL files are unique per run (`dynamic_crawl-<domain>-<token>.jsonl`) and deleted after ingestion unless `CRAWL_KEEP_OUTPUTS=true`.

If a free-text query finds an empty index, the seed is the **first external result**. If Katana is missing, the crawler disables itself with a warning and search keeps working.

Because the crawl is a server process, the visited site sees the server IP: keep depth and rate low and crawl only sites you are entitled to crawl.

---

## 5. Ingest into the index

```bash
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl
```

The first run creates the `pages` index, applies the schema (searchable, filterable, sortable attributes) and uploads documents in batches of 200.

```bash
# replace the whole index (periodic refresh)
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --replace

# preview without contacting Meilisearch
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --dry-run --limit 5

# only the first 100 documents, smaller batches
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --limit 100 --batch-size 50

# also include pages without a body (title derived from the URL)
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --allow-empty-body

# ignore the stored hashes and upload every document again
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --no-skip-unchanged

# tune the near-duplicate filter for one run
# (0.95 = stricter, --no-dedupe = keep every variant)
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --dedupe-threshold 0.95
```

A refresh run prints how many documents were prepared, skipped as unchanged, dropped as duplicates and actually uploaded:

```text
records read | documents prepared | unchanged | skipped | duplicates | near-duplicates | elapsed
```

The first run after upgrading to incremental ingestion uploads every page once, because the documents stored before the change have no `content_hash`. No `--replace` is needed.

### Measuring the ingestion effect

`backend/scripts/measure_ingestion.py` runs the **real** pipeline twice against an in-memory Meilisearch stand-in: nothing is written, no container is required, and the counters come from `IngestReport`, not from a reimplementation.

```bash
# from backend/ with the virtualenv active
python scripts/measure_ingestion.py --input ../data/katana.jsonl

# real refresh scenario: seed the store with an older crawl, then measure the new one
python scripts/measure_ingestion.py --previous ../data/katana_old.jsonl --input ../data/katana.jsonl

# near-duplicate pairs with their similarity, plus a read-only check of the live index
python scripts/measure_ingestion.py --input ../data/katana.jsonl --show-dropped 10 --meili

# inside Docker (the image copies backend/scripts)
docker compose exec backend python scripts/measure_ingestion.py --input /data/katana.jsonl
```

The output has three parts: one block per pass (records read, documents prepared, near-duplicates dropped with their share, uploaded payload, unchanged), then *Incremental effect* (how many documents the second pass would re-upload and how much payload it avoids) and *Near-duplicate effect* (documents removed and their percentage). `--previous` is the measurement that answers "how much would a new crawl of this site upload?": the seed pass fills the store, so the second pass sees real hashes.

The same cases are covered by `backend/tests/test_dedup_pagination.py`, a crawl with `?page=1`, `?page=2`, a print view, a tracking-parameter URL and an unrelated article: only the two genuinely different pages survive.

Two caveats: the filter works per batch (`--batch-size`, default 200), and a second pass over the *same* file is expected to report 100% unchanged — it is a self-check of the mechanism, not a refresh measurement.

Outside Docker, from `backend/` with Meilisearch on `localhost:7700`:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.ingestion.cli --input ../data/katana.jsonl
```

Stop the backend container first to avoid a conflict on port 8000: `docker compose stop backend`.

### What the pipeline extracts

For every Katana record with a `2xx` status and HTML content:

| Field | Source |
| --- | --- |
| `title` | `og:title` → `<title>` → first `h1` → URL slug |
| `h1` | first `<h1>` |
| `image` | `og:image` / `twitter:image`, made absolute; `http(s)` only |
| `snippet` | meta description (if ≥ 40 characters) or the start of the text |
| `content` | clean text (script/style/noscript removed), max 20,000 characters |
| `url` | canonical URL: lowercase host, fragment and tracking parameters removed |
| `domain` | hostname without `www.` |
| `lang` | `lang` attribute of the `<html>` element |
| `id` | SHA-1 of the canonical URL (stable primary key) |
| `crawled_at` | crawl timestamp |
| `content_hash` | SHA-1 of the indexed fields, compared by `--skip-unchanged` |

Duplicates are overwritten thanks to the deterministic primary key, so a new crawl updates pages already indexed.

The schema also includes Italian `stopWords` and bidirectional `synonyms` (`auto`/`automobile`, `pc`/`computer`, …). The CLI reapplies `INDEX_SETTINGS` on every run, so after an update you just run ingestion again (even without `--replace`). Stop words cover function words only: `non`, `più`, `come` and `quando` stay searchable because they change a query's meaning.

### Previews

Every local result shows a thumbnail; clicking it opens a panel with the image, title, domain, excerpt and a link to the site. The `image` field comes from `og:image` (or `twitter:image`), made absolute during ingestion, and is served through `/api/v1/preview` (see [API.md](API.md#get-apiv1preview)).

Because `image` must be in the index, adding previews to an existing index requires a reindex:

```bash
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --replace
```

Without `--replace` existing documents are updated by primary key, but the field stays empty until they are reindexed.

---

## 6. Tests and checks

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

On macOS / Linux / Git Bash use `source .venv/bin/activate`.

The suite covers URL normalization, JSONL/JSON parsing, field extraction, status/content-type filters, the fallback threshold, merge and deduplication, partial degradation, pagination, Brave parameters and extra snippets, parallel fan-out with the provider limit, enriched merge and insights, engine selection, space reserved for external results, available providers, provider cache and circuit breaker, Exa SDK parameters, Serper/Tavily requests, responses and reason codes, proxy parsing/round-robin/quarantine/fallback and Exa client injection, the on-demand crawl (reservation, private hosts, concurrency, cooldowns, timeout, ingestion and cleanup, seed selection), the crawl and HTTP rate limiters (including the absence of address logs), the spelling helpers behind "did you mean", and the Tor diagnostics.

Frontend:

```powershell
cd frontend
npm install
npm run typecheck
npm run build
```

If PowerShell blocks `npm.ps1`, use `npm.cmd install` and `npm.cmd run typecheck`. If `NODE_ENV=production` is exported, force devDependencies with `npm install --include=dev`.

Coverage gate on the module with the most logic (the CI workflow fails below 80%):

```bash
cd backend
pytest --cov=app.services.search --cov-report=term-missing --cov-fail-under=80
```

### End-to-end tests (Playwright)

The suite runs against the real stack: it starts Meilisearch, Tor and the backend,
indexes a Katana fixture and drives the Next.js frontend in Chromium.

```bash
cd frontend
npm install
npx playwright install chromium     # once per machine
npm run test:e2e
```

What the run does:

1. `e2e/global-setup.ts` runs `docker compose up -d --build` from the repository root
   and waits until `/api/v1/health` reports Meilisearch `up`.
2. It copies `frontend/e2e/fixtures/katana.jsonl` to `data/e2e-katana.jsonl` and
   ingests it with the real CLI (`--no-dedupe`) into the `pages` index. The fixture
   uses the domain `e2e.example` and the word `aegisfixture`, so the existing index
   is **not** wiped and the specs filter themselves.
3. Playwright boots `next dev` on port `3100`, bound to the same host the browser
   opens (`--hostname 127.0.0.1` by default): Next.js only serves dev assets to
   `localhost` and to the hostname it was started on, otherwise the chunks are
   rejected and the server-rendered page stays inert. It then runs three specs:
   local results with highlighting, pagination that keeps query and filters,
   preview panel that opens from a card and closes with `Esc`.

| Variable | Default | Effect |
| --- | --- | --- |
| `E2E_SKIP_STACK` | off | `1` reuses an already running Compose stack (no `up --build`) |
| `E2E_PORT` | `3100` | Port for the Playwright-managed frontend |
| `E2E_BASE_URL` | `http://127.0.0.1:3100` | Frontend URL the browser opens; when set, Playwright does not start its own dev server |
| `E2E_API_URL` | `http://127.0.0.1:8000` | Backend used for the readiness check |
| `SEARCH_API_URL` | `http://127.0.0.1:8000` | Passed to the frontend dev server |

The same job exists in `.github/workflows/ci.yml` but only runs on
`workflow_dispatch`: it builds the images and needs a browser.

Dependency audit (same commands as `.github/workflows/dependency-audit.yml`, which also runs weekly):

```powershell
cd backend
pip install pip-audit
pip-audit -r requirements.txt
cd ..\frontend
npm install --package-lock-only --ignore-scripts
npm audit --omit=dev
```

---

## 7. Preview troubleshooting

The panel distinguishes the two cases:

- **"No preview in the crawl"** — the document has no `image`. Check with
  `docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl --dry-run --limit 3 -v`:
  if `image` is empty, the site does not expose `og:image` (like `example.com`) or the index was not reindexed.
- **"Image not reachable"** — `image` exists but the proxy refused the download. Backend logs report the reason without the URL:

  ```powershell
  docker compose logs backend | Select-String "Preview"
  ```

  A `404` on `/api/v1/preview` (visible in `http://127.0.0.1:8000/openapi.json`) means the container runs an old image: rerun `docker compose up -d --build backend`.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Meilisearch HTTP 401` during ingestion | Different `MEILI_MASTER_KEY` between container and CLI | Align `.env` and `backend/.env`, recreate the containers |
| `No documents were extracted` | Crawl with `-omit-body` or non-HTML resources only | Rerun Katana without `-omit-body`, check with `--dry-run` |
| Always `source: "brave"` | Empty index or query outside the corpus | Run ingestion, check the documents in Meilisearch |
| `503` from the backend | Meilisearch and every external provider unreachable | `docker compose ps`, health endpoint, backend logs |
| Brave `401`/`403` | Missing or wrong key, or a plan without Web Search | Regenerate the key and restart the backend |
| Brave `429` | Plan limit reached | Wait, upgrade the plan or configure Exa/Serper/Tavily |
| `exa_unauthorized` / `serper_unauthorized` / `tavily_unauthorized` | Wrong or revoked provider key | Check the key in `.env`, recreate the backend container |
| `exa_rate_limited` / `serper_rate_limited` / `tavily_rate_limited` | Provider quota or rate limit | Wait, lower the traffic or reorder `SEARCH_FALLBACK_ORDER` |
| `tavily_plan_limit` | Tavily plan limit exhausted | Upgrade the plan or remove Tavily from the shield |
| API credits consumed quickly | `SEARCH_MODE=parallel` queries several providers per search | Use `SEARCH_MODE=fallback`, lower `SEARCH_MAX_PROVIDERS` or disable paid features |
| `fallbacks` all `disabled` in health | Exa/Serper/Tavily keys empty | Normal if you only use Brave or the local index |
| `crawl.last_reason: "no_binary"` | Katana is not in the backend `PATH` | `docker compose up -d --build backend` or install `katana` and set `KATANA_BINARY` |
| `crawl.last_reason: "output_unavailable"` | `/data` is not writable by the container user | On Linux: `sudo chown -R 10001:10001 data`; check the `./data:/data` mount |
| `crawl.last_reason: "cooldown"` / `"not_public"` / `"busy"` | Domain crawled recently, private host or busy server | Intended: wait for the cooldown or tune `CRAWL_*` |
| `meta.crawl_scheduled: true` but no local results after a few minutes | Crawl still running, slow site or empty crawl | Check `crawl` in health and the backend logs (reason codes only) |
| "Tor Direct / Off" with `tor_unreachable` | The `aegis-tor` container is stopped or not started yet | `docker compose up -d tor`, then `docker compose ps tor` and `docker compose logs tor` |
| `tor_timeout` | First Tor circuit not ready or blocked network | Wait 20-30 s and retry; check that `aegis-tor` has internet access |
| `tor_not_an_exit` | The proxy answers but the exit is not in the Tor network | `TOR_PROXY_URL_DOCKER` must point at the `tor` service SocksPort (`socks5://tor:9050`) |
| Red badge but `tor-status` answers from the PC | Backend down or wrong `SEARCH_API_URL` | `docker compose ps`, `.env.local`, restart Next.js |
| Frontend "engine unreachable" | Backend down or wrong `SEARCH_API_URL` | `docker compose up -d`, check `.env.local` |
| `429 Too Many Requests` | Rate limit exceeded (`RATE_LIMIT_BURST` / `RATE_LIMIT_REQUESTS_PER_MINUTE`); behind Next.js the bucket is shared | Wait for `Retry-After`, raise the limits, or set `RATE_LIMIT_ENABLED=false` on a trusted local instance |
| `'tsc' is not recognized` | `node_modules` missing or devDependencies skipped | `cd frontend`, then `npm install` (or `npm install --include=dev`) |
| Meilisearch healthcheck "unhealthy" | Image without `curl` for the healthcheck | The backend works anyway; check with `curl.exe http://127.0.0.1:7700/health` |

---

## 9. Production notes

- Put the frontend behind HTTPS and expose neither Meilisearch nor FastAPI on the internet: the browser talks to Next.js only.
- Set `MEILI_ENV=production` and a strong master key (≥ 16 characters); rotate keys periodically.
- Consider disabling `/docs` in `create_app()` if the backend is publicly reachable.
- The built-in limiter protects `/search` and `/suggest` with a hashed peer address (`RATE_LIMIT_*`). Behind the Next.js server that bucket is global; put an edge limiter in front as well if you need per-visitor limits without logging IPs.
- The `backend` service runs with a read-only root filesystem, a bounded `/tmp` tmpfs, no Linux capabilities and `no-new-privileges`. If you extend the image with a dependency that needs a writable path, add a `tmpfs` entry (or a volume) instead of dropping `read_only`.
- Keep the `Content-Security-Policy` from `frontend/next.config.ts`: it allows only same-origin scripts, styles, fonts, images and connections. A reverse proxy must not strip it.
- Schedule periodic crawl and ingestion (`--replace` for a full refresh, incremental ingestion for updates).
- Scrape `/api/v1/stats` for aggregate counters (searches, local/external hits, provider errors): it never contains a query or an address. Set `SEARCH_FALLBACK_ORDER` according to cost, latency and coverage.
- Choose `SEARCH_MODE=fallback` to lower cost and latency, `SEARCH_MODE=parallel` for coverage and diversity.
- Respect `robots.txt`, the terms of use of the sites and the conditions of the Brave, Exa, Serper and Tavily plans.
- Keep the on-demand crawl under control: `CRAWL_DEPTH=1`, a low `CRAWL_RATE_LIMIT`, long cooldowns and `CRAWL_MAX_CONCURRENCY=1` are prudent for a public service. On exposed instances consider `CRAWL_ENABLED=false`, because every search without results can start a process on the server.
