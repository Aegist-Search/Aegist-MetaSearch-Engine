# Aegis Search — documentation

This folder holds the reference material. The [main README](../README.md) is the short version: what the project is, why it exists and how to start it.

| Document | What you will find |
| --- | --- |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Diagram, search flow, project layout, hybrid engine logic, provider features, SOCKS5 rotation, Tor diagnostics, interface languages, privacy guarantees and limits |
| **[API.md](API.md)** | `GET /api/v1/search`, `/suggest`, `/health`, `/tor-status`, `/preview`: parameters, response fields, error codes and ready-to-copy requests |
| **[CONFIGURATION.md](CONFIGURATION.md)** | Every environment variable, grouped by area, with defaults and effect |
| **[OPERATIONS.md](OPERATIONS.md)** | Requirements, `.env` files, Docker stack, Windows scripts, crawling, ingestion, tests, troubleshooting, production notes |

## Where to start

- **Just want to run it** → [OPERATIONS.md](OPERATIONS.md#1-requirements) then [OPERATIONS.md](OPERATIONS.md#3-start-the-stack).
- **Integrating with the API** → [API.md](API.md).
- **Tuning providers, proxies or the crawler** → [CONFIGURATION.md](CONFIGURATION.md).
- **Understanding the engine or contributing** → [ARCHITECTURE.md](ARCHITECTURE.md).
- **Something is broken** → [OPERATIONS.md](OPERATIONS.md#8-troubleshooting).

## Conventions

- Commands are shown for **macOS / Linux / Git Bash** (POSIX) and, where they differ, for **Windows 11 PowerShell** and **cmd.exe**.
- Environment variables are written as `NAME`; defaults are the ones in the `.env.example` files.
- Logs never contain queries, URLs, domains or keys: when a diagnostic is needed, the backend reports a **reason code** such as `exa_rate_limited` or `katana_failed`.

## Repository map

```text
README.md            short introduction and quick start
docs/                this documentation
backend/             FastAPI service, ingestion CLI and crawl orchestration
frontend/            Next.js 16 interface (six languages)
tor/                 client-only Tor SOCKS5 proxy image
scripts/             setup/start/stop/crawl helpers (.bat for Windows, .sh for Linux/macOS)
data/                Katana crawl files (read-write mount in the backend)
```

The full tree with per-file comments is in [ARCHITECTURE.md#project-layout](ARCHITECTURE.md#project-layout).
