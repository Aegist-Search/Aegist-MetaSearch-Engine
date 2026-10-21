import type { Metadata, Viewport } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";

import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";

import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Metadata");

  return {
    title: {
      default: t("title"),
      template: "%s · Aegis",
    },
    description: t("description"),
    applicationName: "Aegis",
    referrer: "no-referrer",
    robots: { index: false, follow: false },
  };
}

export const viewport: Viewport = {
  themeColor: "#0a1310",
  colorScheme: "dark",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <body className="min-h-dvh bg-background font-sans text-foreground antialiased">
        <KeyboardShortcuts />
        <NextIntlClientProvider locale={locale} messages={messages}>
          {children}
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
