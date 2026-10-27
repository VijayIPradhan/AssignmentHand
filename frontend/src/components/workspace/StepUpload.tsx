"use client";

import { useState } from "react";
import { ExternalLink, FileText, RotateCw } from "lucide-react";
import { api, errorMessage, urls } from "@/lib/api";
import { Button, formatDate, Notice, Sheet } from "../ui";
import { useWorkspace } from "./context";

export function StepUpload() {
  const { a, go, reload, watch } = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doc = a.documents.find((d) => d.role === "assignment");
  const extracting = a.active_jobs.some((j) => j.type === "extract");

  async function reExtract() {
    setBusy(true);
    setError(null);
    try {
      const j = await api.extract(a.id);
      watch(j.id);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-2xl text-sm text-muted">The assignment PDF this workspace was created from.</p>
      {doc ? (
        <Sheet>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <FileText className="size-8 text-ink-soft" aria-hidden />
              <div>
                <p className="font-medium">{doc.original_filename}</p>
                <p className="text-[13px] text-muted">
                  {doc.page_count} {doc.page_count === 1 ? "page" : "pages"}, uploaded {formatDate(doc.created_at)}
                </p>
              </div>
            </div>
            <a
              href={urls.docPdf(a.id, doc.id)}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-ink underline-offset-4 hover:underline"
            >
              Open PDF <ExternalLink className="size-3.5" aria-hidden />
            </a>
          </div>
        </Sheet>
      ) : (
        <Notice tone="error">The assignment PDF is missing from this workspace.</Notice>
      )}
      {a.status === "extraction_failed" && (
        <Notice
          tone="error"
          title="The questions could not be read"
          action={
            <Button size="sm" icon={<RotateCw className="size-3.5" aria-hidden />} loading={busy} onClick={reExtract}>
              Try again
            </Button>
          }
        >
          {a.error ?? "Extraction failed."} You can also add the questions by hand in the next step.
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {!extracting && a.status !== "extraction_failed" && (
        <div>
          <Button variant="primary" onClick={() => go("questions")}>
            Review the questions
          </Button>
        </div>
      )}
    </div>
  );
}
