/**
 * Single source of truth for the languages supported by the UI.
 *
 * The app uses cookie-based locale resolution (no locale prefix in the URL),
 * so search URLs like `/search?q=...` stay stable across languages.
 */

export const locales = ["en", "it", "es", "fr", "de", "zh"] as const;

export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = "en";

/** Cookie read by `i18n/request.ts` and written by `LanguageSwitcher`. */
export const LOCALE_COOKIE = "NEXT_LOCALE";

/** Request header set by the middleware for the current render. */
export const LOCALE_HEADER = "x-aegis-locale";

/** One year: the preference should survive browser restarts. */
export const LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/** Native language names used by the switcher. */
export const localeNames: Record<Locale, string> = {
  en: "English",
  it: "Italiano",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  zh: "中文",
};

export function isLocale(value: string | null | undefined): value is Locale {
  return typeof value === "string" && (locales as readonly string[]).includes(value);
}
