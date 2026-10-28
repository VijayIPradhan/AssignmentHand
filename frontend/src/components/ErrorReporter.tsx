"use client";

import { useEffect } from "react";
import { reportClientError } from "@/lib/telemetry";

/** Mounted once in the root layout: forwards uncaught browser errors to the server log. */
export function ErrorReporter() {
  useEffect(() => {
    const onError = (e: ErrorEvent) => {
      reportClientError("error", e.message || "Uncaught error", {
        stack: e.error?.stack,
        source: "window.onerror",
      });
    };
    const onRejection = (e: PromiseRejectionEvent) => {
      const r = e.reason;
      const message = r instanceof Error ? r.message : String(r);
      // Aborted fetches (navigation, cancelled previews) are expected, not errors.
      if (r instanceof DOMException && r.name === "AbortError") return;
      reportClientError("error", `Unhandled promise rejection: ${message}`, {
        stack: r instanceof Error ? r.stack : undefined,
        source: "unhandledrejection",
      });
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}
