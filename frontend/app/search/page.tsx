import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { LanguageSwitcher } from "@/components/language-switcher";
import { Logo } from "@/components/logo";
import { ResultsSkeleton } from "@/components/results-skeleton";
import { SearchBar } from "@/components/search-bar";
import { SearchResults } from "@/components/search-results";
import { SiteFooter } from "@/components/site-footer";
import { isDomain, normalizeDomain } from "@/lib/search-params";
import type { SearchEngine } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Search");
  return { title: t("resultsHeading") };
}

type SearchPageProps = {
  searchParams: Promise<{
    q?: string | string[];
    page?: string | string[];
    engine?: string | string[];
    domain?: string | string[];
    lang?: string | string[];
  }>;
};

function firstValue(value: string | string[] | undefined): string {
  return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

export default async function SearchPage({ searchParams }: SearchPageProps) {
  const t = await getTranslations("Search");
  const params = await searchParams;
  const query = firstValue(params.q).replace(/\s+/g, " ").trim();

  if (!query) {
    redirect("/");
  }

  const parsedPage = Number.parseInt(firstValue(params.page), 10);
  const page = Number.isFinite(parsedPage) && parsedPage > 0 ? Math.min(parsedPage, 10) : 1;
  const rawEngine = firstValue(params.engine);
  const engine: SearchEngine = ["multi", "local", "brave", "exa", "serper", "tavily"].includes(
    rawEngine,
  )
    ? (rawEngine as SearchEngine)
    : "auto";

  // Filters are validated here as well as in the backend: an invalid value in
  // the URL must not turn into a 422 on a page the user can still use.
  const normalizedDomain = normalizeDomain(firstValue(params.domain));
  const domain = normalizedDomain && isDomain(normalizedDomain) ? normalizedDomain : "";
  const rawLang = firstValue(params.lang).trim().toLowerCase();
  const lang = /^[a-z]{2,3}$/.test(rawLang) ? rawLang : "";

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-background/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-4xl items-center gap-3 px-4 py-3 sm:gap-5 sm:px-6">
          <Logo compact />
          <div className="min-w-0 flex-1">
            <SearchBar
              key={`${query}:${engine}:${domain}:${lang}`}
              defaultValue={query}
              engine={engine}
              domain={domain}
              lang={lang}
            />
          </div>
          <LanguageSwitcher compact />
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <h1 className="sr-only">{t("resultsFor", { query })}</h1>
        <Suspense
          key={`${query}:${page}:${engine}:${domain}:${lang}`}
          fallback={<ResultsSkeleton />}
        >
          <SearchResults query={query} page={page} engine={engine} domain={domain} lang={lang} />
        </Suspense>
      </main>

      <SiteFooter className="border-t-0" />
    </div>
  );
}
