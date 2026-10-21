"use client";

import { Loader2, Search } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import {
  useEffect,
  useId,
  useState,
  useTransition,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { buildSearchHref } from "@/lib/search-params";
import type { SearchEngine, SearchSuggestion, SuggestResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 250;
const MIN_SUGGEST_LENGTH = 2;

type SearchBarProps = {
  defaultValue?: string;
  autoFocus?: boolean;
  size?: "default" | "lg";
  className?: string;
  placeholder?: string;
  engine?: SearchEngine;
  domain?: string;
  lang?: string;
};

export function SearchBar({
  defaultValue = "",
  autoFocus = false,
  size = "default",
  className,
  placeholder,
  engine = "auto",
  domain = "",
  lang = "",
}: SearchBarProps) {
  const t = useTranslations("Search");
  const router = useRouter();
  const [value, setValue] = useState(defaultValue);
  const [isPending, startTransition] = useTransition();
  const [focused, setFocused] = useState(false);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();
  const large = size === "lg";

  // Suggestions come from the same-origin proxy, which only queries the local
  // index: no third party sees what the user is typing. The request is
  // debounced and aborted when the next keystroke arrives.
  useEffect(() => {
    const term = value.trim();
    if (!focused || term.length < MIN_SUGGEST_LENGTH) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/suggest?q=${encodeURIComponent(term)}`, {
          cache: "no-store",
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return;
        const data = (await response.json()) as SuggestResponse;
        const items = Array.isArray(data.suggestions) ? data.suggestions.slice(0, 6) : [];
        setSuggestions(items);
        setOpen(items.length > 0);
        setActive(-1);
      } catch {
        // Aborted request or backend offline: keep the previous list.
      }
    }, DEBOUNCE_MS);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [value, focused]);

  function navigate(query: string) {
    setOpen(false);
    startTransition(() => {
      router.push(buildSearchHref({ q: query, engine, domain, lang }));
    });
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = value.trim().replace(/\s+/g, " ");
    if (!query) return;
    navigate(query);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      setOpen(true);
      setActive((current) => (current + 1) % suggestions.length);
      return;
    }
    if (event.key === "ArrowUp") {
      if (suggestions.length === 0) return;
      event.preventDefault();
      setOpen(true);
      setActive((current) => (current <= 0 ? suggestions.length - 1 : current - 1));
      return;
    }
    if (event.key === "Enter" && open && active >= 0) {
      event.preventDefault();
      select(suggestions[active]);
    }
  }

  function select(suggestion: SearchSuggestion) {
    setValue(suggestion.title);
    setSuggestions([]);
    navigate(suggestion.title);
  }

  return (
    <form
      action="/search"
      method="get"
      role="search"
      onSubmit={handleSubmit}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setFocused(false);
          setOpen(false);
        }
      }}
      className={cn("group/search relative w-full", className)}
    >
      <Search
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground/70 transition-colors group-focus-within/search:text-primary",
          large ? "size-5" : "size-4",
        )}
      />
      <Input
        type="search"
        name="q"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onFocus={() => setFocused(true)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder ?? t("placeholder")}
        autoFocus={autoFocus}
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="search"
        maxLength={400}
        aria-label={t("ariaLabel")}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        className={cn(
          "rounded-2xl border-white/10 bg-white/[0.04] pl-11 pr-24 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)] transition-all placeholder:text-muted-foreground/60 hover:border-white/20 focus-visible:border-primary/40 focus-visible:bg-white/[0.06] focus-visible:ring-4 focus-visible:ring-primary/10",
          large
            ? "h-14 pr-28 text-base sm:pr-32 sm:text-lg"
            : "h-11 rounded-xl text-base sm:text-sm",
        )}
      />
      <Button
        type="submit"
        size={large ? "lg" : "sm"}
        disabled={isPending || value.trim().length === 0}
        className={cn(
          "absolute right-1.5 top-1/2 -translate-y-1/2 rounded-xl",
          large ? "h-11 px-5" : "h-8",
        )}
      >
        {isPending ? (
          <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <Search className={cn(large ? "size-4" : "size-3.5")} aria-hidden="true" />
        )}
        <span>{t("submit")}</span>
      </Button>

      {open && suggestions.length > 0 ? (
        <ul
          id={listId}
          role="listbox"
          aria-label={t("suggestions")}
          className="absolute left-0 right-0 top-[calc(100%+0.4rem)] z-40 overflow-hidden rounded-xl border border-white/10 bg-background/95 shadow-xl backdrop-blur-xl"
        >
          {suggestions.map((suggestion, index) => (
            <li key={`${suggestion.url}-${index}`} role="presentation">
              <button
                type="button"
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(suggestion)}
                onMouseEnter={() => setActive(index)}
                className={cn(
                  "flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm transition-colors",
                  index === active ? "bg-primary/10 text-foreground" : "text-foreground/80",
                )}
              >
                <span className="truncate">{suggestion.title}</span>
                <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground/60">
                  {suggestion.domain}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <input type="hidden" name="page" value="1" />
      {engine !== "auto" ? <input type="hidden" name="engine" value={engine} /> : null}
      {domain ? <input type="hidden" name="domain" value={domain} /> : null}
      {lang ? <input type="hidden" name="lang" value={lang} /> : null}
    </form>
  );
}
