"use client";

import { ChevronDown, Layers3 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

import { buildSearchHref } from "@/lib/search-params";
import type { ExternalSource, SearchEngine } from "@/lib/types";

const ENGINE_VALUES: SearchEngine[] = ["auto", "multi", "local", "brave", "exa", "serper", "tavily"];

export function EngineSelector({
  value,
  availableProviders,
  query,
  domain = "",
  lang = "",
}: {
  value: SearchEngine;
  availableProviders: ExternalSource[];
  query: string;
  domain?: string;
  lang?: string;
}) {
  const t = useTranslations("Engine");
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [selected, setSelected] = useState<SearchEngine>(value);

  useEffect(() => {
    setSelected(value);
  }, [value]);

  const labels: Record<SearchEngine, string> = {
    auto: t("auto"),
    multi: t("multi"),
    local: t("local"),
    brave: t("brave"),
    exa: t("exa"),
    serper: t("serper"),
    tavily: t("tavily"),
  };

  const options = ENGINE_VALUES.filter(
    (option) =>
      option === selected ||
      option === "auto" ||
      option === "multi" ||
      option === "local" ||
      availableProviders.includes(option as ExternalSource),
  );

  function handleChange(next: SearchEngine) {
    // Switching engine keeps the active local filters: they belong to the
    // query, not to the provider choice.
    const href = buildSearchHref({ q: query, engine: next, domain, lang });
    startTransition(() => {
      router.push(href);
    });
  }

  return (
    <label className="relative inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] font-medium text-foreground/80 transition-colors focus-within:border-primary/40 focus-within:bg-white/[0.06]">
      <Layers3 className="size-3.5 shrink-0 text-primary/80" aria-hidden="true" />
      <span className="sr-only">{t("label")}</span>
      <select
        value={selected}
        onChange={(event) => {
          const next = event.target.value as SearchEngine;
          setSelected(next);
          handleChange(next);
        }}
        disabled={isPending}
        aria-label={t("aria")}
        className="cursor-pointer appearance-none bg-transparent pr-5 text-[11px] font-medium text-foreground/80 outline-none disabled:cursor-wait disabled:opacity-60"
      >
        {options.map((option) => (
          <option key={option} value={option} className="bg-background text-foreground">
            {labels[option]}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2.5 size-3 text-muted-foreground/70"
        aria-hidden="true"
      />
    </label>
  );
}
