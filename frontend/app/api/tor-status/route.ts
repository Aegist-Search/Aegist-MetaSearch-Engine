import type { TorStatus } from "@/lib/types";

/**
 * Same-origin proxy for the Tor status badge.
 *
 * The browser only talks to Next.js: the backend URL (and any error detail)
 * stays server-side. Failures are normalised to ``tor_active: false`` with a
 * reason code, so the badge can always render a definite state.
 */

export const dynamic = "force-dynamic";

const API_BASE = (process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
// The backend caches the answer and times out after TOR_TIMEOUT_S (5s default):
// leave a margin without making the badge hang.
const UPSTREAM_TIMEOUT_MS = 8_000;

function reply(payload: TorStatus): Response {
  return Response.json(payload, {
    status: 200,
    headers: {
      "Cache-Control": "no-store, max-age=0",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function offline(error: string): Response {
  return reply({ tor_active: false, exit_ip: null, error });
}

export async function GET(): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/v1/tor-status`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return offline("backend_unreachable");
  }

  if (!upstream.ok) {
    return offline(`backend_http_${upstream.status}`);
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return offline("backend_invalid_json");
  }

  if (typeof payload !== "object" || payload === null) {
    return offline("backend_invalid_payload");
  }

  const data = payload as Partial<TorStatus>;
  return reply({
    tor_active: data.tor_active === true,
    exit_ip: typeof data.exit_ip === "string" ? data.exit_ip : null,
    latency_ms: typeof data.latency_ms === "number" ? data.latency_ms : null,
    error: typeof data.error === "string" ? data.error : null,
  });
}
