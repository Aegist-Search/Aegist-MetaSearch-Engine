import { NextResponse, type NextRequest } from "next/server";

import {
  defaultLocale,
  isLocale,
  LOCALE_COOKIE,
  LOCALE_COOKIE_MAX_AGE,
  LOCALE_HEADER,
  type Locale,
} from "@/i18n/config";

/**
 * Cookie-based language negotiation.
 *
 * A stored preference always wins. On the first visit the Accept-Language
 * header is matched against the supported locales, with English as fallback.
 * The resolved locale is forwarded as a request header so Server Components
 * render in that language on the very first request, before the cookie exists.
 */
export function proxy(request: NextRequest) {
  const stored = request.cookies.get(LOCALE_COOKIE)?.value;
  const locale = isLocale(stored) ? stored : negotiateLocale(request.headers.get("accept-language"));

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(LOCALE_HEADER, locale);

  const response = NextResponse.next({
    request: { headers: requestHeaders },
  });

  if (!isLocale(stored)) {
    response.cookies.set(LOCALE_COOKIE, locale, {
      path: "/",
      maxAge: LOCALE_COOKIE_MAX_AGE,
      sameSite: "lax",
      httpOnly: false,
    });
  }

  response.headers.set("Content-Language", locale);

  return response;
}

export const config = {
  // Skip API routes, Next internals and static files.
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};

function negotiateLocale(acceptLanguage: string | null): Locale {
  if (!acceptLanguage) {
    return defaultLocale;
  }

  const ranked = acceptLanguage
    .split(",")
    .map((part, index) => {
      const [tag, ...params] = part.trim().split(";");
      const quality = params
        .map((param) => param.trim())
        .find((param) => param.startsWith("q="))
        ?.slice(2);
      const parsed = quality ? Number.parseFloat(quality) : 1;
      return {
        tag: tag.trim().toLowerCase(),
        quality: Number.isFinite(parsed) ? parsed : 0,
        index,
      };
    })
    .filter((entry) => entry.tag.length > 0 && entry.quality > 0)
    .sort((a, b) => b.quality - a.quality || a.index - b.index);

  for (const { tag } of ranked) {
    if (isLocale(tag)) {
      return tag;
    }

    const base = tag.split("-")[0];
    if (isLocale(base)) {
      return base;
    }
  }

  return defaultLocale;
}
