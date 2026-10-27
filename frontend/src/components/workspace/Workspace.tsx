"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Check, Trash2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { Assignment } from "@/lib/types";
import { ActiveJobs } from "../shared";
import { Button, ConfirmDialog, cx, ErrorState, LoadingBlock, Notice, StatusBadge } from "../ui";
import { Ctx, STEPS, type StepKey, type WorkspaceCtx } from "./context";
import { StepExport } from "./StepExport";
import { StepGenerate } from "./StepGenerate";
import { StepHandwriting } from "./StepHandwriting";
import { StepPreferences } from "./StepPreferences";
import { StepQuestions } from "./StepQuestions";
import { StepReview } from "./StepReview";
import { StepUpload } from "./StepUpload";

function stepDone(a: Assignment, key: StepKey): boolean {
  const s = a.stats ?? {};
  switch (key) {
    case "upload":
      return a.documents.some((d) => d.role === "assignment");
    case "questions":
      return a.extraction_confirmed;
    case "preferences":
      return Object.keys(a.preferences ?? {}).length > 0;
    case "generate":
      return (s.answered ?? 0) > 0;
    case "review":
      return (s.questions ?? 0) > 0 && s.approved === s.questions;
    case "handwriting":
      return !!a.profile_id;
    case "export":
      return a.rendered.some((r) => !r.is_preview && r.kind === "handwritten");
  }
}

function suggestedStep(a: Assignment): StepKey {
  if (!a.extraction_confirmed) return "questions";
  const s = a.stats ?? {};
  if (!(s.answered ?? 0)) return "generate";
  if ((s.approved ?? 0) < (s.questions ?? 0)) return "review";
  if (!a.profile_id) return "handwriting";
  return "export";
}

