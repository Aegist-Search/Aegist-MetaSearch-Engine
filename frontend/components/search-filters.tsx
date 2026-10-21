"use client";

import { SlidersHorizontal, X } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, useTransition, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { locales } from "@/i18n/config";
import { buildSearchHref, isDomain, normalizeDomain } from "@/lib/search-params";
import type { SearchEngine } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Domain and language filters.
 *
 * Both are local-index filters: the backend applies them to Meilisearch and
 * never forwards them to an external provider. They live in the URL, so they
 * survive pagination, a new query and an engine change.
 */
export function SearchFilters({
  query,
  engine = "auto",
  domain = "",
  lang = "",
}: {
  query: string;
  engine?: SearchEngine;
  domain?: string;
  lang?: string;
}) {
  const t = useTranslations("Filters");
  const locale = useLocale();
  const router = useRouter();
  const [domainValue, setDomainValue] = useState(domain);
  const [langValue, setLangValue] = useState(lang);
  const [invalid, setInvalid] = useState(false);
  const [isPending, startTransition] = useTransition();
  const domainId = useId();
  const langId = useId();

  useEffect(() => {
    setDomainValue(domain);
    setLangValue(lang);
  }, [domain, lang]);

  const languageNames = new Intl.DisplayNames([locale], { type: "language" });
  const active = Boolean(domain || lang);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleaned = normalizeDomain(domainValue);
    if (cleaned && !isDomain(cleaned)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    startTransition(() => {
      router.push(buildSearchHref({ q: query, engine, domain: cleaned, lang: langValue }));
    });
  }

  function clear() {
    setDomainValue("");
    setLangValue("");
    setInvalid(false);
    startTransition(() => {
      router.push(buildSearchHref({ q: query, engine }));
    });
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label={t("label")}
      className="mb-4 flex flex-wrap items-end gap-2 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5"
    >
      <SlidersHorizontal
        className="mb-2 size-3.5 shrink-0 text-primary/70"
        aria-hidden="true"
      />

      <div className="min-w-0 flex-1 sm:max-w-[15rem]">
        <label
          htmlFor={domainId}
          className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-muted-foreground/60"
        >
          {t("domain")}
        </label>
        <Input
          id={domainId}
          value={domainValue}
          onChange={(event) => {
            setDomainValue(event.target.value);
            setInvalid(false);
          }}
          placeholder={t("domainPlaceholder")}
          inputMode="url"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          maxLength={253}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${domainId}-error` : undefined}
          className={cn("h-8 text-xs", invalid && "border-destructive/60")}
        />
      </div>

      <div>
        <label
          htmlFor={langId}
          className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-muted-foreground/60"
        >
          {t("language")}
        </label>
        <select
          id={langId}
          value={langValue}
          onChange={(event) => setLangValue(event.target.value)}
          className="h-8 rounded-lg border border-input bg-white/[0.04] px-2 text-xs text-foreground outline-none focus-visible:border-ring/60 focus-visible:ring-4 focus-visible:ring-ring/20"
        >
          <option value="" className="bg-background text-foreground">
            {t("anyLanguage")}
          </option>
          {locales.map((code) => (
            <option key={code} value={code} className="bg-background text-foreground">
              {languageNames.of(code) ?? code.toUpperCase()}
            </option>
          ))}
        </select>
      </div>

      <Button type="submit" size="sm" variant="outline" disabled={isPending} className="h-8">
        {t("apply")}
      </Button>

      {active ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={clear}
          disabled={isPending}
          className="h-8 gap-1 text-muted-foreground"
        >
          <X className="size-3" aria-hidden="true" />
          {t("clear")}
        </Button>
      ) : null}

      {invalid ? (
        <p id={`${domainId}-error`} role="alert" className="w-full text-[11px] text-destructive">
          {t("invalidDomain")}
        </p>
      ) : null}
    </form>
  );
}
