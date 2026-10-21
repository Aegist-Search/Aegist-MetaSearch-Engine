"""Network safety helpers shared by the preview proxy and the on-demand crawler.

Both features accept a URL chosen by the user and then make the *server* fetch
it, so they are potential SSRF pivots. Every target (including each redirect
hop for the preview proxy) must resolve exclusively to public addresses.

For the preview proxy a second problem matters: resolving a host and then
letting ``httpx`` resolve it again opens a DNS-rebinding window between the
check and the connection. ``resolve_public_target`` returns the validated
addresses so the caller can connect to one of them directly, with the original
hostname kept only in the ``Host`` header and the TLS SNI extension.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class PublicTarget:
    """A validated URL plus the public addresses it resolved to."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]
    secure: bool

    @property
    def host_header(self) -> str:
        """Value for the ``Host`` header (the port only when non-default)."""

        host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = 443 if self.secure else 80
        return host if self.port == default_port else f"{host}:{self.port}"

    def url_for(self, address: str) -> str:
        """Request URL that connects to ``address`` instead of resolving again."""

        parsed = urlsplit(self.url)
        host = f"[{address}]" if ":" in address else address
        default_port = 443 if self.secure else 80
        netloc = host if self.port == default_port else f"{host}:{self.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, ""))


async def _resolve(host: str, port: int) -> tuple[str, ...]:
    """Resolve ``host`` once; kept separate so tests can stub the resolver."""

    infos = await asyncio.get_running_loop().getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    )
    return tuple(
        str(info[4][0]).split("%", 1)[0]
        for info in infos
        if info[4]
    )


async def resolve_public_target(url: str) -> PublicTarget | None:
    """Return the validated target for ``url``, or ``None`` when unsafe.

    Every resolved address must be global: loopback, private, link-local and
    reserved ranges (including the cloud metadata endpoint at 169.254.169.254)
    are rejected, so neither feature can be used to read internal services.
    """

    candidate = (url or "").strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        return None
    if any(character.isspace() for character in candidate):
        return None

    try:
        parsed = urlsplit(candidate)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    try:
        resolved = await _resolve(parsed.hostname, port)
    except (OSError, UnicodeError):
        return None
    if not resolved:
        return None

    addresses: list[str] = []
    for address in resolved:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
            address = str(ip)
        if not ip.is_global:
            return None
        if address not in addresses:
            addresses.append(address)

    return PublicTarget(
        url=candidate,
        host=parsed.hostname,
        port=port,
        addresses=tuple(addresses),
        secure=parsed.scheme == "https",
    )


async def is_public_url(url: str) -> bool:
    """True only when ``url`` is http(s) and every resolved address is public."""

    return await resolve_public_target(url) is not None
