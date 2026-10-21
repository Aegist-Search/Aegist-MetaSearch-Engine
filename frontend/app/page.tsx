import { Check, Database, Globe2, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";

import { LanguageSwitcher } from "@/components/language-switcher";
import { Logo } from "@/components/logo";
import { SearchBar } from "@/components/search-bar";
import { SiteFooter } from "@/components/site-footer";
import { Card } from "@/components/ui/card";

export default function HomePage() {
  const t = useTranslations("Home");

  const features = [
    {
      icon: Database,
      title: t("features.local.title"),
      description: t("features.local.description"),
    },
    {
      icon: Globe2,
      title: t("features.shield.title"),
      description: t("features.shield.description"),
    },
    {
      icon: ShieldCheck,
      title: t("features.privacy.title"),
      description: t("features.privacy.description"),
    },
  ];

  const trust = [t("trust.cookies"), t("trust.logs"), t("trust.profiling")];

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden">
      <div
        className="hero-glow pointer-events-none absolute inset-x-0 top-0 h-[560px]"
        aria-hidden="true"
      />

      {/* z-30 keeps the header (and the language dropdown inside it) above
          <main className="relative z-10">: with equal z-index the later
          sibling would paint over the menu and swallow its clicks. */}
      <header className="relative z-30 flex items-center justify-between gap-3 border-b border-white/[0.06] px-5 py-4 sm:px-8">
        <Logo />
        <div className="flex min-w-0 items-center gap-2">
          <LanguageSwitcher />
          <span className="hidden items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-muted-foreground sm:inline-flex">
            <ShieldCheck className="size-3.5 text-primary" aria-hidden="true" />
            {t("zeroTraces")}
          </span>
        </div>
      </header>

      <main className="relative z-10 flex flex-1 flex-col items-center px-5 pb-20 pt-14 sm:pt-20">
        <div className="w-full max-w-2xl">
          <div className="mb-9 text-center">
            <span className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] font-medium text-muted-foreground">
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60 motion-reduce:animate-none" />
                <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
              </span>
              {t("eyebrow")}
            </span>

            <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
              {t("titleLead")}
              <span className="mt-2 block bg-linear-to-r from-primary via-emerald-200 to-teal-300 bg-clip-text text-transparent">
                {t("titleAccent")}
              </span>
            </h1>

            <p className="mx-auto mt-5 max-w-lg text-sm leading-relaxed text-muted-foreground sm:text-base">
              {t("subtitle")}
            </p>
          </div>

          <SearchBar size="lg" autoFocus />

          <div className="mt-5 flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground/75">
            {trust.map((item) => (
              <span key={item} className="inline-flex items-center gap-1.5">
                <Check className="size-3 text-primary" aria-hidden="true" />
                {item}
              </span>
            ))}
          </div>
        </div>

        <div className="mt-16 grid w-full max-w-4xl gap-3 sm:grid-cols-3">
          {features.map(({ icon: Icon, title, description }) => (
            <Card
              key={title}
              className="group gap-0 rounded-2xl border-white/[0.08] bg-white/[0.025] p-5 shadow-none transition-all duration-300 hover:-translate-y-0.5 hover:border-primary/25 hover:bg-white/[0.045]"
            >
              <span className="grid size-9 place-items-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/20 transition-colors group-hover:bg-primary/20">
                <Icon className="size-4" aria-hidden="true" />
              </span>
              <h2 className="mt-4 text-sm font-semibold">{title}</h2>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {description}
              </p>
            </Card>
          ))}
        </div>
      </main>

      <SiteFooter className="relative z-10 border-t-0" />
    </div>
  );
}
