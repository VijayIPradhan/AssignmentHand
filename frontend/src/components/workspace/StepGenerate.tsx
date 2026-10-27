"use client";

import { useState } from "react";
import { PenLine, Sparkles } from "lucide-react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useJob } from "@/lib/hooks";
import { Button, Checkbox, JobProgress, Notice, Sheet, StatusBadge } from "../ui";
import { useWorkspace } from "./context";

export function useGenerate() {
  const { a, reload, watch } = useWorkspace();
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [starting, setStarting] = useState<string | null>(null);
  const job = useJob(jobId, (j) => {
    if (j.status === "failed") setError(j.error ?? "Generation failed.");
    void reload();
  });

  async function start(questionIds: string[] | null, includeApproved: boolean, label = "all") {
    setStarting(label);
    setError(null);
    setNotConfigured(false);
    try {
      const j = await api.generate(a.id, { question_ids: questionIds, include_approved: includeApproved });
      setJobId(j.id);
      watch(j.id);
      await reload();
    } catch (err) {
      if (err instanceof ApiError && err.notConfigured) setNotConfigured(true);
      else setError(errorMessage(err));
    } finally {
      setStarting(null);
    }
  }

  return { start, job, error, notConfigured, starting, jobId };
}

export function NotConfiguredNotice({ onWrite }: { onWrite: () => void }) {
  return (
    <Notice
      tone="warn"
      title="AI drafting is not set up on this server"
      action={
        <Button size="sm" icon={<PenLine className="size-3.5" aria-hidden />} onClick={onWrite}>
          Write answers myself
        </Button>
      }
    >
      Answers cannot be generated until an administrator configures an AI provider. You can write each answer yourself
      in the next step; everything else (approval, handwriting, export) works the same.
    </Notice>
  );
}

export function StepGenerate() {
  const { a, go } = useWorkspace();
  const [includeApproved, setIncludeApproved] = useState(false);
  const gen = useGenerate();
  const ai = a.stats?.ai;
  const included = a.questions.filter((q) => !q.excluded);
  const running = a.active_jobs.some((j) => j.type === "generate") || gen.job.running;
  const approved = included.filter((q) => q.answer?.status === "approved").length;

  if (!a.extraction_confirmed) {
    return (
      <Notice
        tone="warn"
        title="Confirm the questions first"
        action={
          <Button size="sm" onClick={() => go("questions")}>
            Review questions
          </Button>
        }
      >
        Answers are written only for questions you have checked and confirmed.
      </Notice>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-3xl text-sm text-muted">
        Draft answers with AI, then read and edit every one before approving it. Approved answers are never overwritten
        unless you choose to include them.
      </p>
      {(ai && !ai.configured) || gen.notConfigured ? (
        <NotConfiguredNotice onWrite={() => go("review")} />
      ) : (
        <Sheet
          title="Generate answers"
          description={ai ? `Using ${ai.model}${ai.provider ? ` (${ai.provider})` : ""}.` : undefined}
        >
          <div className="flex flex-col gap-4">
            <p className="text-sm">
              {included.length} included {included.length === 1 ? "question" : "questions"}
              {approved ? `, ${approved} already approved` : ""}.
            </p>
            <Checkbox
              label="Also rewrite approved answers"
              hint="Their current text is kept in the version history."
              checked={includeApproved}
              onChange={setIncludeApproved}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                icon={<Sparkles className="size-4" aria-hidden />}
                loading={gen.starting === "all"}
                disabled={running || included.length === 0}
                onClick={() => gen.start(null, includeApproved)}
              >
                Generate answers
              </Button>
              <Button variant="ghost" onClick={() => go("review")}>
                Write or edit answers myself
              </Button>
            </div>
          </div>
        </Sheet>
      )}
      {gen.jobId && <JobProgress job={gen.job.job} label="Writing answers" error={gen.job.error} />}
      {gen.error && <Notice tone="error" title="Generation did not complete">{gen.error}</Notice>}

      <Sheet title="Questions">
        <ul className="-my-2 divide-y divide-line">
          {included.map((q) => (
            <li key={q.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">
                  <span className="font-medium">{q.number ? `${q.number}. ` : ""}</span>
                  {q.text.split("\n")[0]}
                </p>
                {q.answer?.error && <p className="text-[12.5px] text-margin">{q.answer.error}</p>}
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={q.answer?.status ?? "pending"} />
                {ai?.configured && !gen.notConfigured && (
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={gen.starting === q.id}
                    disabled={running}
                    onClick={() => gen.start([q.id], true, q.id)}
                  >
                    {q.answer?.content ? "Regenerate" : "Generate"}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Sheet>
    </div>
  );
}
