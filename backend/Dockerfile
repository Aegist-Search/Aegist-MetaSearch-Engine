# Katana is fetched from its official release so the on-demand crawler can
# spawn it for background crawls. Pin the version for reproducible images;
# override with --build-arg KATANA_VERSION=x.y.z when needed.
# Debian (not Alpine) so the released binary shares the runtime glibc.
FROM debian:bookworm-slim AS katana
ARG KATANA_VERSION=1.8.0
ARG TARGETARCH=amd64
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/katana.zip \
       "https://github.com/projectdiscovery/katana/releases/download/v${KATANA_VERSION}/katana_${KATANA_VERSION}_linux_${TARGETARCH}.zip" \
    && unzip -q /tmp/katana.zip -d /katana katana \
    && chmod 0755 /katana/katana

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY --from=katana /katana/katana /usr/local/bin/katana

RUN useradd --create-home --uid 10001 aegis \
    && chown -R aegis:aegis /app \
    && mkdir -p /data \
    && chown aegis:aegis /data
USER aegis

EXPOSE 8000

# --no-access-log is mandatory: access logs contain client IPs and queries.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
