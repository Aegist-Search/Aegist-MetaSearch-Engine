import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";

export function ResultsSkeleton() {
  const t = useTranslations("Search");

  return (
    <div aria-busy="true" aria-live="polite" className="space-y-4">
      <span className="sr-only">{t("loadingResults")}</span>

      <div className="mb-5 flex items-center justify-between gap-4">
        <Skeleton className="h-4 w-36" />
        <Skeleton className="h-6 w-28 rounded-full" />
      </div>

      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={index}
          className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4 sm:p-5"
        >
          <div className="flex gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Skeleton className="h-3 w-28" />
                <Skeleton className="h-3 w-16" />
                <Skeleton className="ml-auto h-4 w-14 rounded-full" />
              </div>
              <Skeleton className="mt-3 h-5 w-3/4" />
              <Skeleton className="mt-2.5 h-3.5 w-full" />
              <Skeleton className="mt-1.5 h-3.5 w-11/12" />
              <Skeleton className="mt-1.5 h-3.5 w-2/3" />
            </div>
            {/* Matches ResultPreview: same box so the layout does not jump. */}
            <Skeleton className="mt-1 h-20 w-24 shrink-0 rounded-xl sm:h-[5.5rem] sm:w-32" />
          </div>
        </div>
      ))}
    </div>
  );
}
