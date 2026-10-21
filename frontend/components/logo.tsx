import Link from "next/link";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

export function Logo({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const t = useTranslations("Logo");

  return (
    <Link
      href="/"
      aria-label={t("home")}
      className={cn(
        "inline-flex shrink-0 items-center gap-2.5 rounded-lg outline-none transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:ring-ring/50",
        className,
      )}
    >
      <span className="grid size-8 place-items-center rounded-xl bg-linear-to-br from-primary to-teal-400 text-primary-foreground shadow-[0_0_24px_-7px_var(--primary)] ring-1 ring-white/10">
        <svg viewBox="0 0 24 24" fill="none" className="size-[18px]" aria-hidden="true">
          <path
            d="M12 3 5 6v5.2c0 4.4 2.9 8.4 7 9.8 4.1-1.4 7-5.4 7-9.8V6l-7-3Z"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
          <path
            d="m9.2 11.8 2 2 3.6-4"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className={cn("text-lg font-semibold tracking-tight", compact && "hidden sm:inline")}>
        Aegis
      </span>
    </Link>
  );
}
