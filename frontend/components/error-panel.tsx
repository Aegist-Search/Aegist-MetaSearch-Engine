import { AlertTriangle, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";

import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { buildSearchHref } from "@/lib/search-params";
import type { SearchEngine } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ErrorPanel({
  message,
  query,
  page,
  engine = "auto",
  domain = "",
  lang = "",
}: {
  message: string;
  query: string;
  page: number;
  engine?: SearchEngine;
  domain?: string;
  lang?: string;
}) {
  const t = useTranslations("Search.errors");
  const retryHref = buildSearchHref({ q: query, page, engine, domain, lang });

  return (
    <Card
      role="alert"
      className="items-center gap-3 rounded-2xl border-destructive/25 bg-destructive/[0.06] px-6 py-12 text-center shadow-none"
    >
      <span className="grid size-12 place-items-center rounded-full bg-destructive/10 text-destructive ring-1 ring-destructive/20">
        <AlertTriangle className="size-5" aria-hidden="true" />
      </span>
      <h2 className="text-base font-semibold">{t("title")}</h2>
      <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">{message}</p>
      <a
        href={retryHref}
        className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-2")}
      >
        <RotateCcw className="size-3.5" aria-hidden="true" />
        {t("retry")}
      </a>
    </Card>
  );
}
