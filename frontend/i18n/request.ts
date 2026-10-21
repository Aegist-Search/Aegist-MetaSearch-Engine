import { getRequestConfig } from "next-intl/server";
import { cookies, headers } from "next/headers";

import { defaultLocale, isLocale, LOCALE_COOKIE, LOCALE_HEADER } from "./config";

/**
 * Cookie-based request configuration (no locale-based routing).
 *
 * The middleware resolves the locale once per request and forwards it in a
 * request header, so the first visit already renders in the negotiated
 * language even before the browser stores the cookie.
 */
export default getRequestConfig(async () => {
  const requestHeaders = await headers();
  const requested = requestHeaders.get(LOCALE_HEADER);
  const stored = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(requested)
    ? requested
    : isLocale(stored)
      ? stored
      : defaultLocale;

  const messages = await loadMessages(locale);

  return {
    locale,
    messages,
  };
});

async function loadMessages(locale: string) {
  switch (locale) {
    case "it":
      return (await import("../messages/it.json")).default;
    case "es":
      return (await import("../messages/es.json")).default;
    case "fr":
      return (await import("../messages/fr.json")).default;
    case "de":
      return (await import("../messages/de.json")).default;
    case "zh":
      return (await import("../messages/zh.json")).default;
    case "en":
    default:
      return (await import("../messages/en.json")).default;
  }
}