export function Workspace({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const res = useResource<Assignment>(`assignment:${id}`, () => api.assignment(id));
  const { reload, setData } = res;
  const a = res.data;
  const [watching, setWatching] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const rawStep = params.get("step") as StepKey | null;
  const step: StepKey | null = rawStep && STEPS.some((s) => s.key === rawStep) ? rawStep : null;
  const current: StepKey = step ?? (a ? suggestedStep(a) : "questions");

  const go = useCallback(
    (k: StepKey) => {
      router.replace(`${pathname}?step=${k}`, { scroll: false });
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    [router, pathname],
  );

  // Move focus to the step heading when the step changes (not on first load).
  const lastStep = useRef<string | null>(null);
  useEffect(() => {
    const cur = rawStep ?? "";
    if (lastStep.current !== null && lastStep.current !== cur) {
      headingRef.current?.focus({ preventScroll: true });
    }
    lastStep.current = cur;
  }, [rawStep]);

  // Poll while background work is running for this assignment.
  const active = a?.active_jobs.length ?? 0;
  useEffect(() => {
    if (!active && !watching) return;
    const t = setInterval(async () => {
      const fresh = await reload(true);
      if (fresh && fresh.active_jobs.length === 0) setWatching(0);
    }, 1200);
    return () => clearInterval(t);
  }, [active, watching, reload]);

  const ctx = useMemo<WorkspaceCtx | null>(
    () =>
      a
        ? {
            a,
            reload: () => reload(true),
            replace: (next) => setData(next),
            update: (fn) => setData((prev) => (prev ? fn(prev) : prev)),
            go,
            watch: () => setWatching((w) => w + 1),
          }
        : null,
    [a, reload, setData, go],
  );

  async function remove() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteAssignment(id);
      router.push("/assignments");
    } catch (err) {
      setDeleteError(errorMessage(err));
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  if (res.loading && !a) return <LoadingBlock label="Loading assignment" />;
  if (res.error && !a) return <ErrorState message={res.error} onRetry={() => void reload()} />;
  if (!a || !ctx) return null;

  const idx = STEPS.findIndex((s) => s.key === current);
  const s = a.stats ?? {};

  return (
    <Ctx.Provider value={ctx}>
      <header className="mb-8">
        <Link href="/assignments" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Assignments
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <h1 className="page-title text-[1.9rem] leading-tight sm:text-[2.2rem]">{a.title}</h1>
            <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 pl-[1.1rem] text-sm text-muted">
              <StatusBadge status={a.status} />
              {a.subject && <span>{a.subject}</span>}
              {(s.questions ?? 0) > 0 && (
                <span>
                  {s.questions} {s.questions === 1 ? "question" : "questions"}, {s.answered ?? 0} answered,{" "}
                  {s.approved ?? 0} approved
                </span>
              )}
            </p>
          </div>
          <Button
            variant="quiet-danger"
            size="sm"
            icon={<Trash2 className="size-3.5" aria-hidden />}
            onClick={() => setConfirmDelete(true)}
          >
            Delete assignment
          </Button>
        </div>
        {deleteError && (
          <Notice tone="error" className="mt-4">
            {deleteError}
          </Notice>
        )}
      </header>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Assignment steps" className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <ol className="relative -mx-4 flex gap-1 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-col lg:px-0">
            {STEPS.map((st, i) => {
              const done = stepDone(a, st.key);
              const here = st.key === current;
              return (
                <li key={st.key} className="shrink-0">
                  <button
                    type="button"
                    onClick={() => go(st.key)}
                    aria-current={here ? "step" : undefined}
                    className={cx(
                      "flex w-full items-center gap-3 rounded-[5px] px-2.5 py-2 text-left text-sm transition-colors",
                      here ? "bg-sheet text-ink shadow-[inset_2px_0_0_var(--color-margin)] font-medium" : "text-muted hover:bg-sheet/70 hover:text-ink",
                    )}
                  >
                    <span
                      className={cx(
                        "flex size-6 shrink-0 items-center justify-center rounded-full border text-[12px] tabular-nums",
                        done ? "border-ink bg-ink text-white" : here ? "border-ink text-ink" : "border-line-strong text-muted",
                      )}
                      aria-hidden
                    >
                      {done ? <Check className="size-3.5" /> : i + 1}
                    </span>
                    <span className="whitespace-nowrap">
                      {st.label}
                      <span className="sr-only">{done ? " (done)" : ""}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="min-w-0">
          {a.active_jobs.length > 0 && (
            <div className="mb-6">
              <ActiveJobs jobs={a.active_jobs} />
            </div>
          )}
          <h2 ref={headingRef} tabIndex={-1} className="mb-1 text-2xl outline-none">
            <span className="mr-2 text-muted tabular-nums">{idx + 1}.</span>
            {STEPS[idx].label}
          </h2>
          <div className="mt-5">
            {current === "upload" && <StepUpload />}
            {current === "questions" && <StepQuestions />}
            {current === "preferences" && <StepPreferences />}
            {current === "generate" && <StepGenerate />}
            {current === "review" && <StepReview />}
            {current === "handwriting" && <StepHandwriting />}
            {current === "export" && <StepExport />}
          </div>
          <div className="mt-10 flex justify-between border-t border-line pt-5">
            {idx > 0 ? (
              <Button variant="ghost" onClick={() => go(STEPS[idx - 1].key)}>
                Back: {STEPS[idx - 1].label}
              </Button>
            ) : (
              <span />
            )}
            {idx < STEPS.length - 1 && (
              <Button onClick={() => go(STEPS[idx + 1].key)}>Next: {STEPS[idx + 1].label}</Button>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete “${a.title}”?`}
        confirmLabel="Delete assignment"
        danger
        busy={deleting}
        onConfirm={remove}
        onClose={() => setConfirmDelete(false)}
      >
        The uploaded PDF, reference material, all questions and answers, and every preview and exported PDF for this
        assignment are permanently deleted.
      </ConfirmDialog>
    </Ctx.Provider>
  );
}
