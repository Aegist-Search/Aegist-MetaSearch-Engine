"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("Error");

  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <Card
        role="alert"
        className="max-w-md items-center gap-3 rounded-2xl border-destructive/25 bg-destructive/[0.06] px-6 py-12 text-center shadow-none"
      >
        <span className="grid size-12 place-items-center rounded-full bg-destructive/10 text-destructive ring-1 ring-destructive/20">
          <AlertTriangle className="size-5" aria-hidden="true" />
        </span>
        <h1 className="text-base font-semibold">{t("title")}</h1>
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
          {t("description")}
        </p>
        <Button variant="outline" size="sm" onClick={reset} className="mt-2">
          <RotateCcw className="size-3.5" aria-hidden="true" />
          {t("retry")}
        </Button>
      </Card>
    </main>
  );
}
