"use client";

import { useState } from "react";
import { Crop, Sparkles, Trash2 } from "lucide-react";
import { api, ApiError, errorMessage, urls } from "@/lib/api";
import type { Sample } from "@/lib/types";
import { Badge, Button, ConfirmDialog, cx, Notice, StatusBadge, TextArea } from "../ui";
import { CorrectionEditor, FULL, type Box } from "./CorrectionEditor";

type View = "corrected" | "processed";

export function splitIssues(issues: string[] | undefined) {
  const fatal: string[] = [];
  const warnings: string[] = [];
  for (const i of issues ?? []) {
    if (i.startsWith("fatal:")) fatal.push(i.slice(6).trim());
    else warnings.push(i);
  }
  return { fatal, warnings };
}

export function SampleCard({
  profileId,
  sample,
  aiConfigured,
  onChanged,
  onDeleted,
}: {
  profileId: string;
  sample: Sample;
  aiConfigured: boolean;
  onChanged: (s: Sample) => void;
  onDeleted: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [view, setView] = useState<View>("corrected");
  const [crop, setCrop] = useState<Box>((sample.corrections.crop as Box | undefined) ?? FULL);
  const [rotate, setRotate] = useState<number>(sample.corrections.rotate ?? 0);
  const [transcript, setTranscript] = useState(sample.transcript ?? "");
  const [busy, setBusy] = useState<"save" | "transcribe" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aiNote, setAiNote] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const { fatal, warnings } = splitIssues(sample.quality.issues);
  const version = `${sample.corrections.rotate ?? 0}-${(sample.corrections.crop ?? []).join(",")}-${sample.status}`;
  const thumb =
    view === "processed" && sample.has_processed
      ? urls.sampleImage(profileId, sample.id, "processed", 900, version)
      : urls.sampleImage(profileId, sample.id, "corrected", 900, version);

  const dirtyCorrections =
    rotate !== (sample.corrections.rotate ?? 0) ||
    crop.some((v, i) => Math.abs(v - ((sample.corrections.crop as Box | undefined) ?? FULL)[i]) > 1e-4);
  const dirtyTranscript = sample.kind === "freeform" && transcript !== (sample.transcript ?? "");

  async function save() {
    setBusy("save");
    setError(null);
    try {
      const body: { rotate?: number; crop?: number[]; transcript?: string } = {};
      if (dirtyCorrections) {
        body.rotate = Math.round(rotate * 10) / 10;
        body.crop = crop.map((v) => Math.round(v * 10000) / 10000);
      }
      if (dirtyTranscript) body.transcript = transcript;
      const s = await api.updateSample(profileId, sample.id, body);
      onChanged(s);
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function transcribe() {
    setBusy("transcribe");
    setError(null);
    setAiNote(null);
    try {
      const s = await api.transcribeSample(profileId, sample.id);
      setTranscript(s.transcript ?? "");
      onChanged(s);
      setAiNote("Draft transcript added. Check every line against the image before processing.");
    } catch (err) {
      if (err instanceof ApiError && err.notConfigured) {
        setAiNote("AI is not set up on this server, so the transcript has to be typed by hand.");
      } else setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    try {
      await api.deleteSample(profileId, sample.id);
      setConfirmDelete(false);
      onDeleted();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
      setConfirmDelete(false);
    }
  }

  return (
    <li className="rounded-sheet border border-line bg-sheet">
      <div className="grid gap-4 p-4 sm:grid-cols-[180px_minmax(0,1fr)]">
        <div>
          <div className="flex aspect-[3/4] items-center justify-center overflow-hidden rounded border border-line bg-paper-deep">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={thumb} alt={`Sample ${sample.original_filename}`} className="max-h-full max-w-full object-contain" loading="lazy" />
          </div>
          {sample.has_processed && (
            <div className="mt-2 flex gap-1 text-[12px]" role="group" aria-label="Image shown">
              {(["corrected", "processed"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  aria-pressed={view === v}
                  className={cx(
                    "rounded px-2 py-0.5",
                    view === v ? "bg-ink-wash font-medium text-ink" : "text-muted hover:text-ink",
                  )}
                >
                  {v === "corrected" ? "Photo" : "As read"}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate font-medium">{sample.original_filename}</p>
            <Badge tone="ink">{sample.kind === "template" ? "Template sheet" : "Free-form"}</Badge>
            {sample.template_page !== null && sample.template_page !== undefined && (
              <Badge>Page {sample.template_page + 1}</Badge>
            )}
            <StatusBadge status={sample.status} />
          </div>
          {fatal.length > 0 && (
            <Notice tone="error" className="mt-3" title="This sample cannot be used yet">
              <ul className="list-disc pl-4">
                {fatal.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </Notice>
          )}
          {warnings.length > 0 && (
            <Notice tone="warn" className="mt-3">
              <ul className="list-disc pl-4">
                {warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Notice>
          )}
          {sample.status === "uploaded" && !fatal.length && (
            <p className="mt-3 text-[13px] text-muted">Not processed yet. Use “Process samples” once your uploads are in.</p>
          )}
          {sample.kind === "freeform" && !editing && (
            <div className="mt-3">
              <p className="text-[13px] font-medium">Transcript</p>
              {sample.transcript?.trim() ? (
                <p className="mt-1 line-clamp-3 whitespace-pre-line text-[13px] text-muted">{sample.transcript}</p>
              ) : (
                <p className="mt-1 text-[13px] text-warn">
                  No transcript yet. Without it, the writing can only be measured, not cut into letters.
                </p>
              )}
            </div>
          )}
          {!editing && (
            <div className="mt-4 flex flex-wrap gap-2">
              <Button size="sm" icon={<Crop className="size-3.5" aria-hidden />} onClick={() => setEditing(true)}>
                {sample.kind === "freeform" ? "Adjust and transcribe" : "Crop and rotate"}
              </Button>
              <Button
                size="sm"
                variant="quiet-danger"
                icon={<Trash2 className="size-3.5" aria-hidden />}
                onClick={() => setConfirmDelete(true)}
              >
                Delete
              </Button>
            </div>
          )}
          {error && !editing && (
            <Notice tone="error" className="mt-3">
              {error}
            </Notice>
          )}
        </div>
      </div>

      {editing && (
        <div className="border-t border-line p-4">
          <CorrectionEditor
            src={urls.sampleImage(profileId, sample.id, "original", 1400)}
            crop={crop}
            rotate={rotate}
            onChange={(c, r) => {
              setCrop(c);
              setRotate(r);
            }}
          />
          {sample.kind === "freeform" && (
            <div className="mt-5">
              <TextArea
                label="Transcript"
                rows={8}
                value={transcript}
                onChange={(e) => setTranscript(e.target.value)}
                hint="One line per written line, exactly as written (including spelling mistakes)."
                inputClassName="ruled text-sm"
              />
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <Button
                  size="sm"
                  icon={<Sparkles className="size-3.5" aria-hidden />}
                  loading={busy === "transcribe"}
                  onClick={transcribe}
                  disabled={busy !== null && busy !== "transcribe"}
                >
                  Draft transcript with AI
                </Button>
                {!aiConfigured && <span className="text-[12.5px] text-muted">AI is not set up on this server.</span>}
              </div>
              {aiNote && (
                <Notice tone="ink" className="mt-3" role="status">
                  {aiNote}
                </Notice>
              )}
            </div>
          )}
          {error && (
            <Notice tone="error" className="mt-4">
              {error}
            </Notice>
          )}
          <div className="mt-5 flex flex-wrap gap-2">
            <Button
              variant="primary"
              loading={busy === "save"}
              onClick={save}
              disabled={!dirtyCorrections && !dirtyTranscript}
            >
              Save changes
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setCrop((sample.corrections.crop as Box | undefined) ?? FULL);
                setRotate(sample.corrections.rotate ?? 0);
                setTranscript(sample.transcript ?? "");
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
          <p className="mt-2 text-[12.5px] text-muted">Changes take effect the next time you process the samples.</p>
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this sample?"
        confirmLabel="Delete sample"
        danger
        busy={busy === "delete"}
        onConfirm={remove}
        onClose={() => setConfirmDelete(false)}
      >
        The uploaded image and anything read from it are removed. Process the profile again afterwards to rebuild the
        letters without it.
      </ConfirmDialog>
    </li>
  );
}
