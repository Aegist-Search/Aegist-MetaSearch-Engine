# API reference

The backend is a FastAPI service. Default address: `http://127.0.0.1:8000`, published on loopback only.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/v1/search` | `GET` | Hybrid search: local index + external shield |
| `/api/v1/suggest` | `GET` | Autocomplete from the local index only |
| `/api/v1/preview` | `GET` | Preview image of a result (server-side download) |
| `/api/v1/health` | `GET` | Status of Meilisearch, providers, proxy pool, crawler |
| `/api/v1/stats` | `GET` | Aggregate search counters (no queries, no addresses) |
| `/api/v1/tor-status` | `GET` | Verifies that the configured SOCKS5 proxy is a Tor exit |

OpenAPI is available at `/docs` and the raw schema at `/openapi.json`. Consider disabling `/docs` in `create_app()` when the backend is publicly reachable.

Conventions:

- No authentication: the service is meant to stay behind Next.js on loopback.
- No request bodies, no credentials, no cookies. CORS runs with `allow_credentials=False`.
- Responses use `Cache-Control: no-store`; errors never include queries, URLs or keys, only reason codes.

---

## `GET /api/v1/search`

### Parameters

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `q` | string | — | Required, 1–400 characters |
| `page` | integer | `1` | 1–10 (`MAX_PAGE`) |
| `page_size` | integer | `10` | 1–20 |
| `engine` | string | `auto` | `auto`, `multi`, `local`, `brave`, `exa`, `serper`, `tavily` |
| `sort` | string | `relevance` | `relevance` (combined local score) or `crawled_at:desc` (newest pages first, no rerank) |
| `domain` | string | — | Restricts the local index to one hostname, e.g. `example.com` (max 253 characters) |
| `lang` | string | — | Restricts the local index to one language code, e.g. `it` (2–3 lowercase letters) |

### Operators

The query string accepts three operators, parsed by `app.utils.query.parse_query` before any backend is called:

| Syntax | Effect |
| --- | --- |
| `site:example.com` | Adds `domain = "example.com"` to the Meilisearch filter. `https://`, `www.` and paths are normalised away; subdomains must be written explicitly |
| `"frase esatta"` | The quotes are kept: Meilisearch matches the phrase adjacency instead of the single words |
| `-parola` | Removes local results whose title, `h1`, snippet or domain contains the term |

Details:

- The operators apply to **local results only**. External providers receive the cleaned text (`example.com` for a `site:`-only query), so a `site:` restriction never turns into a provider-side query.
- `-term` cannot be expressed as a Meilisearch filter (there is no negative full-text operator), so it is applied to the local page after the search: `pagination.total` still comes from Meilisearch and may be higher than the number of visible hits.
- A malformed operator is not silently dropped: `site:`, `site:localhost` or an unbalanced quote stay in the searchable text.
- `domain` and `lang` are combined with `AND` and with any `site:` operator (they are quoted, validated values, never raw user syntax).
- A query made only of exclusions (`-spam`) has no searchable text and returns `422`.

`engine` behaviour:

| Value | What runs |
| --- | --- |
| `auto` | Local index; the shield follows `SEARCH_MODE` when local hits are below `MIN_LOCAL_RESULTS` |
| `multi` | Local index + parallel fan-out across configured providers, even with enough local results |
| `local` | Local index only; never calls a provider and never crawls |
| `brave`, `exa`, `serper`, `tavily` | Local index + that single provider |

`sort` behaviour:

