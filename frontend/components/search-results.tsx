import { Activity, ArrowUpRight, Database, Globe2, Info, Radar, SearchX, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";

import { EngineSelector } from "@/components/engine-selector";
import { ErrorPanel } from "@/components/error-panel";
import { Pagination } from "@/components/pagination";
import { ResultCard } from "@/components/result-card";
import { SearchFilters } from "@/components/search-filters";
import { Badge } from "@/components/ui/badge";
import { SearchApiError, searchWeb } from "@/lib/api";
import { buildSearchHref } from "@/lib/search-params";
import type { ResultSource, SearchEngine, SearchMeta, SearchResponse } from "@/lib/types";

type ExternalSource = Exclude<ResultSource, "local">;

const EXTERNAL_SOURCE_LABELS: Record<ExternalSource, string> = {
  brave: "Brave Search",
  exa: "Exa",
  serper: "Serper",
  tavily: "Tavily",
};

const EXTERNAL_SOURCE_TONES: Record<ExternalSource, string> = {
  brave: "border-amber-400/20 bg-amber-400/10 text-amber-200",
  exa: "border-violet-400/20 bg-violet-400/10 text-violet-200",
  serper: "border-sky-400/20 bg-sky-400/10 text-sky-200",
  tavily: "border-teal-400/20 bg-teal-400/10 text-teal-200",
};

function sourceTone(source: ResultSource): string {
  return source === "local"
    ? "border-primary/20 bg-primary/10 text-primary"
    : EXTERNAL_SOURCE_TONES[source];
}

function SourcePill({ source }: { source: ResultSource }) {
  const tSources = useTranslations("Sources");

  if (source === "local") {
    return (
      <Badge
        variant="outline"
        className="gap-1.5 border-primary/20 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary"
      >
        <Database className="size-3" aria-hidden="true" />
        {tSources("local")}
      </Badge>
    );
  }

  return (
    <Badge
      variant="outline"
      className={`gap-1.5 px-2.5 py-1 text-[11px] font-medium ${EXTERNAL_SOURCE_TONES[source]}`}
    >
      <Globe2 className="size-3" aria-hidden="true" />
      {EXTERNAL_SOURCE_LABELS[source]}
    </Badge>
  );
}

function ProviderHits({ meta }: { meta: SearchMeta }) {
  const t = useTranslations("Search");
  const tSources = useTranslations("Sources");
  const entries = Object.entries(meta.provider_hits ?? {}) as [string, number][];
  const errors = (meta.provider_errors ?? []) as ExternalSource[];
  if (entries.length === 0 && errors.length === 0) {
    return null;
  }

  const errorNames = errors
    .map((name) => EXTERNAL_SOURCE_LABELS[name] ?? name)
    .join(", ");

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground/60">
      {entries.length > 0 ? (
        <>
          <Activity className="size-3 text-primary/70" aria-hidden="true" />
          <span className="mr-0.5 font-medium uppercase tracking-wide text-muted-foreground/50">
            {t("provider")}
          </span>
          {entries.map(([name, count]) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded-md border border-white/[0.07] bg-white/[0.03] px-1.5 py-0.5"
            >
              <span className="text-foreground/70">
                {name === "local"
                  ? tSources("local")
                  : (EXTERNAL_SOURCE_LABELS[name as ExternalSource] ?? name)}
              </span>
              <span className="font-mono text-muted-foreground/50">{count}</span>
            </span>
          ))}
        </>
      ) : null}
      {errors.length > 0 ? (
        <span className="inline-flex items-center gap-1 rounded-md border border-amber-400/20 bg-amber-400/10 px-1.5 py-0.5 text-amber-200/80">
          <Info className="size-3 shrink-0" aria-hidden="true" />
          {t("providerErrors", { providers: errorNames })}
        </span>
      ) : null}
    </div>
  );
}

function EmptyState({
  query,
  indexEmpty,
  suggestion,
  engine,
  domain,
  lang,
}: {
  query: string;
  indexEmpty: boolean;
  suggestion: string | null;
  engine: SearchEngine;
  domain: string;
  lang: string;
}) {
  const t = useTranslations("Search");
  const suggestionHref = suggestion
    ? buildSearchHref({ q: suggestion, engine, domain, lang })
    : null;

  return (
    <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-6 py-16 text-center">
      <span className="mx-auto grid size-12 place-items-center rounded-full bg-white/[0.05] text-muted-foreground ring-1 ring-white/10">
        <SearchX className="size-5" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-base font-semibold">
        {t(indexEmpty ? "emptyIndexTitle" : "noResults", { query })}
      </h2>
      {suggestionHref && suggestion ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {t("didYouMean")}{" "}
          <a
            href={suggestionHref}
            className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            {suggestion}
          </a>
        </p>
      ) : null}
      {indexEmpty ? (
        <div className="mx-auto mt-4 max-w-lg rounded-xl border border-primary/20 bg-primary/[0.05] px-4 py-3 text-left">
          <p className="text-xs leading-relaxed text-primary/80">{t("emptyIndexHint")}</p>
          <code className="mt-2 block overflow-x-auto rounded-md bg-black/30 px-2 py-1.5 font-mono text-[11px] text-primary/80">
            docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl
          </code>
        </div>
      ) : (
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
          {t("noResultsHint")}
        </p>
      )}
    </div>
  );
}

