# Configuration

All settings come from the environment. Three files are involved:

| File | Used by |
| --- | --- |
| `.env` | Docker Compose (Meilisearch, Tor, backend container) |
| `backend/.env` | The backend when you run it outside Docker |
| `frontend/.env.local` | Next.js, server-side only |

Create them from the examples:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Without copying by hand, use the setup helper for your platform:

```bat
scripts\setup.bat
```

```bash
./scripts/setup.sh
```

It creates the three files without overwriting ones that already exist and makes sure `data/` is there.

> Provider keys are read **only** by the backend. Never put them in `NEXT_PUBLIC_*` variables: anything prefixed that way is shipped to the browser.

---

## Core and Meilisearch

| Variable | Default | Description |
| --- | --- | --- |
| `MEILI_URL` | `http://meilisearch:7700` in Compose, `http://127.0.0.1:7700` locally | Meilisearch address |
| `MEILI_MASTER_KEY` | `dev-master-key-change-me` | Shared by Meilisearch and the backend; at least 16 characters, required in production mode |
| `MEILI_INDEX` | `pages` | Index name |
| `MEILI_TIMEOUT_S` | `4` | Meilisearch client timeout |
| `MIN_LOCAL_RESULTS` | `3` | Below this many local results, `engine=auto` runs the external shield |
| `PAGE_SIZE` | `10` | Default page size |
| `MAX_PAGE` | `10` | Maximum page number |
| `EXTERNAL_BUDGET_S` | `8` | Overall budget for the external phase, fallback or parallel |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins for direct API calls (Next.js does not need CORS) |

---

## Rate limiting and browser security

| Variable | Default | Description |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Limits `GET /api/v1/search` and `GET /api/v1/suggest` |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `120` | Sustained rate per bucket |
| `RATE_LIMIT_BURST` | `30` | Maximum burst before a `429` |
| `RATE_LIMIT_MAX_CLIENTS` | `4096` | Maximum buckets kept in memory (oldest evicted) |

The bucket key is a salted hash of the peer address: the raw address is never stored, logged or returned, and the salt is random per process. Rejected requests get `429` with `Retry-After`. `X-Forwarded-For` is ignored on purpose, so behind the Next.js server every request shares the Next.js address and the effective bucket is global; that still caps provider spend and abuse. The limiter does not cover `/health`, `/preview` or `/tor-status`.

