"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Bot, Download } from "lucide-react";
import { api, errorMessage, urls } from "@/lib/api";
import type { AiStatus, Job, Rendered } from "@/lib/types";
import { Badge, Button, cx, formatBytes, formatDate, JobProgress, Notice, TextField } from "./ui";

export const JOB_LABEL: Record<string, string> = {
  process_profile: "Processing handwriting samples",
  extract: "Reading the assignment and finding questions",
  generate: "Writing answers",
  render: "Rendering handwritten pages",
  export: "Building the document",
  cleanup: "Cleaning up",
};

export function ActiveJobs({ jobs, describe }: { jobs: Job[]; describe?: (j: Job) => string | undefined }) {
  if (!jobs.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {jobs.map((j) => (
        <JobProgress key={j.id} job={j} label={describe?.(j) ?? JOB_LABEL[j.type] ?? j.type} />
      ))}
    </div>
  );
}

export function AiStatusLine({ ai, className }: { ai: AiStatus | undefined; className?: string }) {
  if (!ai) return null;
  return (
    <p className={cx("flex items-center gap-2 text-[13px] text-muted", className)}>
      <Bot className="size-4 text-ink-soft" aria-hidden />
      {ai.configured ? (
        <span>
          AI drafting is available with <span className="font-medium text-graphite">{ai.model}</span>
          {ai.provider ? ` (${ai.provider})` : ""}.
        </span>
      ) : (
        <span>AI drafting is not set up on this server. You can write every answer yourself.</span>
      )}
    </p>
  );
}

const KIND: Record<string, string> = { handwritten: "Handwritten", digital: "Typed copy", review: "Review copy" };

export function ExportRow({ r, title }: { r: Rendered; title?: string }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-graphite">{title ?? KIND[r.kind] ?? r.kind}</p>
        <p className="text-[12.5px] text-muted">
          {title ? `${KIND[r.kind] ?? r.kind}, ` : ""}
          {r.page_count} {r.page_count === 1 ? "page" : "pages"}, {formatBytes(r.size_bytes)}, {formatDate(r.created_at)}
          {r.is_preview ? ", preview" : ""}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {r.downloaded_at && <Badge tone="neutral">Downloaded</Badge>}
        <a
          href={urls.download(r.id)}
          className="inline-flex h-8 items-center gap-1.5 rounded-[5px] border border-line-strong bg-sheet px-3 text-[13px] font-medium text-ink hover:border-ink-soft hover:bg-ink-wash"
          download
        >
          <Download className="size-3.5" aria-hidden />
          Download PDF
        </a>
      </div>
    </li>
  );
}

/** Inline form to name and create a handwriting profile, then open it. */
export function NewProfileForm({ onCancel, autoFocus = true }: { onCancel?: () => void; autoFocus?: boolean }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const p = await api.createProfile(name.trim());
      router.push(`/profiles/${p.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <TextField
        label="Profile name"
        placeholder="e.g. My everyday handwriting"
        value={name}
        maxLength={120}
        autoFocus={autoFocus}
        onChange={(e) => setName(e.target.value)}
        hint="You can keep several profiles, for example neat and quick writing."
      />
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!name.trim()}>
          Create profile
        </Button>
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
