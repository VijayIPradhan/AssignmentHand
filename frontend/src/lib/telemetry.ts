/**
 * Sends browser-side problems (uncaught errors, unhandled promise rejections, server errors and
 * unreachable API) to the backend log via POST /api/v1/client-log, so issues seen on a phone or
 * another browser show up next to the server's own logs.
 *
 * Throttled (at most 10 reports a minute) and de-duplicated; never throws. No page content, form
 * values or answers are sent - only the error message, stack and page path.
 */

const LIMIT_PER_MINUTE = 10;
const sent: number[] = [];
const seen = new Set<string>();

export type ClientLogLevel = "error" | "warning" | "info";

export function reportClientError(
  level: ClientLogLevel,
  message: string,
  opts: { stack?: string; source?: string } = {},
) {
  if (typeof window === "undefined") return;
  try {
    const key = `${level}|${message}`.slice(0, 300);
    if (seen.has(key)) return;
    const now = Date.now();
    while (sent.length && now - sent[0] > 60_000) sent.shift();
    if (sent.length >= LIMIT_PER_MINUTE) return;
    seen.add(key);
    sent.push(now);
    const body = JSON.stringify({
      level,
      message: message.slice(0, 2000),
      stack: opts.stack?.slice(0, 8000),
      url: window.location.pathname + window.location.search,
      source: opts.source,
    });
    // keepalive lets the report finish even if the page is navigating away
    void fetch("/api/v1/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "include",
    }).catch(() => undefined);
  } catch {
    // reporting must never break the page
  }
}
