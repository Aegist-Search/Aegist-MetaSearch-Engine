import { useTranslations } from "next-intl";

import { LanguageSwitcher } from "@/components/language-switcher";
import { Logo } from "@/components/logo";
import { ResultsSkeleton } from "@/components/results-skeleton";
import { Skeleton } from "@/components/ui/skeleton";

export default function SearchLoading() {
  const t = useTranslations("Search");

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-white/[0.07] bg-background/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-4xl items-center gap-3 px-4 py-3 sm:gap-5 sm:px-6">
          <Logo compact />
          <div className="min-w-0 flex-1">
            <Skeleton className="h-11 w-full rounded-xl" />
          </div>
          <LanguageSwitcher compact />
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <h1 className="sr-only">{t("loading")}</h1>
        <ResultsSkeleton />
      </main>
    </div>
  );
}
