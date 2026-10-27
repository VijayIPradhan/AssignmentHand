"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, Cog, Pencil, Trash2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useJob, useResource } from "@/lib/hooks";
import type { Profile, PublicConfig } from "@/lib/types";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  JobProgress,
  LoadingBlock,
  Notice,
  PageHeader,
  Sheet,
  StatusBadge,
  TextField,
} from "../ui";
import { ProfileResults } from "./ProfileResults";
import { SampleCard, splitIssues } from "./SampleCard";
import { SampleGuide } from "./SampleGuide";
import { SampleUpload } from "./SampleUpload";
import { StylePreview } from "./StylePreview";

export function ProfileView({ id }: { id: string }) {
  const router = useRouter();
  const res = useResource<Profile>(`profile:${id}`, () => api.profile(id));
  const config = useResource<PublicConfig>("config", api.publicConfig);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const p = res.data;
  const { reload, setData } = res;

  // Reattach to a processing job that was started earlier (e.g. before a page reload).
  const status = p?.status;
  useEffect(() => {
    if (status !== "processing" || jobId) return;
    let cancelled = false;
    api
      .jobs({ profile_id: id, active: true, limit: 1 })
      .then((jobs) => {
        if (!cancelled && jobs[0]) setJobId(jobs[0].id);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [status, jobId, id]);

  const job = useJob(jobId, (j) => {
    if (j.status === "failed") setJobError(j.error ?? "Processing failed.");
    setJobId(null);
    void reload(true);
  });

  async function processSamples() {
    setStarting(true);
    setStartError(null);
    setJobError(null);
    try {
      const j = await api.processProfile(id);
      setJobId(j.id);
      setData((prev) => (prev ? { ...prev, status: "processing" } : prev));
    } catch (err) {
      setStartError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  async function rename() {
    if (!name.trim()) return;
    setRenameError(null);
    try {
      const updated = await api.updateProfile(id, { name: name.trim() });
      setData(updated);
      setRenaming(false);
    } catch (err) {
      setRenameError(errorMessage(err));
    }
  }

  async function remove() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteProfile(id);
      router.push("/profiles");
    } catch (err) {
      setDeleteError(errorMessage(err));
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (res.loading && !p) return <LoadingBlock label="Loading profile" />;
  if (res.error && !p) return <ErrorState message={res.error} onRetry={() => void reload()} />;
  if (!p) return null;

  const samples = p.samples ?? [];
  const processing = p.status === "processing" || !!jobId;
  const blocking = samples.filter((s) => splitIssues(s.quality.issues).fatal.length > 0).length;
  const canPreview = (p.metrics?.glyph_count ?? 0) > 0 && !processing;
  const maxMb = config.data?.max_upload_mb;

  return (
    <>
      <PageHeader
        back={
          <Link href="/profiles" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" aria-hidden /> Handwriting profiles
          </Link>
        }
        title={
          renaming ? (
            <span className="sr-only">Rename {p.name}</span>
          ) : (
            <span className="inline-flex flex-wrap items-center gap-3">
              {p.name}
              <StatusBadge status={processing ? "processing" : p.status} className="font-sans" />
            </span>
          )
        }
        actions={
          !renaming && (
            <>
              <Button
                variant="ghost"
                size="sm"
                icon={<Pencil className="size-3.5" aria-hidden />}
                onClick={() => {
                  setName(p.name);
                  setRenaming(true);
                }}
              >
                Rename
              </Button>
              <Button
                variant="quiet-danger"
                size="sm"
                icon={<Trash2 className="size-3.5" aria-hidden />}
                onClick={() => setConfirmDelete(true)}
              >
                Delete profile
              </Button>
            </>
          )
        }
      />

      {renaming && (
        <form
          className="-mt-4 mb-8 flex max-w-xl flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void rename();
          }}
        >
          <TextField
            label="Profile name"
            value={name}
            maxLength={120}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            className="min-w-60 flex-1"
            error={renameError}
          />
          <Button type="submit" variant="primary" disabled={!name.trim()}>
            Save name
          </Button>
          <Button variant="ghost" onClick={() => setRenaming(false)}>
            Cancel
          </Button>
        </form>
      )}
      {deleteError && (
        <Notice tone="error" className="mb-6">
          {deleteError}
        </Notice>
      )}

      <div className="flex flex-col gap-8">
        <SampleGuide open={samples.length === 0} />

        <Sheet
          title="Samples"
          description={
            samples.length
              ? `${samples.length} uploaded${blocking ? `, ${blocking} with problems that stop them being used` : ""}.`
              : "Upload a photo or scan of each filled-in page."
          }
          actions={
            samples.length > 0 && (
              <Button
                variant="primary"
                icon={<Cog className="size-4" aria-hidden />}
                loading={starting}
                disabled={processing}
                onClick={processSamples}
              >
                Process samples
              </Button>
            )
          }
        >
          <div className="flex flex-col gap-6">
            {(processing || job.job) && (
              <JobProgress job={job.job} label="Processing samples" error={job.error} />
            )}
            {startError && <Notice tone="error">{startError}</Notice>}
            {jobError && (
              <Notice
                tone="error"
                title="Processing failed"
                action={
                  <Button size="sm" onClick={processSamples}>
                    Try again
                  </Button>
                }
              >
                {jobError}
              </Notice>
            )}
            {samples.length === 0 ? (
              <EmptyState title="No samples yet">
                Start with the template sheet: print it, fill it in and upload a photo below.
              </EmptyState>
            ) : (
              <ul className="flex flex-col gap-4">
                {samples.map((s) => (
                  <SampleCard
                    key={s.id}
                    profileId={p.id}
                    sample={s}
                    aiConfigured={config.data?.ai.configured ?? false}
                    onChanged={(ns) =>
                      setData((prev) =>
                        prev
                          ? { ...prev, status: "draft", samples: prev.samples?.map((x) => (x.id === ns.id ? ns : x)) }
                          : prev,
                      )
                    }
                    onDeleted={() => void reload(true)}
                  />
                ))}
              </ul>
            )}
            {p.status === "draft" && samples.length > 0 && !processing && (
              <Notice tone="ink">
                There are changes that have not been processed yet. Process the samples to update the captured letters.
              </Notice>
            )}
            <div className="border-t border-line pt-5">
              <h3 className="mb-3 text-lg">Add a sample</h3>
              <SampleUpload
                profileId={p.id}
                maxBytes={maxMb ? maxMb * 1024 * 1024 : undefined}
                onUploaded={() => void reload(true)}
              />
            </div>
          </div>
        </Sheet>

        {!processing && <ProfileResults profile={p} />}

        {canPreview ? (
          <StylePreview profile={p} onSaved={(np) => setData(np)} />
        ) : (
          !processing && (
            <Sheet title="Style and live preview">
              <p className="text-sm text-muted">
                Process your samples first. Then you can tune the size, slant and spacing and see a live preview.
              </p>
            </Sheet>
          )
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete “${p.name}”?`}
        confirmLabel="Delete profile"
        danger
        busy={deleting}
        onConfirm={remove}
        onClose={() => setConfirmDelete(false)}
      >
        This permanently deletes every uploaded sample and all letters and measurements derived from them. Assignments
        that used this profile keep their exported PDFs but will need another profile to render again.
      </ConfirmDialog>
    </>
  );
}
