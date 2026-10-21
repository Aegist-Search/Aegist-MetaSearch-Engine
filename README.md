# Aegis Search

A private web search engine: your own **Meilisearch** index, filled by **Katana** crawls, backed by a server-side shield of external providers (Brave, Exa, Serper, Tavily) when the local index is not enough. No IP logs, no saved queries, no persisted results and no tracking cookies.


# A Preview 

![Home Page](https://github.com/Aegist-Search/Aegist-MetaSearch-Engine/blob/main/screenshots/home.png)

![Formatted Card ](https://github.com/Aegist-Search/Aegist-MetaSearch-Engine/blob/main/screenshots/formatted-card.png)

![Results Page](https://github.com/Aegist-Search/Aegist-MetaSearch-Engine/blob/main/screenshots/results.png)

![Preview Card ](https://github.com/Aegist-Search/Aegist-MetaSearch-Engine/blob/main/screenshots/preview-card.png)



**[Architecture](docs/ARCHITECTURE.md)** · **[Configuration](docs/CONFIGURATION.md)** · **[API reference](docs/API.md)** · **[Operations](docs/OPERATIONS.md)**

---

## Why Aegis

- **Private by design** — the browser never talks to the backend or to a provider; API keys stay on the server; the only cookie stores your interface language.
- **Local first** — results come from your own index. External providers are a fallback, not the default.
- **Hybrid when needed** — sequential fallback or parallel fan-out across four providers, merged and deduplicated by canonical URL.
- **Self-hosted** — Docker Compose runs Meilisearch, a client-only Tor proxy and FastAPI; the UI is Next.js 16 with six languages built in.

## What you get

- **Local search with real query syntax** — `site:example.com`, `"exact phrase"` and `-exclude` operators, plus `domain` and `lang` filters; the API also sorts by crawl date (`sort=crawled_at:desc`).
- **Hybrid shield** — Brave, Exa, Serper and Tavily with per-provider weights, a short in-memory response cache, a circuit breaker and an overall time budget. Partial failures are reported without breaking the page.
- **Index quality** — incremental ingestion (unchanged pages are skipped), SimHash near-duplicate removal, Italian stop words and synonyms, query highlighting in titles and snippets.
- **Interface** — instant suggestions from the local index only, thumbnail previews proxied through the server, "did you mean…" on empty results, keyboard shortcuts (`/`, `Esc`, arrows, `Enter`) and localized empty states.
- **On-demand crawl** — a search with no local hits, or an `http(s)` URL as query, queues a background Katana crawl that is indexed for later searches.
- **Operational guards** — anonymous rate limiting on `/search` and `/suggest`, a strict Content-Security-Policy, a Tor exit check shown in the footer, and privacy-safe aggregate counters on `/api/v1/stats` (totals and provider names only). The backend container runs non-root with a read-only filesystem.

## How a search works

```text
Browser ──► Next.js ──► FastAPI ──┬──► Meilisearch (local index)
                                  └──► Brave → Exa → Serper → Tavily
```

1. Next.js renders the page server-side and calls FastAPI; the browser never sees the backend URL or an API key.
2. FastAPI parses the query operators and always queries Meilisearch. With fewer than `MIN_LOCAL_RESULTS` local hits, the external shield runs in sequence (`fallback`) or in parallel (`parallel`).
3. Local hits are reordered by a combined score (Meilisearch relevance, title match, freshness, repeated-domain penalty); external results are weighted by provider and position. Everything is merged local-first and deduplicated by canonical URL.
4. In `auto` mode, a search with no local hits (or a query that is an `http(s)` URL) queues a background Katana crawl whose pages are indexed for later searches; `engine=local` never crawls.
5. While you type, the search bar calls `/api/v1/suggest` (debounced, local index only). Previews are downloaded by the backend and served through `/api/v1/preview`, so the browser never contacts the origin site.

Each endpoint and its response fields are documented in **[docs/API.md](docs/API.md)**.

## Quick start

Requirements: **Docker Desktop** and **Node.js 20+**.

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
docker compose up -d --build
cd frontend && npm install && npm run dev
```

Open <http://localhost:3000> and check the backend with `curl http://127.0.0.1:8000/api/v1/health`. Add at least one provider key to `.env` (`BRAVE_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY` or `TAVILY_API_KEY`): without keys the local index still works.

The helper scripts start and stop everything in the background. They exist in two flavours only: `.bat` for Windows and `.sh` for Linux/macOS.

```bat
scripts\setup.bat
scripts\start.bat
scripts\stop.bat
```

```bash
./scripts/setup.sh
./scripts/start.sh
./scripts/stop.sh
```

Populate the index with a crawl, then ingest it (all variants, including the Docker-only and script forms, are in **[docs/OPERATIONS.md#4-crawl](docs/OPERATIONS.md#4-crawl)**):

```bash
# crawl a site into data/katana.jsonl
docker run --rm -v "$PWD/data:/data" projectdiscovery/katana:latest -u https://example.com -d 1 -jsonl -o /data/katana.jsonl

# index it
docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl
```

On Git Bash prefix the `docker run` with `MSYS_NO_PATHCONV=1`; otherwise use the wrapper that does the mount and the validation for you: `scripts\crawl.bat -Url https://example.com -Ingest` on Windows or `./scripts/crawl.sh --url https://example.com --ingest` on Linux/macOS.

## Essential configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEILI_MASTER_KEY` | `dev-master-key-change-me` | Shared by Meilisearch and the backend, at least 16 characters |
| `BRAVE_API_KEY` | empty | First external provider |
| `EXA_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY` | empty | Optional fallbacks; an empty key disables that provider |
| `SEARCH_MODE` | `fallback` | `fallback` (one provider at a time) or `parallel` |
| `SEARCH_MAX_PROVIDERS` | `4` | Providers queried together in `parallel` mode (1–4) |
| `MIN_LOCAL_RESULTS` | `3` | Local results below which the shield runs |
| `EXTERNAL_BUDGET_S` | `8` | Overall time budget for the external provider phase |
| `PROXY_ENABLED` | `true` | SOCKS5 rotation for provider calls |
| `CRAWL_ENABLED` | `true` | On-demand Katana crawl in the background |
| `RATE_LIMIT_ENABLED` | `true` | Anonymous rate limit on `/search` and `/suggest` |
| `SEARCH_API_URL` | `http://127.0.0.1:8000` | Frontend → backend URL (server-side only) |

Every other variable is documented in **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Diagram, request flow, project layout, hybrid logic, local ranking, providers, proxy rotation, Tor, interface languages, privacy |
| [docs/API.md](docs/API.md) | Endpoints, parameters, operators, response fields, examples |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Complete environment variable reference |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Setup, scripts, crawling, ingestion, tests, troubleshooting, production notes |

## Checks

```bash
# backend
cd backend && pip install -r requirements-dev.txt && pytest

# frontend
cd frontend && npm run typecheck && npm run build

# end-to-end (needs Docker and Chromium)
cd frontend && npm run test:e2e
```

The suite, the coverage gate and the dependency audit commands are described in **[docs/OPERATIONS.md#6-tests-and-checks](docs/OPERATIONS.md#6-tests-and-checks)**. CI runs the backend tests (with the coverage threshold on `app/services/search.py`), the frontend typecheck/build and both audits in `.github/workflows/ci.yml`.

## Privacy in one paragraph

No access logs, no query logs, no database, no persisted result cache: the only caches are bounded and in process memory. The backend strips `Set-Cookie`, CORS runs with `allow_credentials=False`, every response sets `Referrer-Policy: no-referrer`, and logs contain reason codes only (`brave_rate_limited`, `katana_failed`, …). The rate limiter keys on a salted hash of the peer address, never on the address itself. Every frontend response carries a strict `Content-Security-Policy` (`default-src 'self'`, `frame-ancestors 'none'`, no remote assets), thumbnails are fetched server-side and no third-party asset is loaded in the browser. The single functional cookie, `NEXT_LOCALE`, holds your interface language and is never forwarded to the backend. The search query still appears in the page URL, and when the shield runs the query is sent to the providers you enabled. Full list of guarantees and limits: **[docs/ARCHITECTURE.md#privacy](docs/ARCHITECTURE.md#privacy)**.

## Development

This project is coded with **DeepSeek V4.1 Flash**. The model drafts the changes; every fix is reviewed and run locally before it lands.

## License

MIT — see [LICENSE](LICENSE).

Code provided as a project base: adapt it, extend it and verify the legal implications of crawling and of using third-party APIs before publishing it.
"# Aegist-MetaSearch-Engine" 
"# Aegist-MetaSearch-Engine" 
