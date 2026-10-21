import { ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";

import { TorIndicator } from "@/components/tor-indicator";
import { cn } from "@/lib/utils";

export function SiteFooter({ className }: { className?: string }) {
  const t = useTranslations("Footer");

  return (
    <footer
      className={cn(
        "mt-auto border-t border-white/[0.06] px-4 py-5 text-center text-xs text-muted-foreground/80",
        className,
      )}
    >
      <div className="mx-auto flex max-w-3xl flex-col items-center gap-3">
        <TorIndicator />
        <p className="flex flex-wrap items-center justify-center gap-1.5">
          <ShieldCheck className="size-3.5 text-primary/80" aria-hidden="true" />
          <span>{t("privacy")}</span>
        </p>
      </div>
    </footer>
  );
}
