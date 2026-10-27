"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Download,
  ExternalLink,
  FileCheck2,
  FileText,
  Layers,
  Minimize2,
  MoveHorizontal,
  Scissors,
  ScanEye,
  Trash2,
  Type,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { api, ApiError, errorMessage, urls } from "@/lib/api";
import { useJob } from "@/lib/hooks";
import type { Rendered, RenderWarning } from "@/lib/types";
import { Badge, Button, cx, formatBytes, formatDate, JobProgress, Notice, Sheet } from "../ui";
import { useWorkspace } from "./context";

const WARN_ICON: Record<string, ReactNode> = {
  missing_glyph: <Type className="size-4" aria-hidden />,
  approximated_glyph: <Type className="size-4" aria-hidden />,
  unsupported_character: <Type className="size-4" aria-hidden />,
  exceeds_space: <ArrowDownToLine className="size-4" aria-hidden />,
  no_answer_space: <ArrowDownToLine className="size-4" aria-hidden />,
  shrunk_to_fit: <Minimize2 className="size-4" aria-hidden />,
  scaled_down: <Minimize2 className="size-4" aria-hidden />,
  clipping: <Scissors className="size-4" aria-hidden />,
  overflow: <MoveHorizontal className="size-4" aria-hidden />,
  unapproved: <CircleAlert className="size-4" aria-hidden />,
  line_overlap_risk: <Layers className="size-4" aria-hidden />,
};

const WARN_LABEL: Record<string, string> = {
  missing_glyph: "Missing letters",
  approximated_glyph: "Approximated letters",
  unsupported_character: "Unsupported characters",
  exceeds_space: "Answer does not fit its space",
  no_answer_space: "No answer space found",
  shrunk_to_fit: "Written smaller to fit",
  scaled_down: "Expression shrunk",
  clipping: "Text past the margin",
  overflow: "Too wide or tall",
  unapproved: "Unapproved answers",
  line_overlap_risk: "Lines may overlap",
  empty_answer: "Empty answer",
  figure_missing: "Figure not found",
  unsupported_math: "Unsupported maths",
  table_narrow: "Narrow table",
};

const SERIOUS = new Set(["clipping", "overflow", "exceeds_space", "unapproved", "line_overlap_risk", "unsupported_character", "empty_answer"]);

const KIND_LABEL: Record<string, string> = { handwritten: "Handwritten", digital: "Typed copy", review: "Review copy" };

