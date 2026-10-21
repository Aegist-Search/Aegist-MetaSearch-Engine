import type { SearchEngine } from "@/lib/types";

/** Lowercase hostname without scheme, `www.`, path, port or trailing dot. */
export function normalizeDomain(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, "")
    .split(/[/?#]/, 1)[0]
    .split(":")[0]
    .replace(/^www\./, "")
    .replace(/\.$/, "");
}

/** Same shape the backend accepts for `domain`, so a bad value never leaves the UI. */
export function isDomain(value: string): boolean {
  return /^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/.test(
    value,
  );
}

/**
 * Single place where a search URL is built: the filters and the engine survive
 * pagination, a new query and an engine change.
 */
export function buildSearchHref({
  q,
  page = 1,
  engine = "auto",
  domain = "",
  lang = "",
}: {
  q: string;
  page?: number;
  engine?: SearchEngine;
  domain?: string;
  lang?: string;
}): string {
  const params = new URLSearchParams({ q, page: String(page) });
  if (engine !== "auto") {
    params.set("engine", engine);
  }
  if (domain) {
    params.set("domain", domain);
  }
  if (lang) {
    params.set("lang", lang);
  }
  return `/search?${params.toString()}`;
}
