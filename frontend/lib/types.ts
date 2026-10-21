export type ResultSource = "local" | "brave" | "exa" | "serper" | "tavily";
export type ExternalSource = Exclude<ResultSource, "local">;
export type SearchEngine = "auto" | "multi" | "local" | ExternalSource;

export interface HighlightSegment {
  text: string;
  /** True when the query matched this run and it should be highlighted. */
  match: boolean;
}

export interface SearchResultItem {
  id: string;
  title: string;
  url: string;
  snippet: string;
  title_highlight?: HighlightSegment[] | null;
  snippet_highlight?: HighlightSegment[] | null;
  extra_snippets?: string[];
  source: ResultSource;
  domain: string;
  h1?: string | null;
  rank?: number | null;
  image?: string | null;
  /** Unix timestamp (seconds) of the crawl that indexed the page (local results). */
  crawled_at?: number | null;
}

export interface SearchPagination {
  page: number;
  page_size: number;
  has_more: boolean;
  total: number | null;
  total_pages: number | null;
}

export interface SearchInsight {
  source: ResultSource;
  title: string;
  text: string;
  url?: string | null;
}

export interface SearchMeta {
  took_ms: number;
  local_hits: number;
  brave_hits: number;
  external_hits?: number;
  provider_hits?: Partial<Record<ResultSource, number>>;
  /** Providers that failed or were skipped after repeated failures (names only). */
  provider_errors?: ExternalSource[];
  /** True when the response is empty and the local index has no documents. */
  local_index_empty?: boolean;
  /** Corrected query proposed for an empty page that looks like a typo. */
  query_suggestion?: string | null;
  insights?: SearchInsight[];
  degraded: boolean;
  /** True when the backend queued a background Katana crawl for this search. */
  crawl_scheduled?: boolean;
}

export interface SearchResponse {
  query: string;
  engine?: SearchEngine;
  source: ResultSource;
  sources?: ResultSource[];
  available_providers?: ExternalSource[];
  results: SearchResultItem[];
  pagination: SearchPagination;
  meta: SearchMeta;
}

export interface SearchSuggestion {
  title: string;
  url: string;
  domain: string;
}

export interface SuggestResponse {
  query: string;
  suggestions: SearchSuggestion[];
}

export interface TorStatus {
  tor_active: boolean;
  exit_ip: string | null;
  latency_ms?: number | null;
  error?: string | null;
}
