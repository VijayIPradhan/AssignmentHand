"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCheck, CheckCircle2, History, RotateCcw, Sparkles, Undo2 } from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import type { Answer, Question } from "@/lib/types";
import { RichText } from "../RichText";
import { Badge, Button, cx, formatDate, JobProgress, Notice, SaveState, StatusBadge } from "../ui";
import { useWorkspace } from "./context";
import { NotConfiguredNotice, useGenerate } from "./StepGenerate";

function countWords(text: string) {
  const stripped = text.replace(/\$\$[\s\S]*?\$\$|\$[^$]*\$/g, " x ").replace(/[|*#>-]/g, " ");
  return stripped.split(/\s+/).filter((w) => /\w/.test(w)).length;
}

const FLAG_LABEL: Record<string, string> = {
  ambiguous_question: "Ambiguous question",
  insufficient_information: "Not enough information",
  needs_verification: "Check this",
  other: "Note",
};

function FormattingHelp() {
  return (
    <details className="rounded-[5px] border border-line bg-sheet px-4 py-3 text-sm">
      <summary className="cursor-pointer font-medium text-ink">Formatting that the handwriting understands</summary>
      <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
        <dt className="text-muted">Paragraphs</dt>
        <dd>Leave an empty line between paragraphs.</dd>
        <dt className="text-muted">Lists</dt>
        <dd>
          Start lines with <code className="rounded bg-paper-deep px-1">- </code>
        </dd>
        <dt className="text-muted">Tables</dt>
        <dd>
          <code className="rounded bg-paper-deep px-1">| Name | Value |</code> on each row, header first
        </dd>
        <dt className="text-muted">Maths</dt>
        <dd>
          <code className="rounded bg-paper-deep px-1">$x^2 + y^2$</code> inline,{" "}
          <code className="rounded bg-paper-deep px-1">$$\frac{"{a}{b}"}$$</code> on its own line
        </dd>
        <dt className="text-muted">Underline</dt>
        <dd>
          <code className="rounded bg-paper-deep px-1">**words to underline**</code>
        </dd>
      </dl>
    </details>
  );
}

type SaveStatus = "idle" | "dirty" | "saving" | "saved" | "error";

function AnswerEditor({
  q,
  target,
  canGenerate,
  onRegenerate,
  regenerating,
  busyAll,
}: {
  q: Question;
  target: number | null;
  canGenerate: boolean;
  onRegenerate: () => void;
  regenerating: boolean;
  busyAll: boolean;
}) {
  const { update, reload } = useWorkspace();
  const ans = q.answer ?? null;
  const [content, setContent] = useState(ans?.content ?? "");
  const [seenRev, setSeenRev] = useState<number | null>(ans?.revision ?? null);
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  // Adopt server changes (e.g. a finished generation) when there are no local edits.
  const serverRev = ans?.revision ?? null;
  if (serverRev !== seenRev && (status === "idle" || status === "saved")) {
    setSeenRev(serverRev);
    setContent(ans?.content ?? "");
  }

  const applyAnswer = (next: Answer) => {
    setSeenRev(next.revision);
    update((prev) => ({
      ...prev,
      questions: prev.questions.map((x) => (x.id === q.id ? { ...x, answer: next } : x)),
    }));
  };

  // Saves run one after another so each sends the revision the previous one produced.
  const chain = useRef<Promise<boolean>>(Promise.resolve(true));
  const revRef = useRef<number | null>(seenRev);
  useEffect(() => {
    revRef.current = seenRev;
  }, [seenRev]);

  function save(text: string, force = false): Promise<boolean> {
    const run = async () => {
      setStatus("saving");
      setError(null);
      try {
        const next = await api.writeAnswer(q.id, text, force ? null : revRef.current);
        revRef.current = next.revision;
        applyAnswer(next);
        setConflict(false);
        setStatus("saved");
        void reload();
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) setConflict(true);
        else setError(errorMessage(err));
        setStatus("error");
        return false;
      }
    };
    chain.current = chain.current.then(run, run);
    return chain.current;
  }

  const autosave = useDebounced((text: string) => void save(text), 1000, true);

  useEffect(() => {
    if (status !== "dirty") return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  async function act(label: string, fn: () => Promise<Answer>) {
    autosave.flush();
    setBusy(label);
    setError(null);
    try {
      const next = await fn();
      applyAnswer(next);
      setContent(next.content);
      setStatus("idle");
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function loadLatest() {
    autosave.cancel();
    const fresh = await reload();
    const fq = fresh?.questions.find((x) => x.id === q.id);
    setContent(fq?.answer?.content ?? "");
    setSeenRev(fq?.answer?.revision ?? null);
    setConflict(false);
    setStatus("idle");
  }

  const words = countWords(content);
  const st = ans?.status ?? "pending";
  const approved = st === "approved";
  const generating = st === "generating";
  const history = [...(ans?.history ?? [])].reverse();
  const id = `answer-${q.id}`;

  return (
    <article
      className={cx("rounded-sheet border bg-sheet", approved ? "border-[#c4dfcc]" : "border-line")}
      aria-labelledby={`${id}-h`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <h3 id={`${id}-h`} className="text-lg">
            {q.number ? `Question ${q.number}` : "Question"}
            {q.marks !== null && q.marks !== undefined && (
              <span className="ml-2 font-sans text-[13px] text-muted">{q.marks} marks</span>
            )}
          </h3>
          <RichText text={q.text} className="mt-1 text-[14px] text-graphite" />
        </div>
        <StatusBadge status={st} />
      </header>
      <div className="flex flex-col gap-3 px-5 py-4">
        {ans?.flags && ans.flags.length > 0 && (
          <ul className="flex flex-col gap-2" aria-label="Things to check in this answer">
            {ans.flags.map((f, i) => (
              <li key={i} className="flex gap-2 rounded-[5px] border border-[#ecd9a8] bg-warn-soft px-3 py-2 text-[13.5px]">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                <span>
                  <span className="font-medium text-warn">{FLAG_LABEL[f.type] ?? f.type.replace(/_/g, " ")}: </span>
                  {f.message}
                </span>
              </li>
            ))}
          </ul>
        )}
        {st === "failed" && ans?.error && <Notice tone="error" title="Generation failed">{ans.error}</Notice>}
        {conflict && (
          <Notice
            tone="error"
            title="This answer was changed somewhere else"
            action={
              <div className="flex gap-2">
                <Button size="sm" onClick={loadLatest}>
                  Load latest
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void save(content, true)}>
                  Keep mine
                </Button>
              </div>
            }
          >
            Your latest edit was not saved. Load the latest version (your edit is lost) or overwrite it with yours.
          </Notice>
        )}
        <label htmlFor={id} className="sr-only">
          Answer to question {q.number}
        </label>
        <textarea
          id={id}
          value={content}
          disabled={generating}
          onChange={(e) => {
            setContent(e.target.value);
            setStatus("dirty");
            autosave.call(e.target.value);
          }}
          onBlur={() => autosave.flush()}
          rows={Math.min(22, Math.max(6, content.split("\n").length + 2))}
          placeholder={generating ? "An answer is being written" : "Write your answer here"}
          className="ruled w-full resize-y rounded-[5px] border border-line-strong px-3 text-[15px] text-graphite focus:border-ink focus:outline-none disabled:opacity-60"
          aria-describedby={`${id}-meta`}
        />
        <div id={`${id}-meta`} className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted">
          <span className="tabular-nums">
            {words} {words === 1 ? "word" : "words"}
            {target ? (
              <span className={cx(words > target * 1.3 || (words > 0 && words < target * 0.6) ? "text-warn" : undefined)}>
                {" "}
                of about {target}
              </span>
            ) : null}
            {ans?.model ? `, drafted by ${ans.model}` : ""}
          </span>
          <span className="flex items-center gap-3">
            <SaveState state={status} />
            {error && <span className="text-margin">{error}</span>}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {approved ? (
            <Button
              size="sm"
              icon={<Undo2 className="size-3.5" aria-hidden />}
              loading={busy === "unapprove"}
              onClick={() => ans && act("unapprove", () => api.unapprove(ans.id))}
            >
              Unapprove
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              icon={<CheckCircle2 className="size-3.5" aria-hidden />}
              loading={busy === "approve"}
              disabled={!content.trim() || generating || status === "saving"}
              onClick={async () => {
                // Make sure the text on screen is what gets approved.
                if (status === "dirty" || !ans) {
                  autosave.cancel();
                  if (!(await save(content))) return;
                }
                const fresh = await reload();
                const fa = fresh?.questions.find((x) => x.id === q.id)?.answer;
                if (fa) await act("approve", () => api.approve(fa.id));
              }}
            >
              Approve
            </Button>
          )}
          {canGenerate && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Sparkles className="size-3.5" aria-hidden />}
              loading={regenerating}
              disabled={busyAll || generating}
              onClick={onRegenerate}
            >
              {content.trim() ? "Regenerate" : "Generate"}
            </Button>
          )}
          {history.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              icon={<History className="size-3.5" aria-hidden />}
              aria-expanded={showHistory}
              onClick={() => setShowHistory((v) => !v)}
            >
              Versions ({history.length})
            </Button>
          )}
        </div>
        {showHistory && ans && (
          <ol className="flex flex-col gap-2 border-t border-line pt-3" aria-label="Earlier versions">
            {history.map((h) => (
              <li key={`${h.revision}-${h.at}`} className="rounded-[5px] border border-line bg-paper px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-muted">
                  <span>
                    Version {h.revision}, {h.status}, saved {formatDate(h.at)}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<RotateCcw className="size-3.5" aria-hidden />}
                    loading={busy === `restore-${h.revision}`}
                    onClick={() => act(`restore-${h.revision}`, () => api.restore(ans.id, h.revision))}
                  >
                    Restore
                  </Button>
                </div>
                <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-[13px]">{h.content}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </article>
  );
}

export function StepReview() {
  const { a, reload, go } = useWorkspace();
  const gen = useGenerate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenId, setRegenId] = useState<string | null>(null);
  const included = a.questions.filter((q) => !q.excluded);
  const ai = a.stats?.ai;
  const canGenerate = !!ai?.configured && !gen.notConfigured && a.extraction_confirmed;
  const running = a.active_jobs.some((j) => j.type === "generate") || gen.job.running;
  const s = a.stats ?? {};
  const unapprovedWithText = included.filter((q) => q.answer?.content.trim() && q.answer.status !== "approved").length;

  async function approveAll() {
    setBusy(true);
    setError(null);
    try {
      await api.approveAll(a.id);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!included.length) {
    return (
      <Notice tone="warn" title="No questions to answer" action={<Button size="sm" onClick={() => go("questions")}>Review questions</Button>}>
        Add or include at least one question first.
      </Notice>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-3xl text-sm text-muted">
        Read every answer and edit it until you are happy, then approve it. Changes save automatically. Only approved
        answers go into the final export; editing an approved answer un-approves it.
      </p>
      <div className="sticky top-14 z-20 -mx-1 flex flex-wrap items-center gap-3 rounded-[5px] border border-line bg-paper/95 px-3 py-2 backdrop-blur">
        <span className="text-sm">
          <span className="font-medium">{s.approved ?? 0}</span> of {s.questions ?? included.length} approved,{" "}
          {s.words ?? 0} words
        </span>
        <Button
          size="sm"
          variant="primary"
          icon={<CheckCheck className="size-3.5" aria-hidden />}
          loading={busy}
          disabled={!unapprovedWithText}
          onClick={approveAll}
          className="ml-auto"
        >
          Approve all written answers
        </Button>
        {(s.approved ?? 0) === (s.questions ?? -1) && (
          <Button size="sm" onClick={() => go("handwriting")}>
            Continue to handwriting
          </Button>
        )}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {gen.notConfigured && <NotConfiguredNotice onWrite={() => undefined} />}
      {gen.jobId && <JobProgress job={gen.job.job} label="Writing answers" error={gen.job.error} />}
      {gen.error && <Notice tone="error">{gen.error}</Notice>}
      {!ai?.configured && (
        <Badge tone="neutral" className="self-start">
          AI drafting is not set up; write the answers yourself
        </Badge>
      )}
      <FormattingHelp />
      {included.map((q) => (
        <AnswerEditor
          key={q.id}
          q={q}
          target={a.preferences?.target_words ?? null}
          canGenerate={canGenerate}
          busyAll={running}
          regenerating={regenId === q.id && (gen.starting === q.id || running)}
          onRegenerate={() => {
            setRegenId(q.id);
            void gen.start([q.id], true, q.id);
          }}
        />
      ))}
    </div>
  );
}
