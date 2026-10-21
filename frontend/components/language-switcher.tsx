"use client";

import { Check, ChevronDown, Globe } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";

import {
  isLocale,
  localeNames,
  locales,
  LOCALE_COOKIE,
  LOCALE_COOKIE_MAX_AGE,
  type Locale,
} from "@/i18n/config";
import { cn } from "@/lib/utils";

type LanguageSwitcherProps = {
  /** Compact mode shows only the globe and the language code on small screens. */
  compact?: boolean;
  className?: string;
};

export function LanguageSwitcher({ compact = false, className }: LanguageSwitcherProps) {
  const activeLocale = useLocale();
  const current: Locale = isLocale(activeLocale) ? activeLocale : "en";
  const t = useTranslations("LanguageSwitcher");
  const currentLabel = t("current", { language: localeNames[current] });
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [isPending, startTransition] = useTransition();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  function changeLocale(next: Locale) {
    setOpen(false);

    if (next === current) {
      return;
    }

    const secure = window.location.protocol === "https:" ? "; secure" : "";
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=${LOCALE_COOKIE_MAX_AGE}; samesite=lax${secure}`;
    document.documentElement.lang = next;

    // Server Components re-render with the new messages; the URL is unchanged.
    startTransition(() => {
      router.refresh();
    });
  }

  return (
    <div ref={containerRef} className={cn("relative shrink-0", className)}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${t("aria")}. ${currentLabel}`}
        title={currentLabel}
        aria-busy={isPending}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-medium text-foreground/80 transition-colors",
          "hover:border-white/20 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          isPending && "cursor-wait opacity-60",
        )}
      >
        <Globe className="size-3.5 text-primary/80" aria-hidden="true" />
        <span className={cn("text-foreground/80", compact && "hidden sm:inline")}>
          {localeNames[current]}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground/60">
          {current}
        </span>
        <ChevronDown
          className={cn(
            "size-3 text-muted-foreground/70 transition-transform",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("label")}
          className="absolute right-0 top-full z-50 mt-2 w-48 overflow-hidden rounded-xl border border-white/10 bg-popover p-1 shadow-2xl shadow-black/40"
        >
          {locales.map((locale) => {
            const selected = locale === current;

            return (
              <button
                key={locale}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => changeLocale(locale)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs transition-colors",
                  selected
                    ? "bg-primary/10 text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.06] hover:text-foreground",
                )}
              >
                <span className="flex-1">{localeNames[locale]}</span>
                <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground/50">
                  {locale}
                </span>
                {selected ? (
                  <Check className="size-3.5 text-primary" aria-hidden="true" />
                ) : (
                  <span className="size-3.5" aria-hidden="true" />
                )}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
