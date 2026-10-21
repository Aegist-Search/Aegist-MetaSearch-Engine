"use client";

import { useRef, useState } from "react";
import { ExternalLink, ImageOff, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { previewImageSrc } from "@/lib/preview";
import { cn } from "@/lib/utils";

type ResultPreviewProps = {
  image?: string | null;
  title: string;
  url: string;
  domain: string;
  snippet?: string;
};

export function ResultPreview({ image, title, url, domain, snippet }: ResultPreviewProps) {
  const t = useTranslations("Search.preview");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [broken, setBroken] = useState(false);
  const rawSrc = previewImageSrc(image);
  const hasImage = rawSrc !== null;
  const src = broken ? null : rawSrc;
  const initial = domain.replace(/^www\./, "").charAt(0).toUpperCase() || "?";

  return (
    <>
      <button
        type="button"
        onClick={() => dialogRef.current?.showModal()}
        aria-label={t("open", { title })}
        className={cn(
          "group/thumb relative mt-1 h-20 w-24 shrink-0 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]",
          "transition duration-300 hover:border-primary/35 hover:bg-white/[0.06]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 sm:h-[5.5rem] sm:w-32",
        )}
      >
        {src ? (
          <img
            src={src}
            alt=""
            loading="lazy"
            decoding="async"
            onError={() => setBroken(true)}
            className="size-full object-cover opacity-85 transition duration-300 group-hover/thumb:scale-[1.03] group-hover/thumb:opacity-100"
          />
        ) : (
          <span
            aria-hidden="true"
            className="grid size-full place-items-center bg-gradient-to-br from-primary/20 via-transparent to-transparent text-xl font-semibold text-primary/60"
          >
            {initial}
          </span>
        )}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-xl ring-1 ring-inset ring-white/5 transition group-hover/thumb:ring-primary/25"
        />
      </button>

      <dialog
        ref={dialogRef}
        aria-label={t("dialog", { title })}
        onClick={(event) => {
          if (event.target === dialogRef.current) {
            dialogRef.current?.close();
          }
        }}
        className="m-auto max-h-[90vh] w-[min(92vw,44rem)] overflow-y-auto rounded-2xl border border-white/10 bg-card p-0 text-card-foreground shadow-2xl backdrop:bg-black/70 backdrop:backdrop-blur-sm"
      >
        <div className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{title}</p>
            <p className="truncate text-xs text-muted-foreground">{domain}</p>
          </div>
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            aria-label={t("close")}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-white/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        {src ? (
          <img
            src={src}
            alt={t("image", { title })}
            onError={() => setBroken(true)}
            className="max-h-[62vh] w-full bg-black/30 object-contain"
          />
        ) : (
          <div className="flex aspect-video flex-col items-center justify-center gap-2 bg-white/[0.03] px-6 text-center text-sm text-muted-foreground">
            <ImageOff className="size-5" aria-hidden="true" />
            <p className="font-medium text-foreground/80">
              {hasImage ? t("imageUnavailableTitle") : t("noImageTitle")}
            </p>
            <p className="max-w-sm text-xs leading-relaxed text-muted-foreground/80">
              {hasImage ? t("imageUnavailableDescription") : t("noImageDescription")}
            </p>
          </div>
        )}

        {snippet ? (
          <p className="mt-3 line-clamp-3 px-4 text-sm leading-relaxed text-muted-foreground">
            {snippet}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <span className="min-w-0 truncate text-xs text-muted-foreground/70">{url}</span>
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            referrerPolicy="no-referrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-sm text-xs font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            {t("openSite")}
            <ExternalLink className="size-3" aria-hidden="true" />
          </a>
        </div>
      </dialog>
    </>
  );
}
