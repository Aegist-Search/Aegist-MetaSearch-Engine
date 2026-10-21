const MAX_PREVIEW_URL_LENGTH = 2048;

/**
 * Same-origin URL that renders a result image through the backend proxy.
 * Returns null for missing or non-http(s) values so the UI can show a fallback.
 */
export function previewImageSrc(image?: string | null): string | null {
  if (!image) {
    return null;
  }

  const value = image.trim();
  if (value.length === 0 || value.length > MAX_PREVIEW_URL_LENGTH) {
    return null;
  }
  if (!/^https?:\/\//i.test(value)) {
    return null;
  }

  return `/api/preview?url=${encodeURIComponent(value)}`;
}
