import type { NextRequest } from "next/server";

/**
 * Same-origin proxy for result previews.
 *
 * The browser only ever talks to Next.js: the backend URL stays server-side and
 * every safety check (public host only, size cap, image content type) lives in
 * the FastAPI endpoint.
 */

export const dynamic = "force-dynamic";

const API_BASE = (process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const MAX_URL_LENGTH = 2048;
const UPSTREAM_TIMEOUT_MS = 10_000;

function noContent(status: number): Response {
  return new Response(null, {
    status,
    headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

export async function GET(request: NextRequest): Promise<Response> {
  const target = (request.nextUrl.searchParams.get("url") ?? "").trim();

  if (target.length < 8 || target.length > MAX_URL_LENGTH || !/^https?:\/\//i.test(target)) {
    return noContent(400);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/v1/preview?url=${encodeURIComponent(target)}`, {
      cache: "no-store",
      headers: { Accept: "image/*" },
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return noContent(502);
  }

  if (!upstream.ok || !upstream.body) {
    return noContent(404);
  }

  const contentType = (upstream.headers.get("content-type") ?? "").toLowerCase();
  if (!contentType.startsWith("image/")) {
    return noContent(404);
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=86400, immutable",
      "Content-Disposition": "inline",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
