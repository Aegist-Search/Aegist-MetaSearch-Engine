import type { SearchEngine, SearchResponse } from "@/lib/types";

/**
 * Server-only client. This module must only be imported from Server Components:
 * the backend URL stays on the server and no query is ever cached.
 */
const API_BASE = (process.env.SEARCH_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

export type SearchApiErrorCode =
  | "unreachable"
  | "unavailable"
  | "invalid_query"
  | "http"
  | "invalid_response";

export class SearchApiError extends Error {
  readonly code: SearchApiErrorCode;
  readonly status?: number;

  constructor(message: string, code: SearchApiErrorCode, status?: number, options?: ErrorOptions) {
    super(message, options);
    this.name = "SearchApiError";
    this.code = code;
    this.status = status;
  }
}

export type SearchSort = "relevance" | "crawled_at:desc";

export async function searchWeb({
  q,
  page = 1,
  engine = "auto",
  sort = "relevance",
  domain,
  lang,
}: {
  q: string;
  page?: number;
  engine?: SearchEngine;
  sort?: SearchSort;
  domain?: string;
  lang?: string;
}): Promise<SearchResponse> {
  const url = new URL(`${API_BASE}/api/v1/search`);
  url.searchParams.set("q", q);
  url.searchParams.set("page", String(page));
  if (engine !== "auto") {
    url.searchParams.set("engine", engine);
  }
  if (sort !== "relevance") {
    url.searchParams.set("sort", sort);
  }
  // Local-index filters: the backend validates them again and never forwards
  // them to an external provider.
  if (domain) {
    url.searchParams.set("domain", domain);
  }
  if (lang) {
    url.searchParams.set("lang", lang);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    throw new SearchApiError("Backend unreachable", "unreachable");
  }

  if (!response.ok) {
    if (response.status === 503) {
      throw new SearchApiError("Search sources unavailable", "unavailable");
    }
    if (response.status === 422) {
      throw new SearchApiError("Invalid query", "invalid_query");
    }
    throw new SearchApiError("Unexpected search service status", "http", response.status);
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    throw new SearchApiError("Unreadable search response", "invalid_response");
  }

  const data = payload as SearchResponse | null;
  if (!data || !Array.isArray(data.results) || !data.pagination) {
    throw new SearchApiError("Unexpected search payload", "invalid_response");
  }

  return data;
}
