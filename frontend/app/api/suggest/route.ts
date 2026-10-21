import type { NextRequest } from "next/server";

import type { SearchSuggestion, SuggestResponse } from "@/lib/types";

/**
 * Same-origin proxy for the autocomplete dropdown.
 *
 * The browser only talks to Next.js, and the backend endpoint behind it is
 * local-only: a keystroke never becomes a request to an external provider.
 * Failures are normalised to an empty list, so typing never shows an error.
 */

export const dynamic = "force-dynamic";

const API_BASE = (process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const MAX_QUERY_LENGTH = 80;
const UPSTREAM_TIMEOUT_MS = 4_000;

function reply(payload: SuggestResponse): Response {
  return Response.json(payload, {
    status: 200,
    headers: {
      "Cache-Control": "no-store, max-age=0",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function empty(query: string): Response {
  return reply({ query, suggestions: [] });
}

function isSuggestion(value: unknown): value is SearchSuggestion {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<SearchSuggestion>;
  return (
    typeof item.title === "string" && typeof item.url === "string" && typeof item.domain === "string"
  );
}

export async function GET(request: NextRequest): Promise<Response> {
  const query = (request.nextUrl.searchParams.get("q") ?? "").replace(/\s+/g, " ").trim();

  if (!query) {
    return empty("");
  }
  if (query.length > MAX_QUERY_LENGTH) {
    return empty(query.slice(0, MAX_QUERY_LENGTH));
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/v1/suggest?q=${encodeURIComponent(query)}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return empty(query);
  }

  if (!upstream.ok) {
    return empty(query);
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return empty(query);
  }

  if (typeof payload !== "object" || payload === null) {
    return empty(query);
  }

  const data = payload as { suggestions?: unknown };
  const suggestions = Array.isArray(data.suggestions)
    ? data.suggestions.filter(isSuggestion).slice(0, 6)
    : [];

  return reply({ query, suggestions });
}
