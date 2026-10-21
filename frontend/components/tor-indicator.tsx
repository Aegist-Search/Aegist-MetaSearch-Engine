"use client";

import { Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import type { TorStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Shows whether the server is exiting through Tor.
 *
 * The fetch goes to the same-origin Next.js route, which proxies the FastAPI
 * endpoint: the backend URL never reaches the browser. The answer is cached by
 * the backend for TOR_CACHE_TTL_S, so polling every minute does not open a new
 * Tor circuit on each tick.
 */

const POLL_INTERVAL_MS = 60_000;

export function TorIndicator({ className }: { className?: string }) {
  const t = useTranslations("Tor");
  const [status, setStatus] = useState<TorStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function refresh() {
      try {
        const response = await fetch("/api/tor-status", {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("tor status unavailable");
        }
        const payload = (await response.json()) as TorStatus;
        if (!cancelled) {
          setStatus(payload);
          setUnreachable(false);
        }
      } catch (error) {
        const aborted = error instanceof DOMException && error.name === "AbortError";
        if (!cancelled && !aborted) {
          setUnreachable(true);
        }
      }
    }

    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  const loading = status === null && !unreachable;
  const active = status?.tor_active === true;

  const label = loading ? t("loading") : active ? t("active") : t("direct");
  const detail = active
    ? [
        status?.exit_ip ? t("exit", { ip: status.exit_ip }) : t("exitNode"),
        status?.latency_ms != null ? `${status.latency_ms} ms` : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : status?.error === "tor_disabled"
      ? t("disabled")
      : t("unreachable");

  return (
    <Badge
      variant="outline"
      role="status"
      aria-live="polite"
      aria-label={`${label}. ${detail}`}
      title={detail}
      className={cn(
        "gap-1.5 px-2.5 py-1 text-[11px] font-medium transition-colors",
        active
          ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
          : loading
            ? "border-white/10 bg-white/[0.04] text-muted-foreground"
            : "border-rose-400/25 bg-rose-400/10 text-rose-200",
        className,
      )}
    >
      {loading ? (
        <Loader2 className="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : active ? (
        <ShieldCheck className="size-3" aria-hidden="true" />
      ) : (
        <ShieldOff className="size-3" aria-hidden="true" />
      )}
      {label}
    </Badge>
  );
}