export async function SearchResults({
  query,
  page,
  engine,
  domain = "",
  lang = "",
}: {
  query: string;
  page: number;
  engine: SearchEngine;
  domain?: string;
  lang?: string;
}) {
  const t = await getTranslations("Search");
  const tErrors = await getTranslations("Search.errors");
  const tSources = await getTranslations("Sources");
  const locale = await getLocale();

  let data: SearchResponse;
  try {
    data = await searchWeb({ q: query, page, engine, domain, lang });
  } catch (error) {
    let message = tErrors("unexpected");
    if (error instanceof SearchApiError) {
      switch (error.code) {
        case "unreachable":
          message = tErrors("unreachable");
          break;
        case "unavailable":
          message = tErrors("unavailable");
          break;
        case "invalid_query":
          message = tErrors("invalidQuery");
          break;
        case "http":
          message = tErrors("http", { status: error.status ?? 0 });
          break;
        case "invalid_response":
          message = tErrors("invalidResponse");
          break;
      }
    }
    return (
      <ErrorPanel
        message={message}
        query={query}
        page={page}
        engine={engine}
        domain={domain}
        lang={lang}
      />
    );
  }

  const {
    results,
    pagination,
    source,
    sources,
    meta,
    engine: responseEngine,
    available_providers: availableProviders,
  } = data;
  const activeEngine = responseEngine ?? engine;
  const activeSources =
    sources && sources.length > 0 ? sources : results.length > 0 ? [source] : [];
  const insights = meta.insights ?? [];
  const total =
    typeof pagination.total === "number" &&
    pagination.total > 0 &&
    (pagination.page > 1 || pagination.total > results.length)
      ? new Intl.NumberFormat(locale).format(pagination.total)
      : null;

  return (
    <section aria-labelledby="results-heading">
      <h2 id="results-heading" className="sr-only">
        {t("resultsHeading")}
      </h2>

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
          <span>{t("resultsCount", { count: results.length })}</span>
          {total ? (
            <>
              <span className="text-white/20">·</span>
              <span>{t("approxTotal", { total })}</span>
            </>
          ) : null}
          <span className="text-white/20">·</span>
          <span className="font-mono text-[11px] text-muted-foreground/60">
            {meta.took_ms} ms
          </span>
        </div>
        <EngineSelector
          value={activeEngine}
          availableProviders={availableProviders ?? []}
          query={query}
          domain={domain}
          lang={lang}
        />
      </div>

      <SearchFilters query={query} engine={activeEngine} domain={domain} lang={lang} />

      {activeSources.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground/50">
            {t("sources")}
          </span>
          {activeSources.map((item) => (
            <SourcePill key={item} source={item} />
          ))}
        </div>
      ) : null}

      <ProviderHits meta={meta} />

      {meta.crawl_scheduled ? (
        <p
          role="status"
          className="mb-5 mt-3 flex items-start gap-2 rounded-xl border border-primary/20 bg-primary/[0.06] px-3 py-2.5 text-xs leading-relaxed text-primary/80"
        >
          <Radar
            className="mt-0.5 size-3.5 shrink-0 animate-pulse motion-reduce:animate-none"
            aria-hidden="true"
          />
          <span>{t("crawlScheduled")}</span>
        </p>
      ) : null}

      {insights.length > 0 ? (
        <div className="mb-5 grid gap-2.5">
          <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/60">
            <Sparkles className="size-3 text-primary/70" aria-hidden="true" />
            {t("insights")}
          </p>
          {insights.map((insight, index) => (
            <article
              key={`${insight.source}-${index}`}
              className="rounded-2xl border border-primary/20 bg-primary/5 p-4"
            >
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Sparkles className="size-3.5 shrink-0 text-primary" aria-hidden="true" />
                <span className="font-medium text-foreground/80">{insight.title}</span>
                <Badge
                  variant="outline"
                  className={`ml-auto shrink-0 gap-1 border px-2 py-0 text-[10px] font-semibold uppercase tracking-wide ${sourceTone(insight.source)}`}
                >
                  {tSources(insight.source)}
                </Badge>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {insight.text}
              </p>
              {insight.url ? (
                <a
                  href={insight.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  referrerPolicy="no-referrer"
                  className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary/80 underline-offset-4 hover:text-primary hover:underline"
                >
                  {t("openSource")}
                  <ArrowUpRight className="size-3" aria-hidden="true" />
                </a>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}

      {results.length === 0 ? (
        <EmptyState
          query={query}
          indexEmpty={meta.local_index_empty === true}
          suggestion={meta.query_suggestion ?? null}
          engine={activeEngine}
          domain={domain}
          lang={lang}
        />
      ) : (
        <ol className="space-y-3">
          {results.map((result) => (
            <li key={`${result.source}-${result.id}`}>
              <ResultCard result={result} />
            </li>
          ))}
        </ol>
      )}

      <Pagination
        query={query}
        page={pagination.page}
        hasMore={pagination.has_more}
        engine={activeEngine}
        domain={domain}
        lang={lang}
      />

      {meta.degraded ? (
        <p className="mt-6 flex items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2.5 text-xs leading-relaxed text-amber-100/80">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {t("degraded")}
        </p>
      ) : null}
    </section>
  );
}