The frontend sends a `Content-Security-Policy` on every response (`default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `img-src 'self' data: blob:`, `connect-src 'self'`). `script-src`/`style-src` keep `'unsafe-inline'` because Next.js hydrates with inline scripts and inlined critical CSS; replace it with a per-request nonce if you need a strict policy. Development adds `'unsafe-eval'` and `ws:`/`wss:` for HMR.

`pip-audit -r backend/requirements.txt` and `npm audit --omit=dev` run in `.github/workflows/dependency-audit.yml` on push, pull request and weekly.

---

## Providers

| Variable | Default | Description |
| --- | --- | --- |
| `SEARCH_MODE` | `fallback` | `fallback` (one provider at a time, lowest cost) or `parallel` |
| `SEARCH_MAX_PROVIDERS` | `4` | Providers queried in parallel mode (1–4) |
| `SEARCH_FALLBACK_ORDER` | `exa,serper,tavily` | Order after Brave; allowed names are `exa`, `serper`, `tavily` |
| `PROVIDER_CACHE_TTL_S` | `180` | Reuse a provider response kept in process memory for this many seconds; `0` disables the cache |
| `PROVIDER_CACHE_MAX_ENTRIES` | `256` | Upper bound on cached provider responses (1–4096) |
| `PROVIDER_FAILURE_THRESHOLD` | `3` | Consecutive errors after which a provider is skipped |
| `PROVIDER_COOLDOWN_S` | `60` | How long a failing provider is skipped before a new trial |

Brave is always the first external provider; leave `BRAVE_API_KEY` empty to exclude it.

### Brave

| Variable | Default | Description |
| --- | --- | --- |
| `BRAVE_ENABLED` | `true` | Provider switch (backend-local runs) |
| `BRAVE_API_KEY` | empty | Key from <https://api.search.brave.com/app/keys> |
| `BRAVE_SAFESEARCH` | `moderate` | Safe search level |
| `BRAVE_COUNTRY` | `IT` | ISO 3166-1 alpha-2 country code; empty leaves the Brave default (`US`) |
| `BRAVE_SEARCH_LANG` | `it` | Result language, e.g. `it` |
| `BRAVE_UI_LANG` | `it-IT` | Language of the response metadata |
| `BRAVE_EXTRA_SNIPPETS` | `true` | Ask for up to 5 additional excerpts per result |

### Exa

| Variable | Default | Description |
| --- | --- | --- |
| `EXA_API_KEY` | empty | Key from <https://dashboard.exa.ai/api-keys> |
| `EXA_SEARCH_TYPE` | `auto` | `auto`, `fast`, `instant`, `deep-lite`, `deep`, `deep-reasoning`, `neural` |
| `EXA_MODERATION` | `true` | Enable Exa content moderation |
| `EXA_INCLUDE_SUMMARY` | `false` | Adds a per-result summary to the insights; costs extra |

### Serper

| Variable | Default | Description |
| --- | --- | --- |
| `SERPER_API_KEY` | empty | Key from <https://serper.dev> |
| `SERPER_GL` | `it` | Google country |
| `SERPER_HL` | `it` | Google interface language |

### Tavily

| Variable | Default | Description |
| --- | --- | --- |
| `TAVILY_API_KEY` | empty | Key from <https://app.tavily.com> |
| `TAVILY_SEARCH_DEPTH` | `basic` | Tavily search depth |
| `TAVILY_LANGUAGE` | `it` | Result language |
| `TAVILY_SAFE_SEARCH` | `true` | Safe search |
| `TAVILY_INCLUDE_ANSWER` | `false` | Adds the answer to the insights; costs extra |
| `TAVILY_INCLUDE_RAW_CONTENT` | `false` | Adds raw page content; costs extra |

A provider with an empty key is skipped silently: you can run with Brave only, Exa only or any subset.

---

## On-demand crawl (Katana)

| Variable | Default | Description |
| --- | --- | --- |
| `CRAWL_ENABLED` | `true` | Enables the background crawl |
| `KATANA_BINARY` | `katana` | Name in `PATH` or absolute path |
| `CRAWL_OUTPUT_DIR` | `/data` | Folder for the JSONL files (the `./data` bind mount in the container) |
| `CRAWL_OUTPUT_PREFIX` | `dynamic_crawl` | File name prefix |
| `CRAWL_KEEP_OUTPUTS` | `false` | Keep the JSONL after ingestion (debugging only) |
| `CRAWL_DEPTH` | `1` | `katana -d` |
| `CRAWL_RATE_LIMIT` | `20` | `katana -rl`, requests per second |
| `CRAWL_MAX_STARTS_PER_MINUTE` | `6` | Crawl starts per minute (global token bucket) |
| `CRAWL_START_BURST` | `2` | Maximum burst of consecutive starts |
| `CRAWL_REQUEST_TIMEOUT_S` | `10` | `katana -timeout`, seconds per request |
| `CRAWL_TIMEOUT_S` | `90` | Hard limit for the Katana process |
| `CRAWL_DNS_TIMEOUT_S` | `3` | Limit for the background DNS/public-address check |
| `CRAWL_INGEST_TIMEOUT_S` | `60` | Limit for the ingestion that follows the crawl |
| `CRAWL_MAX_CONCURRENCY` | `2` | Concurrent crawls (1–10) |
| `CRAWL_DOMAIN_COOLDOWN_S` | `900` | Pause per domain after a successful crawl |
| `CRAWL_FAILURE_COOLDOWN_S` | `1800` | Pause per domain after an error |
| `CRAWL_INGEST_BATCH_SIZE` | `200` | Documents per Meilisearch request |

A token bucket limits **starts**, not running crawls; excess requests are skipped with `rate_limited`, never queued. The bucket is a plain counter: it stores no IPs, queries or domains.

---

## Ingestion

| Variable | Default | Description |
| --- | --- | --- |
| `DEDUPE_ENABLED` | `true` | SimHash near-duplicate filter, applied by the CLI and the crawler |
| `DEDUPE_THRESHOLD` | `0.9` | Similarity in `(0, 1]` above which two pages count as the same content; `1.0` drops only identical text |
| `DEDUPE_PREFIX_CHARS` | `2000` | Characters of `content` fed to SimHash (200–20000) |

Incremental ingestion has no environment variable: `--skip-unchanged` is on by default in the CLI and always on in the crawler (it compares the stored `content_hash`, one `documents/fetch` call per batch). `--no-skip-unchanged` uploads everything, and `--replace` disables the comparison because it empties the index first.

---

## SOCKS5 proxy rotation

| Variable | Default | Description |
| --- | --- | --- |
| `PROXY_ENABLED` | `true` | Rotate external provider calls through SOCKS5 proxies |
| `PROXY_LIST_URL` | `https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt` | Source of the public list |
| `PROXY_LIST_EXTRA` | empty | Extra proxies, one per line or comma separated (`host:port`, `user:pass@host:port`, `socks5://...`) |
| `PROXY_REFRESH_INTERVAL_S` | `900` | List refresh interval |
| `PROXY_RETRY_INTERVAL_S` | `60` | Shorter retry while the pool is empty |
| `PROXY_FETCH_TIMEOUT_S` | `10` | Timeout for the list download |
| `PROXY_STARTUP_TIMEOUT_S` | `8` | Maximum warm-up budget at startup |
| `PROXY_POOL_SIZE` | `50` | Maximum candidates kept |
| `PROXY_VALIDATE_ON_LOAD` | `true` | Validate candidates during refresh |
| `PROXY_VALIDATION_TIMEOUT_S` | `2.5` | Timeout for a single validation |
| `PROXY_VALIDATION_CONCURRENCY` | `20` | Parallel validations |
| `PROXY_VALIDATE_URL` | empty | When set, requires a real `GET` through the proxy |
| `PROXY_MAX_ATTEMPTS` | `3` | Attempts per call, each with a different proxy |
| `PROXY_CONNECT_TIMEOUT_S` | `2` | Proxy connection timeout; × attempts must stay under `EXTERNAL_BUDGET_S` |
| `PROXY_COOLDOWN_S` | `120` | Initial quarantine after an error |
| `PROXY_MAX_COOLDOWN_S` | `900` | Maximum quarantine |
| `PROXY_MAX_CLIENTS` | `64` | Cached `httpx` clients per proxy (≥ `PROXY_POOL_SIZE` recommended) |
| `PROXY_FALLBACK_DIRECT` | `true` | Use the server's direct connection when no proxy answers |
| `PROXY_WARNING_INTERVAL_S` | `60` | Minimum interval between fallback warnings |

Meilisearch, preview downloads and the list download itself always use the direct connection. The `httpx[socks]` extra (`socksio`) is already in `backend/requirements.txt`; without it the backend logs `proxy_socksio_missing` once and uses the direct connection.

---

## Tor diagnostics

| Variable | Default | Description |
| --- | --- | --- |
| `TOR_ENABLED` | `true` | Enables `/api/v1/tor-status` and `backend/scripts/test_tor.py`; no call at startup |
| `TOR_PROXY_URL` | `socks5://127.0.0.1:9050` | Tor SOCKS5 proxy for a backend outside Docker |
| `TOR_PROXY_URL_DOCKER` | `socks5://tor:9050` | Proxy as seen from the container; overrides only in Docker |
| `TOR_STATUS_URL` | `https://check.torproject.org/api/ip` | IP/`IsTor` verification service |
| `TOR_TIMEOUT_S` | `5` | Verification timeout |
| `TOR_CACHE_TTL_S` | `30` | Cache for the endpoint response |

The diagnostics are independent from the proxy pool used for providers: a green badge does not guarantee that provider calls went through Tor.

---

## Frontend

| Variable | Default | Description |
| --- | --- | --- |
| `SEARCH_API_URL` | `http://127.0.0.1:8000` | Backend address used by Server Components; never exposed to the browser |

The interface language is not configured here: it comes from the `NEXT_LOCALE` cookie, then `Accept-Language`, then English. See [ARCHITECTURE.md#interface-languages](ARCHITECTURE.md#interface-languages).
