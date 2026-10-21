# Architecture

```text
                         ┌──────────────────────────────┐
                         │  Katana (crawl JSONL)        │
                         └──────────────┬───────────────┘
                                        │ data/katana.jsonl
                                        ▼
                         ┌──────────────────────────────┐
                         │  Ingestion CLI (Python)      │
                         │  cleanup + normalization     │
                         └──────────────┬───────────────┘
                                        │ batch upsert
                                        ▼
┌─────────────┐   server-side   ┌────────────────────┐
│ Next.js 16  │ ──────────────► │ FastAPI            │
│ App Router  │   SEARCH_API_URL│ /api/v1/search     │
└─────────────┘                 └───────┬────────────┘
                                        │
                          ┌─────────────┴──────────────┐
                          ▼                            ▼
                 ┌─────────────────┐          ┌──────────────────────────────┐
                 │ Meilisearch     │          │ External shield (server-side)│
                 │ local index     │          │ Brave → Exa → Serper → Tavily│
                 └─────────────────┘          └──────────────────────────────┘
```

## Search flow

1. The browser opens `/search?q=...` in Next.js.
2. The Server Component calls the backend with `fetch(..., { cache: "no-store" })`: the query never travels from the browser to the backend and no API key is exposed.
3. FastAPI parses the query operators (`site:`, `"phrase"`, `-term`) and builds a validated Meilisearch filter from them plus the optional `domain`/`lang` parameters; the backend always queries Meilisearch with the cleaned text.
4. With `engine=auto` (default), if the local page has fewer than `MIN_LOCAL_RESULTS` results (default **3**), FastAPI uses the external server-to-server shield. With `SEARCH_MODE=fallback` it tries providers in sequence and stops at the first one that adds a new URL; with `SEARCH_MODE=parallel` it queries up to `SEARCH_MAX_PROVIDERS` providers at once and merges the responses. An explicit choice (`multi` or a single provider) runs the shield even with enough local results; `local` always skips it.
5. Results are merged: local first, then external ones, deduplicated by canonical URL. External hits are weighted by provider and position (Brave 1.00, Exa 0.97, Serper 0.94, Tavily 0.91, minus 0.03 per position), so a fast provider cannot fill the page with weaker results; if the same URL comes from several sources, the backend combines snippets and excerpts. With an explicit `multi` or provider choice, local results take at most half a page so providers always have room; local pagination advances accordingly. Every result and the response report their sources. The local block is then reordered by the combined score described in [Local ranking](#local-ranking).
6. If the query is an `http(s)` URL or the local index returned nothing, the endpoint queues a **background Katana crawl** (`BackgroundTasks`). The response is immediate and reports `meta.crawl_scheduled`; when the Katana process finishes, the shared pipeline indexes the pages, which become available in later searches. Guardrails prevent unbounded crawls and access to internal hosts.
7. The response is rendered with Cards, source Badges, cards for API insights, loading skeletons and error handling. An empty page carries `meta.local_index_empty` (the index has no documents yet) and `meta.query_suggestion`: every word is looked up on its own with typo tolerance and the highlighted index spelling closest to it becomes a "did you mean" link, preserving engine and filters. The engine selector lets you choose `auto`, `multi`, `local` or a specific provider; the choice travels as the `engine` parameter and is preserved across pagination.
8. The search bar debounces keystrokes (250 ms) and calls the same-origin `/api/suggest` route, which proxies `GET /api/v1/suggest`: a prefix search on the local index (title and domain), at most six entries, with no external provider involved. Selecting an entry runs a normal search.
9. The interface language is resolved server-side by the Next.js middleware: stored preference in the `NEXT_LOCALE` cookie, otherwise `Accept-Language`, with English as the default. The URL does not change and the backend receives neither the language nor the cookie.

## Project layout

```text
.
├── backend/
│   ├── app/
│   │   ├── api/routes/
│   │   │   ├── search.py          # GET /api/v1/search
│   │   │   ├── suggest.py         # GET /api/v1/suggest (local only)
│   │   │   ├── preview.py         # GET /api/v1/preview
│   │   │   ├── health.py          # GET /api/v1/health
│   │   │   ├── stats.py           # GET /api/v1/stats (aggregate counters)
│   │   │   └── tor.py             # GET /api/v1/tor-status
│   │   ├── ingestion/
│   │   │   ├── cli.py             # ingestion command
│   │   │   ├── pipeline.py        # pipeline shared by CLI + API crawler
│   │   │   ├── dedup.py           # SimHash near-duplicate filter
│   │   │   ├── katana.py          # JSONL parser + HTML extraction
│   │   │   ├── meili.py           # synchronous Meilisearch client (admin)
│   │   │   └── index_config.py    # index settings
│   │   ├── services/
│   │   │   ├── search.py          # hybrid orchestration, shield and merge
│   │   │   ├── provider_cache.py  # short-lived in-memory provider cache
│   │   │   ├── circuit.py         # per-provider circuit breaker
│   │   │   ├── metrics.py         # privacy-safe aggregate counters
│   │   │   ├── spelling.py        # typo comparison for "did you mean"
│   │   │   ├── crawler_service.py # on-demand Katana crawl in background
│   │   │   ├── rate_limit.py      # crawl token bucket + per-client HTTP limiter
│   │   │   ├── tor.py             # Tor exit node diagnostics
│   │   │   ├── meili.py           # asynchronous Meilisearch client
│   │   │   ├── preview.py         # preview image proxy + cache
│   │   │   ├── brave.py           # Brave provider
│   │   │   ├── exa.py             # Exa provider (official exa-py SDK)
│   │   │   ├── serper.py          # Serper provider (Google SERP)
│   │   │   ├── tavily.py          # Tavily provider
│   │   │   ├── proxy_manager.py   # SOCKS5 pool, rotation, health, fallback
│   │   │   ├── http_client.py     # HTTP protocol shared by providers
│   │   │   ├── provider.py        # common provider protocol
│   │   │   ├── text.py            # shared HTML cleanup
│   │   │   ├── results.py         # internal value objects
│   │   │   └── errors.py          # errors with reason codes
│   │   ├── utils/urls.py          # URL normalization and deduplication
│   │   ├── utils/query.py         # search operators -> text + Meilisearch filter
│   │   ├── utils/net.py           # shared SSRF guard (preview + crawl)
│   │   ├── config.py              # settings from env
│   │   ├── models.py              # public Pydantic schemas
│   │   ├── middleware.py          # hashed-IP rate limit middleware
│   │   └── main.py                # FastAPI app + privacy middleware
│   ├── scripts/ingest.py          # wrapper: python scripts/ingest.py
│   ├── scripts/measure_ingestion.py  # offline counters: dedupe + incremental
│   ├── scripts/test_tor.py        # console diagnostics for the Tor proxy
│   ├── tests/                     # pytest (parsing, URL, fallback, crawl, proxy)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env.example
├── frontend/
│   ├── app/                       # App Router: home, search, error pages, API proxies
│   ├── i18n/                      # locales, cookie and header, message loading
│   ├── messages/                  # en, it, es, fr, de, zh
│   ├── components/                # UI: search bar, filters, cards, preview, switcher
│   ├── e2e/                       # Playwright specs + Katana fixture
│   ├── lib/                       # server-side API client, types, utilities
│   ├── proxy.ts                   # cookie-based language negotiation
│   ├── playwright.config.ts       # end-to-end runner (starts the stack)
│   └── next.config.ts             # privacy headers + next-intl plugin
├── tor/                           # client-only Tor SOCKS5 image
├── data/                          # Katana crawl + on-demand crawl output (read-write)
├── scripts/                       # setup/start/stop/crawl helpers (.bat and .sh)
├── docker-compose.yml
├── .env.example
├── TODO.md
└── README.md
```

## Hybrid logic

| Situation | Behaviour |
| --- | --- |
| `engine=auto` and ≥ `MIN_LOCAL_RESULTS` local | Local-only response, no external provider is called |
| `engine=auto` and < `MIN_LOCAL_RESULTS` local | External shield in sequential fallback or parallel fan-out; local-first merge |
| `engine=multi` or explicit provider | Shield runs even with enough local results |
| Error, empty response or duplicates only | In `fallback` it moves to the next provider; in `parallel` the others keep going |
| Meilisearch unreachable | External shield, `meta.degraded = true` |
| Every external provider fails | `503` when there are no local results |
| No provider key configured | Local index only, no error |
| No results from any source | `200` with an empty list and an empty state in the frontend |

Deduplication uses a key independent of scheme and `www.`, so `https://www.example.com/a/` and `http://example.com/a` never appear twice.

### Operators and filters

`site:example.com`, `"exact phrase"` and `-exclude` are parsed by `backend/app/utils/query.py` before any backend is called:

- `site:` values are normalised like the stored `domain` field (lowercase, no scheme, no `www.`, no path) and become a `domain = "…"` Meilisearch filter, combined with `AND` when several are present;
- quoted phrases stay quoted in `q`, so Meilisearch itself enforces adjacency;
- `-term` has no Meilisearch equivalent (filters cannot express a negative full-text match), so it is applied to the local hits of the current page. `meta.local_hits` and the pagination total therefore describe the unfiltered candidate page.

The optional `domain` and `lang` query parameters add `domain = …` / `lang = …` clauses. Every value is validated and quoted, so no user string is interpolated into filter syntax. These filters are local-only: the external shield receives the cleaned text and never the restrictions.

### Local ranking

Local hits are not shown in raw Meilisearch order. After the merge, the local block is reordered by a combined score, computed by `rerank_local` in `backend/app/services/search.py` — a pure function (no I/O, no mutation of the input, deterministic for a given `now`):

```text
score = rank + 0.25 · title_match + 0.20 · freshness − domain_penalty
```

| Term | Meaning |
| --- | --- |
| `rank` | Meilisearch `_rankingScore`, clamped to 0..1; a missing score counts as 0 |
| `title_match` | Share of the query terms found in the title (0..1, case-insensitive) |
| `freshness` | Exponential decay on `crawled_at`, half-life 30 days; a page without a timestamp scores 0 |
| `domain_penalty` | 0.06 for every earlier hit from the same domain, capped at 0.30 |

The penalty is computed on the incoming order, so it is deterministic: a site that already fills the page is pushed down without being excluded. Ties keep the incoming order, which makes the result stable and easy to test (`backend/tests/test_rerank.py`).

Only the local block is reordered: external results keep their interleaving and local hits stay before them, so the local-first contract and the room reserved for providers are untouched. The rerank runs on the hits of the current page, and `sort=crawled_at:desc` disables it entirely: that is an explicit request for plain chronological order, applied by Meilisearch through `sortableAttributes`. The weights are constants, not environment settings.

The `crawled_at` value is returned with every local result, so the interface can show an "updated on" label; see [API.md](API.md#get-apiv1search).

## Ingestion pipeline

`ingest_katana_file` is the only ingestion path: the CLI and the on-demand crawler both call it. Each batch passes two filters before the upload.

### Incremental ingestion

Every document carries `content_hash`, a SHA-1 of the indexed fields (title, `h1`, content, image, language). `crawled_at` is excluded on purpose: it changes on every crawl and would make every page look new. Before uploading a batch, `MeiliClient.document_hashes` fetches the stored hashes by primary key (`id` is in `filterableAttributes`) and only the changed documents are sent.

- `--skip-unchanged` is on by default; `--no-skip-unchanged` uploads everything.
- `--replace` disables the comparison, since the index is emptied first.
- The crawler always compares: a repeated background crawl of the same page costs one `documents/fetch` call instead of an upload.
- On an index created before this feature the lookup fails with HTTP 400 (`id` is not filterable yet); the pipeline treats that as "nothing is known to be unchanged" and uploads the batch normally. The next run, after the CLI has reapplied `INDEX_SETTINGS`, gets the fast path.
- Documents indexed before the change have no `content_hash`, so the first run re-uploads them once. No `--replace` is needed.

Counters: `records`, `documents` (prepared), `unchanged`, `skipped`, `duplicates` (same URL twice in the file), `near_duplicates`, `indexed` (actually uploaded).

### Near-duplicate filter

Pagination, print views and mirrors publish almost identical pages under different URLs. `backend/app/ingestion/dedup.py` computes a 64-bit SimHash over the first `DEDUPE_PREFIX_CHARS` characters of `content` and compares fingerprints with a Hamming distance: two documents are the same page when their similarity is at least `DEDUPE_THRESHOLD` (default 0.9).

- Within a cluster the most recently crawled variant wins; the survivor keeps the cluster's first slot, so the output order is deterministic.
- A page without content is never dropped: an empty fingerprint is not comparable.
- The filter runs per batch, which catches the common case (the same crawl producing both URLs) with bounded memory. Two variants in different batches can both be uploaded.
- `--dedupe/--no-dedupe` and `--dedupe-threshold` override `DEDUPE_ENABLED` and `DEDUPE_THRESHOLD` for one run.

## Multi-provider shield

Priority order after the local index: `Brave → Exa → Serper → Tavily`.

Common rules:

- A provider with an empty key is skipped: Brave alone, Exa alone or any subset works.
- The external phase has an overall budget, `EXTERNAL_BUDGET_S` (default **8 seconds**). When it runs out, in-flight calls are cancelled and the response is marked partial.
- Errors and timeouts of one provider do not block the others. If at least one source answers, the page is returned with `meta.degraded = true`.
- A successful provider response is kept in process memory for `PROVIDER_CACHE_TTL_S` (default **180 seconds**), keyed by provider, normalised query, page and page size. A repeated search does not consume quota; nothing is written to disk or logged, and failures are never cached. Set the TTL to `0` to disable the cache.
- After `PROVIDER_FAILURE_THRESHOLD` consecutive errors (default **3**) a provider is skipped for `PROVIDER_COOLDOWN_S` (default **60 seconds**), so a dead provider does not cost its timeout on every search. The next call after the cooldown is a trial: a success closes the circuit, a failure re-opens it.
- `meta.provider_errors` lists the names of the providers that failed or were skipped by an open circuit in that request; `source` reports the first external provider that contributed and `sources` lists every source on the page.

With `SEARCH_MODE=fallback` (default):

- Providers are called **in sequence**, avoiding multiplied latency, cost and data sent to third parties.
- The first provider returning at least one URL not already present locally wins; errors, empty responses and duplicates only move to the next one.
- `SEARCH_MAX_PROVIDERS` does not apply: the chain can run to the last configured provider.

With `SEARCH_MODE=parallel`:

- Up to `SEARCH_MAX_PROVIDERS` providers are queried **at the same time**; the priority order is used only for tie-breaking.
- External results are ordered by a provider weight minus a positional penalty (see step 5 of the search flow), not by arrival: a provider answering first does not automatically get the top slots.
- The same URL from several sources gets merged `extra_snippets` and the richer snippet.
- Each provider contributes its features: Brave extra snippets, Exa highlights/summary, Serper `answerBox`/`knowledgeGraph`, Tavily answer/raw content.
- `meta.provider_hits` shows each provider's raw contribution and `meta.insights` collects the API-specific answers.

| Provider | Main variables | Features used by the engine |
| --- | --- | --- |
| Brave | `BRAVE_API_KEY`, `BRAVE_COUNTRY`, `BRAVE_SEARCH_LANG`, `BRAVE_UI_LANG`, `BRAVE_EXTRA_SNIPPETS` | Web results, extra snippets and infobox as insights; page offset (0–9), up to 20 results |
| Exa | `EXA_API_KEY`, `EXA_SEARCH_TYPE`, `EXA_MODERATION`, `EXA_INCLUDE_SUMMARY` | Official `exa-py` SDK (`AsyncExa`), highlights and text; optional summary; up to 100 results, emulated offset |
| Serper | `SERPER_API_KEY`, `SERPER_GL`, `SERPER_HL` | Google SERP: organic, `answerBox` and `knowledgeGraph` as insights; native pagination, up to 20 results |
| Tavily | `TAVILY_API_KEY`, `TAVILY_SEARCH_DEPTH`, `TAVILY_LANGUAGE`, `TAVILY_SAFE_SEARCH`, `TAVILY_INCLUDE_ANSWER`, `TAVILY_INCLUDE_RAW_CONTENT` | Results, optional answer and raw content; up to 20 results, emulated offset |

Exa and Tavily have no native offset comparable to Brave: for later pages the backend requests enough results to cover the page and slices them. Beyond their limits (100 for Exa, 20 for Tavily) deep pages can be empty; Serper and Brave paginate best.

Exa uses the official Python SDK `exa-py` through `AsyncExa`: the SDK handles authentication and serialization, while the backend keeps the overall timeout, parsing, deduplication and reason codes. The JavaScript package `exa-js` exists but is not used: calls stay in FastAPI, so the key is not duplicated in Next.js and the shield logic stays in one place.

Keys are read only by the backend and never appear in responses or logs. Error messages contain reason codes only, for example `exa_rate_limited` or `serper_unauthorized`.

## SOCKS5 proxy rotation

Every server-side call to Brave, Exa, Serper and Tavily can go through SOCKS5 proxies fetched from a public list. Meilisearch, preview downloads and the list download itself stay on the direct connection: the first is local, the others already have their own security rules.

1. At startup the pool downloads `PROXY_LIST_URL`, merges `PROXY_LIST_EXTRA` and keeps at most `PROXY_POOL_SIZE` candidates.
2. Each candidate is validated with a SOCKS5 handshake (no third-party request). With `PROXY_VALIDATE_URL` set, a full `GET` through the proxy is required too.
3. Calls use proxies in round-robin. On timeout or dropped tunnel the proxy is quarantined with exponential backoff and the call immediately retries with another candidate (`PROXY_MAX_ATTEMPTS`, default 3).
4. If no proxy answers, the backend falls back to the direct connection and logs a warning with a reason code only (`proxy_pool_empty`, `proxy_attempts_exhausted`, `proxy_socksio_missing`). With `PROXY_FALLBACK_DIRECT=false` the call fails instead, with the provider's reason code.
5. A background task refreshes the list every `PROXY_REFRESH_INTERVAL_S`, or every `PROXY_RETRY_INTERVAL_S` while the pool is empty. A failed download keeps the previous pool.

The official `exa-py` SDK exposes no proxy option: `ExaClient` clones the SDK `httpx` client configuration (base URL, key header, timeout) onto a client bound to the selected proxy, one attempt at a time. If a future SDK version no longer exposes a replaceable client, the backend detects it (`exa_proxy_unsupported`) and falls back to the direct connection instead of silently bypassing the proxy.

**Security and limits of public proxies.** Provider APIs are reached over HTTPS: the proxy sees the destination address and connection metadata, but not the query, headers or API key, which stay encrypted up to the provider (the scheme is `socks5://`, so DNS resolution happens on the server; `socks5h://` avoids that too). Never lower TLS verification to make a proxy work. Public lists are free, unstable and unverifiable: a proxy can disappear, be slow or log traffic. For serious use prefer private or commercial proxies through `PROXY_LIST_EXTRA` and consider `PROXY_FALLBACK_DIRECT=false` if the server must not reveal its IP when the pool is empty. Pool proxies ignore `HTTP_PROXY`/`ALL_PROXY` (`trust_env=False`), so an environment variable cannot bypass the rotation.

The router lists every variable with defaults: [CONFIGURATION.md#socks5-proxy-rotation](CONFIGURATION.md#socks5-proxy-rotation).

## Tor diagnostics

`/api/v1/tor-status` and `backend/scripts/test_tor.py` use `TOR_PROXY_URL` to verify that traffic really leaves from a Tor node. Inside Docker the default is `socks5://tor:9050`; outside Docker it is `socks5://127.0.0.1:9050`.

The `tor` service in `docker-compose.yml` is **client-only** (not a relay), uses `SocksPort 0.0.0.0:9050` and a `SocksPolicy` accepting only the private Docker network ranges. The port is **not published** on the host, so it is unreachable from the LAN; containers reach it over the Compose network. That is why the badge works on Windows without installing or configuring Tor on the PC.

To use a Tor already running on the host, set `TOR_PROXY_URL_DOCKER=socks5://host.docker.internal:9050`. Tor must then accept connections from the Docker bridge (`SocksPort 172.17.0.1:9050` or the gateway address, with a restricted `SocksPolicy`): the host's `127.0.0.1:9050` alone is not reachable from a container. Never expose unauthenticated SOCKS5 on `0.0.0.0`.

The footer badge is a client component calling the same-origin `/api/tor-status` route in Next.js: the browser never contacts the backend. It shows *Tor Active (Anonymous)* with the exit node in the tooltip, *Checking Tor…* during the first probe and *Tor Direct / Off* if Tor does not answer.

If you expose the endpoint, remember that `exit_ip` is the server's exit node and becomes readable by anyone who can call it: consider `TOR_ENABLED=false` or an edge limit.

## Interface languages

The interface is translated into **six languages** with `next-intl`: `en` (default), `it`, `es`, `fr`, `de`, `zh`. Messages live in `frontend/messages/<locale>.json`; the locale list, native names and cookie name are in `frontend/i18n/config.ts`.

Priority:

1. the `NEXT_LOCALE` cookie, written by the middleware on the first visit and by the switcher when you change language (one year, `Path=/`, `SameSite=Lax`);
2. the browser `Accept-Language` header, on the first visit or when the cookie is missing;
3. `en` as a fallback.

`frontend/proxy.ts` (Next.js 16's name for the middleware) resolves the language on every request and passes it to Server Components through the internal `x-aegis-locale` header, so the first visit already renders in the negotiated language. `frontend/i18n/request.ts` loads the matching messages, `frontend/app/layout.tsx` sets `<html lang>` and the proxy adds `Content-Language` to the response.

Design decisions:

- **no language prefix in the URL**: `/search?q=...` stays identical in every language, so shared links and pagination do not change;
- the switcher sits in the home header and in a compact version on search pages and in the skeleton; changing language rewrites the cookie and re-renders on the server (`router.refresh()`), without a page reload;
- the backend receives neither the language nor the cookie: the server-side fetch forwards only `Accept: application/json`, so search is independent from the interface language (provider targeting is configured in `.env`);
- translated: texts, ARIA labels, title and meta description, error messages and empty states; indexed content stays in its original language;
- a missing key produces a `MISSING_MESSAGE` error at runtime: add new strings to all six files.

To add a language: add the code to `locales` and the native name to `localeNames` (`frontend/i18n/config.ts`), create `frontend/messages/<locale>.json` copying the structure of `en.json`, and add the case in `loadMessages` (`frontend/i18n/request.ts`).

## Privacy

Implemented guarantees:

- **No access logs**: `uvicorn --no-access-log` in the Dockerfile and `uvicorn.access` disabled in code in `app/main.py`.
- **No query logs**: the `httpx` logger is forced to `WARNING`, because at `INFO` it would record the full URL of provider calls, query included.
- **No persistence**: no database and no history. The only caches are bounded and live in process memory (provider responses for `PROVIDER_CACHE_TTL_S`, preview images, rate-limit buckets); nothing is written to disk and everything is lost on restart. The frontend uses `cache: "no-store"` and the backend answers with `Cache-Control: no-store`.
- **Anonymous request limiter**: search and suggestions share a token bucket per salted hash of the client address. The raw address is never stored, logged or returned, and the salt is random per process. A rejected request gets `429` with `Retry-After`.
- **Content-Security-Policy**: every frontend response carries `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `img-src 'self' data: blob:` and `connect-src 'self'`; Next.js hydration needs `'unsafe-inline'` for scripts and styles unless a per-request nonce is added.
- **No tracking cookies**: CORS with `allow_credentials=False` and the backend strips any `Set-Cookie` header. The only cookie is the functional `NEXT_LOCALE`, which holds the interface language and is never forwarded to the backend.
- **No referrer**: `Referrer-Policy: no-referrer` on every frontend and backend response; external links use `rel="noreferrer noopener"`.
- **No third-party assets**: no remote fonts, external favicons, provider thumbnails or analytics in the browser.
- **Provider keys server-side only**: every call happens in the backend, with the server IP.
- **Local Meilisearch**: telemetry disabled (`MEILI_NO_ANALYTICS=true`), port exposed on loopback only.
- **Opaque errors**: logs contain reason codes only (`brave_rate_limited`, `meilisearch_connecterror`, …), never queries, URLs or keys.
- **On-demand crawl without log traces**: `crawler_service` records reason codes and counts only. Katana `stderr`, which may contain visited URLs, goes to `/dev/null`.
- **Anonymous crawl limiter**: the crawl-start token bucket is a single counter; it keeps no IPs, queries or domains.
- **Tor diagnostics without user data**: `/api/v1/tor-status` returns only `tor_active`, the server's exit node and a reason code; the response is cached so the badge does not multiply circuits.

Known limits:

- The language preference lives in a `NEXT_LOCALE` cookie (one year, `SameSite=Lax`, not `HttpOnly` because the client-side switcher writes it): it contains nothing but the language code and can be cleared from the browser.
- The query still appears in the page URL (`/search?q=...`): it ends up in browser history and in the logs of any reverse proxy. Switch to a client-side POST search or remove the query after loading if that matters.
- When the shield runs, the query is sent to the enabled providers (Brave, Exa, Serper, Tavily): they are third parties and apply their own policy. The user's IP is not forwarded. In `fallback` the query reaches one provider at a time; in `parallel` up to `SEARCH_MAX_PROVIDERS` at once.
- Hosting providers and CDNs can still see IPs at the network level. Strong anonymity needs a dedicated deployment without infrastructure logs.
- With rotation active, providers see the proxy IP, not the server's. If the pool is empty and `PROXY_FALLBACK_DIRECT=true`, the server IP becomes visible again; public proxies can still observe the destination and call metadata (not TLS content).
- The HTTP rate limiter keys on the peer address. Behind the Next.js server every request comes from the Next.js process, so the effective bucket is global rather than per user: it bounds abuse and provider spend, not one noisy visitor. The on-demand crawl amplifies this risk, so lower `CRAWL_MAX_CONCURRENCY`, raise the cooldowns or set `CRAWL_ENABLED=false` on exposed instances.
- The crawl guards validate URL and DNS before starting Katana, but the process resolves the name again: for full protection against DNS rebinding, run the backend on a network with filtered egress.
