import { Fragment } from "react";

import { Clock, ExternalLink } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ResultPreview } from "@/components/result-preview";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { HighlightSegment, ResultSource, SearchResultItem } from "@/lib/types";

const SOURCE_TONES: Record<ResultSource, string> = {
  local: "border-primary/20 bg-primary/10 text-primary",
  brave: "border-amber-400/20 bg-amber-400/10 text-amber-200",
  exa: "border-violet-400/20 bg-violet-400/10 text-violet-200",
  serper: "border-sky-400/20 bg-sky-400/10 text-sky-200",
  tavily: "border-teal-400/20 bg-teal-400/10 text-teal-200",
};

// Provider snippets are plain text with occasional Markdown, but some APIs
// still return escaped HTML markup. ReactMarkdown never renders raw HTML
// (no rehype-raw here), so leftover tags would otherwise be shown literally.
// Strip the residual markup, turning breaks into real Markdown line breaks.
const SCRIPT_OR_STYLE_RE = /<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi;
const HORIZONTAL_RULE_RE = /<hr\s*\/?>/gi;
const LINE_BREAK_RE = /<br\s*\/?>/gi;
const BLOCK_BREAK_RE = /<\/(?:p|div|li|tr|h[1-6]|blockquote)>/gi;
const HTML_TAG_RE = /<\/?[a-z][a-z0-9-]*(?:\s[^<>]*)?\/?>/gi;

function normalizeMarkdown(value: string): string {
  return value
    .replace(SCRIPT_OR_STYLE_RE, " ")
    .replace(HORIZONTAL_RULE_RE, "\n\n---\n\n")
    .replace(LINE_BREAK_RE, "\\\n")
    .replace(BLOCK_BREAK_RE, "\n")
    .replace(HTML_TAG_RE, "")
    .replace(/\r\n?/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const MARKDOWN_COMPONENTS: Components = {
  a({ node: _node, href, children, ...props }) {
    if (!href) {
      return <>{children}</>;
    }

    // GFM footnote anchors stay in place; every other link (including
    // relative ones) opens in a new tab without leaking a referrer.
    const isFragment = href.startsWith("#");

    return (
      <a
        {...props}
        href={href}
        target={isFragment ? undefined : "_blank"}
        rel="noreferrer noopener"
        referrerPolicy="no-referrer"
      >
        {children}
      </a>
    );
  },
  // GFM tables can be wider than the card: wrap them so they scroll instead
  // of stretching the layout.
  table({ node: _node, children, ...props }) {
    return (
      <div className="markdown-table-wrap">
        <table {...props}>{children}</table>
      </div>
    );
  },
  // Never load remote images embedded in provider text: the browser must not
  // contact third parties (tracking pixels, referrer leaks).
  img: () => null,
};

function splitUrl(url: string): { domain: string; path: string } {
  try {
    const parsed = new URL(url);
    const domain = parsed.hostname.replace(/^www\./, "");
    const path = `${parsed.pathname === "/" ? "" : parsed.pathname}${parsed.search}`;
    return {
      domain,
      path: path.length > 64 ? `${path.slice(0, 61)}…` : path,
    };
  } catch {
    return { domain: url, path: "" };
  }
}

// The backend splits highlighted text into plain runs: React builds the
// markup, so indexed page text is never injected as HTML.
function HighlightedText({ segments }: { segments?: HighlightSegment[] | null }) {
  if (!segments?.length) {
    return null;
  }

  return (
    <>
      {segments.map((segment, index) =>
        segment.match ? (
          <mark
            key={index}
            className="rounded-sm bg-primary/20 px-0.5 text-inherit [box-decoration-break:clone]"
          >
            {segment.text}
          </mark>
        ) : (
          <Fragment key={index}>{segment.text}</Fragment>
        ),
      )}
    </>
  );
}

function MarkdownText({ text, className }: { text: string; className?: string }) {
  const normalized = normalizeMarkdown(text);
  if (!normalized) {
    return null;
  }

  return (
    <div className={cn("markdown-snippet", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {normalized}
      </ReactMarkdown>
    </div>
  );
}

export function ResultCard({ result }: { result: SearchResultItem }) {
  const tSources = useTranslations("Sources");
  const tSearch = useTranslations("Search");
  const format = useFormatter();
  const { domain, path } = splitUrl(result.url);
  const extraSnippets = (result.extra_snippets ?? [])
    .map((snippet) => normalizeMarkdown(snippet))
    .filter((snippet) => snippet.length > 0);
  // crawled_at is a Unix timestamp in seconds; the label is rendered by the
  // server in the active locale, so no extra client code is needed.
  const crawledAt =
    typeof result.crawled_at === "number" && result.crawled_at > 0
      ? new Date(result.crawled_at * 1000)
      : null;

  return (
    <Card className="group/card gap-0 rounded-2xl border-white/[0.08] bg-white/[0.025] p-4 shadow-none transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/25 hover:bg-white/[0.045] sm:p-5">
      <div className="flex gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2 text-xs">
            <span className="truncate font-medium text-foreground/70">{domain}</span>
            {path ? (
              <>
                <span aria-hidden="true" className="text-white/10">
                  /
                </span>
                <span className="truncate text-muted-foreground/60">{path}</span>
              </>
            ) : null}
            <Badge
              variant="outline"
              className={cn(
                "ml-auto shrink-0 gap-1 border px-2 py-0 text-[10px] font-semibold uppercase tracking-wide",
                SOURCE_TONES[result.source],
              )}
            >
              {tSources(result.source)}
            </Badge>
          </div>

          <h2 className="mt-2.5 text-[17px] font-medium leading-snug">
            <a
              href={result.url}
              data-result-link=""
              target="_blank"
              rel="noreferrer noopener"
              referrerPolicy="no-referrer"
              className="inline-flex items-baseline gap-1 rounded-sm text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            >
              <span className="line-clamp-2">
                {result.title_highlight?.length ? (
                  <HighlightedText segments={result.title_highlight} />
                ) : (
                  result.title
                )}
              </span>
              <ExternalLink
                className="size-3.5 shrink-0 translate-y-0.5 opacity-0 transition-opacity group-hover/card:opacity-60"
                aria-hidden="true"
              />
              <span className="sr-only">{tSearch("opensInNewTab")}</span>
            </a>
          </h2>

          {result.snippet ? (
            result.snippet_highlight?.length ? (
              <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
                <HighlightedText segments={result.snippet_highlight} />
              </p>
            ) : (
              <MarkdownText
                text={result.snippet}
                className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-muted-foreground"
              />
            )
          ) : null}

          {crawledAt ? (
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
              <Clock className="size-3 shrink-0 opacity-70" aria-hidden="true" />
              <span>
                {tSearch("updatedOn", {
                  date: format.dateTime(crawledAt, { dateStyle: "medium" }),
                })}
              </span>
            </p>
          ) : null}

          {extraSnippets.length > 0 ? (
            <details className="mt-3 text-xs text-muted-foreground">
              <summary className="w-fit cursor-pointer select-none rounded-sm font-medium text-primary/80 marker:text-primary/50 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
                {tSearch("extraSnippets", { count: extraSnippets.length })}
              </summary>
              <ul className="mt-2 space-y-2 border-l border-primary/20 pl-3">
                {extraSnippets.map((snippet, index) => (
                  <li key={`${result.id}-extra-${index}`} className="leading-relaxed">
                    <MarkdownText text={snippet} />
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>

        <ResultPreview
          image={result.image}
          title={result.title}
          url={result.url}
          domain={domain}
          snippet={result.snippet}
        />
      </div>
    </Card>
  );
}
