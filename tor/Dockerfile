# Tor SOCKS5 proxy for the backend's Tor diagnostics.
#
# Built from Debian instead of a third-party image so the Tor daemon comes
# from the distro package and the container stays reproducible. The SocksPort
# is NOT published on the host: it is reachable from the Compose network, not
# from the LAN.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tor \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/tor \
    && chmod 700 /var/lib/tor \
    && chown -R debian-tor:debian-tor /var/lib/tor

COPY torrc /etc/tor/torrc

USER debian-tor

EXPOSE 9050

# No access log of our own: Tor writes only its bootstrapping notices.
ENTRYPOINT ["tor", "-f", "/etc/tor/torrc"]
