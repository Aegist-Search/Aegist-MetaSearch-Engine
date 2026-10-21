"""Console diagnostic for the Tor SOCKS5 proxy.

Usage (from ``backend/``):

    python scripts/test_tor.py
    python scripts/test_tor.py --proxy socks5://127.0.0.1:9150
    TOR_PROXY_URL=socks5://127.0.0.1:9050 python scripts/test_tor.py

The script asks check.torproject.org through the proxy, prints whether the
traffic really exits from a Tor node and which exit address was detected.
Exit code ``0`` means Tor is active, ``1`` anything else (proxy off, timeout,
not a Tor exit), so it can be used in a health check.

It prints the exit address on purpose: that address belongs to the server's
Tor circuit, not to the user who ran the search.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow both ``python scripts/test_tor.py`` from backend/ and
# ``python backend/scripts/test_tor.py`` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.tor import TorStatusService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test-tor",
        description="Verifica che il proxy SOCKS5 configurato sia un'uscita Tor valida.",
        epilog=(
            "esempi:\n"
            "  python scripts/test_tor.py\n"
            "  python scripts/test_tor.py --proxy socks5://127.0.0.1:9150\n"
            "  python scripts/test_tor.py --timeout 10\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--proxy", default="", help="Override TOR_PROXY_URL")
    parser.add_argument("--url", default="", help="Override TOR_STATUS_URL")
    parser.add_argument("--timeout", type=float, default=0.0, help="Override TOR_TIMEOUT_S")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = get_settings()
    overrides: dict[str, object] = {}
    if args.proxy.strip():
        overrides["tor_proxy_url"] = args.proxy.strip()
    if args.url.strip():
        overrides["tor_status_url"] = args.url.strip()
    if args.timeout > 0:
        overrides["tor_timeout_s"] = args.timeout
    if overrides:
        # model_copy keeps the command line override local to this process.
        settings = settings.model_copy(update=overrides)

    if not settings.tor_enabled:
        print("Tor disabilitato (TOR_ENABLED=false).")
        return 1

    print(f"Proxy SOCKS5:        {settings.tor_proxy_url}")
    print(f"Servizio di verifica: {settings.tor_status_url}")
    print(f"Timeout:             {settings.tor_timeout_s:.0f}s")

    status = await TorStatusService(settings).check()
    if status.active:
        exit_ip = status.exit_ip or "sconosciuto"
        latency = f" ({status.latency_ms} ms)" if status.latency_ms is not None else ""
        print(f"Tor attivo: uscita dal nodo {exit_ip}{latency}.")
        return 0

    print(f"Tor NON attivo: {status.error or 'errore sconosciuto'}.")
    if status.exit_ip:
        print(f"IP rilevato dal servizio di verifica: {status.exit_ip}")
    print("Controlla che il daemon Tor sia avviato e che SOCKS5 sia raggiungibile su "
          f"{settings.tor_proxy_url}.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
