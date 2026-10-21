"use client";

import { useEffect } from "react";

const RESULT_LINK_SELECTOR = "a[data-result-link]";
const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isEditable(target: EventTarget | null): target is HTMLElement {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return target.isContentEditable || EDITABLE_TAGS.has(target.tagName);
}

/**
 * Keyboard shortcuts, mounted once in the root layout:
 *
 * - `/` focuses the search field (unless the user is already typing);
 * - `Esc` closes an open preview and otherwise blurs the field;
 * - `ArrowUp`/`ArrowDown` move between result links without leaving the
 *   page; `Enter` then opens the focused result with the native link
 *   behaviour.
 *
 * No extra UI is rendered, so the layout does not change.
 */
export function KeyboardShortcuts() {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      if (event.key === "/") {
        if (isEditable(event.target) || document.querySelector("dialog[open]")) {
          return;
        }
        const input = document.querySelector<HTMLInputElement>(
          'input[type="search"][name="q"]',
        );
        if (!input) {
          return;
        }
        event.preventDefault();
        input.focus();
        input.select();
        return;
      }

      if (event.key === "Escape") {
        const dialog = document.querySelector<HTMLDialogElement>("dialog[open]");
        if (dialog) {
          dialog.close();
          return;
        }
        if (isEditable(event.target)) {
          event.target.blur();
        }
        return;
      }

      // A modal preview owns the keyboard: do not move focus behind it.
      if (document.querySelector("dialog[open]")) {
        return;
      }

      if (isEditable(event.target)) {
        return;
      }

      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }

      const links = Array.from(
        document.querySelectorAll<HTMLAnchorElement>(RESULT_LINK_SELECTOR),
      );
      if (links.length === 0) {
        return;
      }

      event.preventDefault();
      const current = links.indexOf(document.activeElement as HTMLAnchorElement);
      const next =
        event.key === "ArrowDown"
          ? (current + 1) % links.length
          : current <= 0
            ? links.length - 1
            : current - 1;
      links[next].focus();
      links[next].scrollIntoView({ block: "nearest" });
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  return null;
}
