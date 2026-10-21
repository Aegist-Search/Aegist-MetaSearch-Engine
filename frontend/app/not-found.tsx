import Link from "next/link";
import { Compass } from "lucide-react";
import { useTranslations } from "next-intl";

import { Logo } from "@/components/logo";
import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  const t = useTranslations("NotFound");

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-5 px-4 text-center">
      <Logo />
      <span className="grid size-12 place-items-center rounded-full bg-white/[0.05] text-muted-foreground ring-1 ring-white/10">
        <Compass className="size-5" aria-hidden="true" />
      </span>
      <div>
        <h1 className="text-lg font-semibold">{t("title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("description")}
        </p>
      </div>
      <Link href="/" className={buttonVariants({ variant: "outline", size: "sm" })}>
        {t("back")}
      </Link>
    </main>
  );
}
