"use client";

import { useState, type FormEvent } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Sample } from "@/lib/types";
import { FileDrop } from "../FileDrop";
import { Button, cx, Notice, TextArea, TextField } from "../ui";

export function SampleUpload({
  profileId,
  maxBytes,
  onUploaded,
}: {
  profileId: string;
  maxBytes?: number;
  onUploaded: (s: Sample) => void;
}) {
  const [kind, setKind] = useState<"template" | "freeform">("template");
  const [file, setFile] = useState<File | null>(null);
  const [transcript, setTranscript] = useState("");
  const [page, setPage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const pageNum = page.trim() ? Number(page) - 1 : null;
      const s = await api.uploadSample(profileId, file, kind, {
        transcript: kind === "freeform" ? transcript : undefined,
        template_page: kind === "template" && pageNum !== null && pageNum >= 0 ? pageNum : null,
      });
      setFile(null);
      setTranscript("");
      setPage("");
      onUploaded(s);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <fieldset>
        <legend className="mb-2 text-[13px] font-medium">Kind of sample</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              ["template", "Template sheet", "A filled-in page of the printable sheet."],
              ["freeform", "Free-form writing", "A page of normal writing, with a transcript."],
            ] as const
          ).map(([value, title, desc]) => (
            <label
              key={value}
              className={cx(
                "flex cursor-pointer gap-3 rounded-[5px] border px-3.5 py-3 text-sm",
                kind === value ? "border-ink bg-ink-wash" : "border-line-strong bg-sheet hover:border-ink-soft",
              )}
            >
              <input
                type="radio"
                name="kind"
                value={value}
                checked={kind === value}
                onChange={() => setKind(value)}
                className="mt-0.5 accent-ink"
              />
              <span>
                <span className="block font-medium text-graphite">{title}</span>
                <span className="block text-[13px] text-muted">{desc}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <FileDrop
        accept="image/png,image/jpeg,application/pdf,.png,.jpg,.jpeg,.pdf"
        label="Choose a photo or scan"
        hint="PNG, JPG or PDF (first page). Drop a file here or click to choose."
        file={file}
        maxBytes={maxBytes}
        onFile={(f, err) => {
          setFile(f);
          setError(err ?? null);
        }}
        disabled={busy}
        compact
      />
      {kind === "template" ? (
        <TextField
          label="Sheet page (optional)"
          type="number"
          min={1}
          max={21}
          inputMode="numeric"
          value={page}
          onChange={(e) => setPage(e.target.value)}
          hint="Leave empty: the page is read from the code printed on the sheet."
          className="max-w-xs"
        />
      ) : (
        <TextArea
          label="Transcript"
          rows={5}
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
          hint="Type exactly what is written, one line per written line. You can also add or fix it after uploading."
          inputClassName="ruled text-sm"
        />
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div>
        <Button type="submit" variant="primary" loading={busy} disabled={!file}>
          Upload sample
        </Button>
      </div>
    </form>
  );
}
