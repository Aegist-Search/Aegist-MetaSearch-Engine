import Link from "next/link";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button, buttonVariants } from "@/components/ui/button";
import { buildSearchHref } from "@/lib/search-params";
import { cn } from "@/lib/utils";
import type { SearchEngine } from "@/lib/types";

export function Pagination({
  query,
  page,
  hasMore,
  engine = "auto",
  domain = "",
  lang = "",
}: {
  query: string;
  page: number;
  hasMore: boolean;
  engine?: SearchEngine;
  domain?: string;
  lang?: string;
}) {
  const t = useTranslations("Search.pagination");

  if (page <= 1 && !hasMore) return null;

  const hrefFor = (target: number) =>
    buildSearchHref({ q: query, page: target, engine, domain, lang });

  return (
    <nav
      aria-label={t("aria")}
      className="mt-9 flex items-center justify-between gap-4 border-t border-white/[0.06] pt-5"
    >
      {page > 1 ? (
        <Link
          href={hrefFor(page - 1)}
          prefetch={false}
          rel="prev"
          aria-label={t("previousAria")}
          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
        >
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">{t("previous")}</span>
        </Link>
      ) : (
        <Button variant="outline" size="sm" disabled aria-label={t("previousAria")}>
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">{t("previous")}</span>
        </Button>
      )}

      <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 font-mono text-[11px] text-muted-foreground">
        {t("page", { page })}
      </span>

      {hasMore ? (
        <Link
          href={hrefFor(page + 1)}
          prefetch={false}
          rel="next"
          aria-label={t("nextAria")}
          className={cn(buttonVariants({ size: "sm" }))}
        >
          <span className="hidden sm:inline">{t("next")}</span>
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Link>
      ) : (
        <Button variant="outline" size="sm" disabled aria-label={t("nextAria")}>
          <span className="hidden sm:inline">{t("next")}</span>
          <ArrowRight className="size-3.5" aria-hidden="true" />
        </Button>
      )}
    </nav>
  );
}