function Warnings({ warnings }: { warnings: RenderWarning[] }) {
  if (!warnings.length)
    return (
      <Notice tone="ok" title="No layout problems found">
        Still read every page before handing it in.
      </Notice>
    );
  return (
    <ul className="flex flex-col gap-2" aria-label="Rendering warnings">
      {warnings.map((w, i) => {
        const serious = SERIOUS.has(w.type);
        return (
          <li
            key={i}
            className={cx(
              "flex gap-2.5 rounded-[5px] border px-3 py-2 text-[13.5px]",
              serious ? "border-[#ecd9a8] bg-warn-soft" : "border-line bg-paper",
            )}
          >
            <span className={cx("mt-0.5 shrink-0", serious ? "text-warn" : "text-ink-soft")}>
              {WARN_ICON[w.type] ?? <AlertTriangle className="size-4" aria-hidden />}
            </span>
            <span>
              <span className="font-medium">{WARN_LABEL[w.type] ?? w.type.replace(/_/g, " ")}</span>
              {w.page ? <span className="text-muted"> (page {w.page})</span> : null}: {w.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Viewer({ r }: { r: Rendered }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const n = r.page_count;
  const prev = useCallback(() => setPage((p) => Math.max(1, p - 1)), []);
  const next = useCallback(() => setPage((p) => Math.min(n, p + 1)), [n]);

  if (n === 0) return <p className="text-sm text-muted">This document has no pages.</p>;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={prev} icon={<ChevronLeft className="size-4" aria-hidden />} />
        <span className="text-sm tabular-nums" aria-live="polite">
          Page {page} of {n}
        </span>
        <Button size="sm" variant="ghost" aria-label="Next page" disabled={page >= n} onClick={next} icon={<ChevronRight className="size-4" aria-hidden />} />
        <span className="mx-2 h-5 w-px bg-line" aria-hidden />
        <Button size="sm" variant="ghost" aria-label="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))} icon={<ZoomOut className="size-4" aria-hidden />} />
        <span className="w-12 text-center text-sm tabular-nums">{Math.round(zoom * 100)}%</span>
        <Button size="sm" variant="ghost" aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom((z) => Math.min(3, +(z + 0.25).toFixed(2)))} icon={<ZoomIn className="size-4" aria-hidden />} />
        <Button size="sm" variant="ghost" onClick={() => setZoom(1)} disabled={zoom === 1}>
          Fit width
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-[96px_minmax(0,1fr)]">
        <ol className="order-2 flex gap-2 overflow-x-auto md:order-1 md:max-h-[80vh] md:flex-col md:overflow-y-auto md:overflow-x-visible md:pr-1" aria-label="Pages">
          {Array.from({ length: n }, (_, i) => i + 1).map((pg) => (
            <li key={pg} className="shrink-0">
              <button
                type="button"
                onClick={() => setPage(pg)}
                aria-current={pg === page ? "page" : undefined}
                aria-label={`Page ${pg}`}
                className={cx(
                  "block w-20 overflow-hidden rounded border bg-white md:w-full",
                  pg === page ? "border-ink ring-2 ring-ink/20" : "border-line hover:border-ink-soft",
                )}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={urls.renderedPage(r.id, pg)} alt="" loading="lazy" className="block w-full" />
                <span className="block border-t border-line py-0.5 text-center text-[11px] text-muted">{pg}</span>
              </button>
            </li>
          ))}
        </ol>
        <div
          className="order-1 max-h-[80vh] overflow-auto rounded border border-line bg-paper-deep p-3 md:order-2"
          tabIndex={0}
          aria-label={`Page ${page}. Use the left and right arrow keys to change page.`}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") prev();
            if (e.key === "ArrowRight") next();
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={urls.renderedPage(r.id, page)}
            alt={`Rendered page ${page} of ${n}`}
            className="mx-auto block bg-white shadow-[0_1px_3px_rgb(0_0_0/0.12)]"
            style={{ width: `${zoom * 100}%`, maxWidth: "none" }}
          />
        </div>
      </div>
    </div>
  );
}

type Action = "preview" | "final" | "digital" | "review";

export function StepExport() {
  const { a, reload, go, watch } = useWorkspace();
  const [jobId, setJobId] = useState<string | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [starting, setStarting] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unapproved, setUnapproved] = useState<string[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const sorted = useMemo(
    () => [...a.rendered].sort((x, y) => (x.created_at < y.created_at ? 1 : -1)),
    [a.rendered],
  );
  const handwritten = sorted.filter((r) => r.kind === "handwritten");
  const selected = sorted.find((r) => r.id === selectedId) ?? handwritten[0] ?? null;

  const job = useJob(jobId, (j) => {
    if (j.status === "failed") setError(j.error ?? "This did not complete.");
    const rid = (j.result as { rendered_id?: string } | undefined)?.rendered_id;
    void reload().then(() => {
      if (rid && j.status === "succeeded") setSelectedId(rid);
    });
    setJobId(null);
  });

  async function start(kind: Action) {
    setStarting(kind);
    setError(null);
    setUnapproved(null);
    try {
      const j =
        kind === "preview" || kind === "final" ? await api.render(a.id, kind === "preview") : await api.exportCopy(a.id, kind);
      setAction(kind);
      setJobId(j.id);
      watch(j.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.details && typeof err.details === "object" && "unapproved" in err.details) {
        setUnapproved(((err.details as { unapproved: unknown[] }).unapproved ?? []).map(String));
      } else setError(errorMessage(err));
    } finally {
      setStarting(null);
    }
  }

  async function remove(rid: string) {
    setDeleting(rid);
    setError(null);
    try {
      await api.deleteRendered(rid);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDeleting(null);
    }
  }

  const busy = !!jobId || a.active_jobs.some((j) => j.type === "render" || j.type === "export");
  const label: Record<Action, string> = {
    preview: "Rendering the preview",
    final: "Rendering the final PDF",
    digital: "Building the typed copy",
    review: "Building the review copy",
  };
  const stats = a.stats ?? {};
  const allApproved = (stats.questions ?? 0) > 0 && stats.approved === stats.questions;

  if (!a.profile_id) {
    return (
      <Notice
        tone="warn"
        title="Choose a handwriting profile first"
        action={
          <Button size="sm" onClick={() => go("handwriting")}>
            Choose handwriting
          </Button>
        }
      >
        Pages are rendered in the handwriting profile you select for this assignment.
      </Notice>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Sheet title="Render and export">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            A preview includes unapproved answers so you can check the layout early. The final PDF needs every included
            answer approved{allApproved ? ", which they are" : ` (${stats.approved ?? 0} of ${stats.questions ?? 0} so far)`}.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              icon={<ScanEye className="size-4" aria-hidden />}
              loading={starting === "preview"}
              disabled={busy}
              onClick={() => start("preview")}
            >
              Render preview
            </Button>
            <Button
              variant="primary"
              icon={<FileCheck2 className="size-4" aria-hidden />}
              loading={starting === "final"}
              disabled={busy}
              onClick={() => start("final")}
            >
              Export final PDF
            </Button>
            <Button variant="ghost" icon={<FileText className="size-4" aria-hidden />} loading={starting === "digital"} disabled={busy} onClick={() => start("digital")}>
              Typed copy
            </Button>
            <Button
              variant="ghost"
              icon={<FileText className="size-4" aria-hidden />}
              loading={starting === "review"}
              disabled={busy || handwritten.length === 0}
              onClick={() => start("review")}
              title={handwritten.length === 0 ? "Render the handwritten pages first" : undefined}
            >
              Review copy
            </Button>
          </div>
          <p className="text-[12.5px] text-muted">
            The typed copy is a plain document of questions and answers. The review copy puts the handwritten pages next
            to the typed text for checking.
          </p>
          {jobId && action && <JobProgress job={job.job} label={label[action]} error={job.error} />}
          {error && <Notice tone="error" title="That did not work">{error}</Notice>}
          {unapproved && (
            <Notice
              tone="warn"
              title="Some answers are not approved yet"
              action={
                <Button size="sm" onClick={() => go("review")}>
                  Review answers
                </Button>
              }
            >
              Approve these before exporting the final PDF: {unapproved
                .map((u) => {
                  const i = a.questions.findIndex((q) => q.id === u);
                  return i >= 0 ? `item ${i + 1} (no number)` : `Question ${u}`;
                })
                .join(", ")}
              .
            </Notice>
          )}
        </div>
      </Sheet>

      {selected ? (
        <Sheet
          title={`${KIND_LABEL[selected.kind] ?? selected.kind}${selected.is_preview ? " preview" : ""}`}
          description={`${selected.page_count} ${selected.page_count === 1 ? "page" : "pages"}, ${formatBytes(selected.size_bytes)}, made ${formatDate(selected.created_at)}`}
          actions={
            <>
              <a
                href={urls.download(selected.id, true)}
                target="_blank"
                rel="noopener"
                className="inline-flex h-8 items-center gap-1.5 rounded-[5px] px-3 text-[13px] font-medium text-ink hover:bg-ink-wash"
              >
                <ExternalLink className="size-3.5" aria-hidden /> Open PDF
              </a>
              <a
                href={urls.download(selected.id)}
                download
                className="inline-flex h-8 items-center gap-1.5 rounded-[5px] bg-ink px-3 text-[13px] font-medium text-white hover:bg-ink-hover"
              >
                <Download className="size-3.5" aria-hidden /> Download
              </a>
            </>
          }
        >
          <div className="flex flex-col gap-5">
            {selected.kind === "handwritten" && <Warnings warnings={selected.warnings} />}
            {selected.kind === "handwritten" ? (
              <Viewer key={selected.id} r={selected} />
            ) : (
              <p className="text-sm text-muted">Page images are only made for handwritten renders. Open the PDF to read it.</p>
            )}
          </div>
        </Sheet>
      ) : (
        !busy && (
          <p className="rounded-sheet border border-dashed border-line-strong px-5 py-8 text-center text-sm text-muted">
            Nothing rendered yet. Render a preview to see your answers in handwriting.
          </p>
        )
      )}

      {sorted.length > 0 && (
        <Sheet title="History" description="Every preview and export for this assignment.">
          <ul className="-my-2 divide-y divide-line">
            {sorted.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                <button
                  type="button"
                  onClick={() => setSelectedId(r.id)}
                  className={cx("min-w-0 text-left", selected?.id === r.id && "text-ink")}
                  aria-current={selected?.id === r.id ? "true" : undefined}
                >
                  <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {KIND_LABEL[r.kind] ?? r.kind}
                    {r.is_preview ? <Badge>Preview</Badge> : <Badge tone="ok">Final</Badge>}
                    {r.warnings.length > 0 && (
                      <Badge tone="warn">
                        {r.warnings.length} {r.warnings.length === 1 ? "warning" : "warnings"}
                      </Badge>
                    )}
                    {selected?.id === r.id && <Badge tone="ink">Showing</Badge>}
                  </span>
                  <span className="block text-[12.5px] text-muted">
                    {r.page_count} pages, {formatBytes(r.size_bytes)}, {formatDate(r.created_at)}
                    {r.downloaded_at ? `, downloaded ${formatDate(r.downloaded_at)}` : ""}
                  </span>
                </button>
                <span className="flex gap-1">
                  <a
                    href={urls.download(r.id)}
                    download
                    className="inline-flex h-8 items-center gap-1.5 rounded-[5px] px-3 text-[13px] font-medium text-ink hover:bg-ink-wash"
                  >
                    <Download className="size-3.5" aria-hidden /> Download
                  </a>
                  <Button
                    size="sm"
                    variant="quiet-danger"
                    aria-label="Delete this file"
                    loading={deleting === r.id}
                    onClick={() => remove(r.id)}
                    icon={<Trash2 className="size-3.5" aria-hidden />}
                  />
                </span>
              </li>
            ))}
          </ul>
        </Sheet>
      )}
    </div>
  );
}