- `relevance` (default): local hits are reordered by a combined score — Meilisearch relevance, query match in the title, freshness of `crawled_at` and a penalty for repeated domains. The formula and the weights are in [ARCHITECTURE.md#local-ranking](ARCHITECTURE.md#local-ranking).
- `crawled_at:desc`: Meilisearch sorts the local index by `crawled_at` descending and the rerank is skipped, so the order is strictly chronological.

External results are ordered by provider weight and position (Brave 1.00, Exa 0.97, Serper 0.94, Tavily 0.91, minus 0.03 per position), so `multi` does not reward whichever provider answers first. A successful provider response is cached in process memory for `PROVIDER_CACHE_TTL_S` (default 180 s), keyed by provider, normalised query, page and page size: repeating a search within the TTL does not consume provider quota and writes nothing to disk. After `PROVIDER_FAILURE_THRESHOLD` consecutive errors the provider is skipped for `PROVIDER_COOLDOWN_S`; those names are returned in `meta.provider_errors`.

`crawled_at` only appears in responses after the index settings have been reapplied; run the ingestion CLI once (`docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl`). The field was already stored in the documents, so no full reindex is needed.

### Requests

**PowerShell (Windows 11):**

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/search?q=privacy&page=1"
curl.exe "http://127.0.0.1:8000/api/v1/search?q=privacy&engine=multi"
```

**macOS / Linux / Git Bash:**

```bash
curl "http://127.0.0.1:8000/api/v1/search?q=privacy&page=1"

# operators: one site, an exact phrase, an excluded word
curl --get "http://127.0.0.1:8000/api/v1/search" \
  --data-urlencode 'q=site:example.com "privacy policy" -cookie'

# local filters (domain and language)
curl "http://127.0.0.1:8000/api/v1/search?q=privacy&domain=example.com&lang=it"

# force the fan-out across every configured provider
curl "http://127.0.0.1:8000/api/v1/search?q=privacy&engine=multi"

# local index only, no external calls
curl "http://127.0.0.1:8000/api/v1/search?q=privacy&engine=local"

# second page, 20 results
curl "http://127.0.0.1:8000/api/v1/search?q=privacy&page=2&page_size=20"
```

### Response

```json
{
  "query": "privacy",
  "engine": "auto",
  "source": "brave",
  "sources": ["local", "brave"],
  "available_providers": ["brave"],
  "results": [
    {
      "id": "5f0c...",
      "title": "A practical guide to privacy",
      "url": "https://example.com/privacy",
      "snippet": "A practical guide to reducing your online footprint.",
      "title_highlight": [
        { "text": "A practical guide to ", "match": false },
        { "text": "privacy", "match": true }
      ],
      "snippet_highlight": null,
      "extra_snippets": [],
      "source": "local",
      "domain": "example.com",
      "h1": "Privacy on the web",
      "rank": 0.98,
      "crawled_at": 1767225600
    },
    {
      "id": "a91b...",
      "title": "Privacy: what it is and how to protect it",
      "url": "https://another.example/privacy",
      "snippet": "…",
      "extra_snippets": [
        "An additional excerpt returned by Brave.",
        "A second context excerpt."
      ],
      "source": "brave",
      "domain": "another.example",
      "h1": null,
      "rank": null,
      "crawled_at": null
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "has_more": true,
    "total": null,
    "total_pages": null
  },
  "meta": {
    "took_ms": 184,
    "local_hits": 1,
    "brave_hits": 9,
    "external_hits": 9,
    "provider_hits": { "brave": 9 },
    "provider_errors": [],
    "local_index_empty": false,
    "query_suggestion": null,
    "insights": [
      {
        "source": "brave",
        "title": "Privacy",
        "text": "Overview and resources on the topic of privacy.",
        "url": "https://example.com/privacy"
      }
    ],
    "degraded": false,
    "crawl_scheduled": false
  }
}
```

### Response fields

Top level:

| Field | Meaning |
| --- | --- |
| `query` | The text actually searched: operators (`site:`, `-term`) are removed, a quoted phrase keeps its quotes. A `site:`-only query echoes the domain |
| `engine` | The requested selection, not the mode that ran |
| `source` | `"local"` when the page comes only from the index, otherwise the first external provider that contributed |
| `sources` | Every actual source of the page, in priority order, e.g. `["local", "brave", "exa"]` |
| `available_providers` | Providers with a configured key (the UI uses it to hide unavailable choices) |
| `results` | Merged, deduplicated page |

Result:

| Field | Meaning |
| --- | --- |
| `id` | SHA-1 of the canonical URL for local results; provider id otherwise |
| `title`, `url`, `snippet` | Display data |
| `title_highlight`, `snippet_highlight` | Local results only: `{text, match}` segments; build `<mark>` in React, never inject HTML |
| `extra_snippets` | Additional excerpts from Brave, or merged from several providers |
| `source` | `local`, `brave`, `exa`, `serper`, `tavily` |
| `domain`, `h1` | Normalized host and first heading (`null` for external results) |
| `rank` | Meilisearch relevance for local results, `null` otherwise |
| `crawled_at` | Unix timestamp (seconds) of the crawl that indexed a local result; `null` for external results |

Pagination and metadata:

| Field | Meaning |
| --- | --- |
| `pagination.total`, `total_pages` | Filled only for fully local responses; providers expose no reliable total |
| `pagination.has_more` | Whether another page is likely available |
| `meta.took_ms` | Server-side duration |
| `meta.local_hits` | Local results found before the merge |
| `meta.external_hits` | Unique external results on the page |
| `meta.brave_hits` | Brave results on the page (kept for compatibility) |
| `meta.provider_hits` | Raw result count per responding provider |
| `meta.provider_errors` | Names of the providers that failed or were skipped after repeated errors; empty when every provider answered |
| `meta.local_index_empty` | `true` only when the response is empty **and** the local index holds no documents (fresh install); checked with one stats call only in that case |
| `meta.query_suggestion` | Corrected query proposed for an empty page: each word is looked up on its own with `matchingStrategy: "all"`, the highlighted runs give the spelling stored in the index and the closest one within Meilisearch's typo tolerance wins. Computed on the fly, never stored; `null` when there is nothing to suggest |
| `meta.insights` | Provider answers/knowledge snippets (Serper `answerBox`/`knowledgeGraph`, Brave infobox, Exa summary, Tavily answer) |
| `meta.degraded` | `true` when a source answered with an error and results are partial |
| `meta.crawl_scheduled` | `true` when this request queued a background Katana crawl |

### Errors

| Status | When |
| --- | --- |
| `422` | Invalid `q`, `page`, `page_size`, `engine`, `sort`, `domain` or `lang`; also a query without searchable text |
| `429` | Client rate limit exceeded; the response carries `Retry-After` in seconds |
| `503` | No source is reachable and there are no local results |
| `200` | No results from any source: empty `results` list |

---

## `GET /api/v1/suggest`

Autocomplete for the search bar. It queries **only** the local index (title and domain, prefix matching) and never calls an external provider, so what the user is typing stays on the server. The query is not logged or stored.

| Name | Type | Notes |
| --- | --- | --- |
| `q` | string | Required, 1–80 characters |

```bash
curl "http://127.0.0.1:8000/api/v1/suggest?q=priv"
```

```json
{
  "query": "priv",
  "suggestions": [
    {
      "title": "A practical guide to privacy",
      "url": "https://example.com/privacy",
      "domain": "example.com"
    }
  ]
}
```

- At most **6** suggestions; duplicate URLs are collapsed and entries without a title are dropped.
- Operators are parsed as in `/search`; a query without searchable text (`-spam`) returns an empty list.
- An unreachable index returns `200` with an empty list, so the dropdown never shows an error while typing.
- Like `/search`, this endpoint shares the per-client rate limit and can answer `429` with `Retry-After`.

In the UI the browser calls the same-origin Next.js route `/api/suggest?q=...`, which proxies this endpoint.

---

## `GET /api/v1/preview`

Downloads the preview image of a result on the server, so the browser never contacts the origin site.

| Name | Type | Notes |
| --- | --- | --- |
| `url` | string | Required, 8–2048 characters, absolute `http(s)` URL |

```bash
curl -o preview.jpg "http://127.0.0.1:8000/api/v1/preview?url=https%3A%2F%2Fexample.com%2Fcover.jpg"
```

- Returns the image bytes with `Cache-Control: public, max-age=86400, immutable`, `Content-Disposition: inline` and `X-Content-Type-Options: nosniff`.
- Only public hosts are accepted (loopback, private ranges, link-local and `169.254.169.254` are rejected, also after redirects); only raster types (`jpeg`, `png`, `webp`, `gif`, `avif`, `bmp`, `ico`) are served.
- The body is capped at 4 MB per image and cached in memory (32 MB total).
- `404` when no usable image is available.

In the UI this goes through the same-origin Next.js route `/api/preview?url=...`.

---

## `GET /api/v1/health`

```json
{
  "status": "ok",
  "meilisearch": "up",
  "brave": "configured",
  "fallbacks": {
    "exa": "configured",
    "serper": "disabled",
    "tavily": "disabled"
  },
  "index": "pages",
  "proxy": {
    "enabled": true,
    "pool": 42,
    "available": 37,
    "quarantined": 5,
    "last_refresh_s": 128.4,
    "last_error": null
  },
  "crawl": {
    "enabled": true,
    "binary": true,
    "running": 0,
    "reserved": 0,
    "throttled": 0,
    "last_reason": null
  },
  "version": "1.0.0"
}
```

- `status`: `ok` when Meilisearch answers, `degraded` otherwise.
- `brave` and `fallbacks`: `configured` or `disabled`. Keys are never returned.
- `proxy`: pool counters and the last reason code only.
- `crawl`: `binary` tells whether Katana is available; `last_reason` is a code such as `katana_failed`, `timeout`, `cooldown`, `not_public`, `rate_limited`.

---

## `GET /api/v1/stats`

Aggregate counters for monitoring. They live in process memory, reset on restart and
never contain a query, a URL, a result or a client address: only totals and provider
names, so the endpoint can be scraped without weakening the privacy guarantees.

```bash
curl "http://127.0.0.1:8000/api/v1/stats"
```

```json
{
  "uptime_s": 5231,
  "searches_total": 412,
  "searches_failed_total": 2,
  "searches_degraded_total": 31,
  "searches_empty_total": 57,
  "local_hits_total": 2810,
  "external_hits_total": 640,
  "provider_errors_total": { "brave": 12, "exa": 3 }
}
```

| Field | Meaning |
| --- | --- |
| `uptime_s` | Seconds since the process started |
| `searches_total` | Searches answered, failed ones included |
| `searches_failed_total` | Searches that returned `503` because no backend answered |
| `searches_degraded_total` | Answers served while at least one backend was failing |
| `searches_empty_total` | Searches whose page had no result |
| `local_hits_total` | Local index hits returned across all searches |
| `external_hits_total` | External results that reached a page |
| `provider_errors_total` | Searches where that provider failed or was skipped, counted once per search |

---

## `GET /api/v1/tor-status`

Verifies that the SOCKS5 proxy in `TOR_PROXY_URL` (in Docker `TOR_PROXY_URL_DOCKER`, default `socks5://tor:9050`) really is a Tor exit, by querying `check.torproject.org` **through** that proxy.

```json
{
  "tor_active": true,
  "exit_ip": "185.220.101.1",
  "latency_ms": 412,
  "error": null
}
```

- `tor_active=false` comes with a reason code (`tor_timeout`, `tor_unreachable`, `tor_not_an_exit`, `tor_http_503`, …) and the endpoint still answers within `TOR_TIMEOUT_S` (default 5 s).
- `exit_ip` is the **server's** exit node, not the visitor's, and is `null` if the service returns an invalid value.
- Responses are cached for `TOR_CACHE_TTL_S` (default 30 s) and guarded by a lock, so the polling badge does not open a circuit per request.
- With `TOR_ENABLED=false` the answer is `tor_active=false` with `"tor_disabled"` and no network call.

In the UI the badge uses the same-origin Next.js route `/api/tor-status`.
