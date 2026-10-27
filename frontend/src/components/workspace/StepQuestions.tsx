"use client";

import { Fragment, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  CheckCheck,
  Combine,
  Eye,
  EyeOff,
  Image as ImageIcon,
  Pencil,
  Plus,
  RotateCw,
  Scissors,
  Trash2,
} from "lucide-react";
import { api, errorMessage, urls } from "@/lib/api";
import type { DocPage, Question } from "@/lib/types";
import { RichText } from "../RichText";
import { Badge, Button, ConfirmDialog, cx, EmptyState, Notice, Sheet, TextArea, TextField } from "../ui";
import { useWorkspace } from "./context";

function PageViewer({ highlight }: { highlight: number | null }) {
  const { a } = useWorkspace();
  const doc = a.documents.find((d) => d.role === "assignment");
  const refs = useRef<Record<number, HTMLElement | null>>({});
  if (!doc) return null;
  const pages: DocPage[] = doc.pages.length
    ? doc.pages
    : Array.from({ length: doc.page_count }, (_, i) => ({ index: i, width: 0, height: 0 }));
  return (
    <div className="flex flex-col gap-4" aria-label="Original pages">
      {pages.map((pg, i) => (
        <figure
          key={i}
          id={`src-page-${i}`}
          ref={(el) => {
            refs.current[i] = el;
          }}
          className={cx(
            "overflow-hidden rounded border bg-white transition-shadow",
            highlight === i ? "border-ink shadow-[0_0_0_3px_rgb(27_42_107/0.15)]" : "border-line",
          )}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={urls.docPage(a.id, doc.id, i + 1)} alt={`Page ${i + 1} of the assignment`} className="block w-full" loading="lazy" />
          <figcaption className="border-t border-line bg-paper px-3 py-1.5 text-[12.5px] text-muted">
            Page {i + 1}
            {pg.issues && pg.issues.length > 0 && (
              <ul className="mt-1 flex flex-col gap-0.5">
                {pg.issues.map((iss) => (
                  <li key={iss} className="flex gap-1.5 text-warn">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                    {iss}
                  </li>
                ))}
              </ul>
            )}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function InstructionsEditor() {
  const { a, replace } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(a.instructions ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      replace(await api.updateAssignment(a.id, { instructions: text }));
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet
      title="General instructions"
      description="Instructions that apply to the whole assignment. They are given to the answer writer with every question."
      actions={
        !editing && (
          <Button
            size="sm"
            icon={<Pencil className="size-3.5" aria-hidden />}
            onClick={() => {
              setText(a.instructions ?? "");
              setEditing(true);
            }}
          >
            Edit
          </Button>
        )
      }
    >
      {editing ? (
        <div className="flex flex-col gap-3">
          <TextArea label="Instructions" rows={5} value={text} onChange={(e) => setText(e.target.value)} />
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex gap-2">
            <Button variant="primary" loading={busy} onClick={save}>
              Save instructions
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : a.instructions ? (
        <RichText text={a.instructions} className="text-sm" />
      ) : (
        <p className="text-sm text-muted">No general instructions were found.</p>
      )}
    </Sheet>
  );
}

interface Draft {
  number: string;
  text: string;
  marks: string;
  instructions: string;
  context: string;
}

const toDraft = (q: Question): Draft => ({
  number: q.number,
  text: q.text,
  marks: q.marks === null || q.marks === undefined ? "" : String(q.marks),
  instructions: q.instructions ?? "",
  context: q.context ?? "",
});

function QuestionItem({
  q,
  index,
  total,
  selected,
  onSelect,
  onMove,
  onShowPage,
  onAddAfter,
}: {
  q: Question;
  index: number;
  total: number;
  selected: boolean;
  onSelect: (v: boolean) => void;
  onMove: (dir: -1 | 1) => void;
  onShowPage: (page: number) => void;
  onAddAfter: () => void;
}) {
  const { a, update, reload } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => toDraft(q));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  const patchLocal = (nq: Question) =>
    update((prev) => ({ ...prev, questions: prev.questions.map((x) => (x.id === nq.id ? { ...nq, answer: x.answer } : x)) }));

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run("save", async () => {
      const marks = draft.marks.trim();
      const nq = await api.updateQuestion(q.id, {
        number: draft.number,
        text: draft.text,
        marks: marks ? Number(marks) : null,
        clear_marks: !marks,
        instructions: draft.instructions,
        context: draft.context,
        reviewed: true,
      });
      patchLocal(nq);
      setEditing(false);
    });

  const split = () =>
    run("split", async () => {
      const el = textRef.current;
      const at = el ? el.selectionStart : 0;
      if (!at || at >= draft.text.length) throw new Error("Place the cursor in the text where the second question should start.");
      if (draft.text !== q.text) {
        await api.updateQuestion(q.id, { text: draft.text });
      }
      await api.splitQuestion(q.id, at);
      await reload();
      setEditing(false);
    });

  const lowConfidence = q.confidence < 0.6;
  const pageList = Array.from(new Set(q.pages.map((p) => p.page)));

  return (
    <li
      className={cx(
        "rounded-sheet border bg-sheet",
        q.excluded ? "border-dashed border-line-strong opacity-75" : q.needs_review ? "border-[#ecd9a8]" : "border-line",
      )}
    >
      <div className="flex gap-3 p-4">
        <input
          type="checkbox"
          className="mt-1.5 size-4 shrink-0 accent-ink"
          checked={selected}
          onChange={(e) => onSelect(e.target.checked)}
          aria-label={`Select question ${q.number || index + 1} for merging`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-serif text-lg text-ink">{q.number ? `Question ${q.number}` : `Item ${index + 1}`}</span>
            {q.marks !== null && q.marks !== undefined && <Badge>{q.marks} marks</Badge>}
            {q.excluded && <Badge>Excluded</Badge>}
            {q.needs_review && <Badge tone="warn">Check this</Badge>}
            {!q.needs_review && !q.excluded && (
              <Badge tone="ok">
                <Check className="size-3" aria-hidden /> Reviewed
              </Badge>
            )}
            {q.has_visual && (
              <Badge tone="ink">
                <ImageIcon className="size-3" aria-hidden /> Has a figure
              </Badge>
            )}
            {q.origin === "user" && <Badge>Added by you</Badge>}
            <span className={cx("text-[12px]", lowConfidence ? "text-warn" : "text-faint")}>
              {Math.round(q.confidence * 100)}% confidence
            </span>
          </div>

          {q.needs_review && q.review_reasons.length > 0 && (
            <ul className="mt-2 flex flex-col gap-0.5 text-[13px] text-warn">
              {q.review_reasons.map((r) => (
                <li key={r} className="flex gap-1.5">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                  {r}
                </li>
              ))}
            </ul>
          )}

          {editing ? (
            <div className="mt-3 flex flex-col gap-3">
              <div className="grid gap-3 sm:grid-cols-[120px_120px]">
                <TextField
                  label="Number"
                  value={draft.number}
                  maxLength={32}
                  onChange={(e) => setDraft({ ...draft, number: e.target.value })}
                />
                <TextField
                  label="Marks"
                  type="number"
                  min={0}
                  step="0.5"
                  value={draft.marks}
                  onChange={(e) => setDraft({ ...draft, marks: e.target.value })}
                />
              </div>
              <TextArea
                ref={textRef}
                label="Question text"
                rows={Math.min(16, Math.max(4, draft.text.split("\n").length + 1))}
                value={draft.text}
                maxLength={20000}
                onChange={(e) => setDraft({ ...draft, text: e.target.value })}
                hint="Tables can be written with pipes: | a | b |. To split, put the cursor where the next question starts."
              />
              <TextArea
                label="Instructions for this question"
                rows={2}
                value={draft.instructions}
                onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
              />
              <TextArea
                label="Context (shared passage, data or scenario)"
                rows={3}
                value={draft.context}
                onChange={(e) => setDraft({ ...draft, context: e.target.value })}
              />
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" loading={busy === "save"} onClick={save} disabled={!draft.text.trim()}>
                  Save question
                </Button>
                <Button icon={<Scissors className="size-3.5" aria-hidden />} loading={busy === "split"} onClick={split}>
                  Split at cursor
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setDraft(toDraft(q));
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <RichText text={q.text} className="mt-2" />
              {q.instructions && (
                <p className="mt-2 text-[13.5px] text-muted">
                  <span className="font-medium text-graphite">Instructions: </span>
                  {q.instructions}
                </p>
              )}
              {q.context && (
                <details className="mt-2 text-[13.5px]">
                  <summary className="cursor-pointer text-ink">Context</summary>
                  <RichText text={q.context} className="mt-1 text-[13.5px] text-muted" />
                </details>
              )}
            </>
          )}

          {q.figures.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-2" aria-label="Figures">
              {q.figures.map((f) => (
                <li key={f.key}>
                  <a href={urls.figure(a.id, f.key)} target="_blank" rel="noopener" className="block rounded border border-line bg-white p-1 hover:border-ink-soft">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={urls.figure(a.id, f.key)} alt={`Figure from page ${f.page + 1}`} className="h-24 w-auto" loading="lazy" />
                  </a>
                </li>
              ))}
            </ul>
          )}

          {pageList.length > 0 && (
            <p className="mt-2 flex flex-wrap gap-1.5 text-[12.5px] text-muted">
              On
              {pageList.map((pg) => (
                <button
                  key={pg}
                  type="button"
                  onClick={() => onShowPage(pg)}
                  className="text-ink underline decoration-rule underline-offset-2 hover:decoration-ink"
                >
                  page {pg + 1}
                </button>
              ))}
            </p>
          )}

          {error && (
            <Notice tone="error" className="mt-3">
              {error}
            </Notice>
          )}

          {!editing && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Button
                size="sm"
                icon={<Pencil className="size-3.5" aria-hidden />}
                onClick={() => {
                  setDraft(toDraft(q));
                  setEditing(true);
                }}
              >
                Edit
              </Button>
              {q.needs_review && (
                <Button
                  size="sm"
                  icon={<Check className="size-3.5" aria-hidden />}
                  loading={busy === "review"}
                  onClick={() => run("review", async () => patchLocal(await api.updateQuestion(q.id, { reviewed: true })))}
                >
                  Mark reviewed
                </Button>
              )}
              <Button
                size="sm"
                variant="ghost"
                icon={q.excluded ? <Eye className="size-3.5" aria-hidden /> : <EyeOff className="size-3.5" aria-hidden />}
                loading={busy === "exclude"}
                onClick={() =>
                  run("exclude", async () => {
                    patchLocal(await api.updateQuestion(q.id, { excluded: !q.excluded }));
                    await reload();
                  })
                }
              >
                {q.excluded ? "Include" : "Exclude"}
              </Button>
              <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" aria-hidden />} onClick={onAddAfter}>
                Add after
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Move up"
                disabled={index === 0}
                onClick={() => onMove(-1)}
                icon={<ArrowUp className="size-3.5" aria-hidden />}
              />
              <Button
                size="sm"
                variant="ghost"
                aria-label="Move down"
                disabled={index === total - 1}
                onClick={() => onMove(1)}
                icon={<ArrowDown className="size-3.5" aria-hidden />}
              />
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
        </div>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title="Delete this question?"
        confirmLabel="Delete question"
        danger
        busy={busy === "delete"}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() =>
          run("delete", async () => {
            try {
              await api.deleteQuestion(q.id);
            } finally {
              setConfirmDelete(false);
            }
            await reload();
          })
        }
      >
        The question and any answer written for it are removed. To keep it out of the export but hold on to it, use
        Exclude instead.
      </ConfirmDialog>
    </li>
  );
}

function AddQuestionForm({ afterId, onDone }: { afterId: string | null; onDone: () => void }) {
  const { a, reload } = useWorkspace();
  const [number, setNumber] = useState("");
  const [text, setText] = useState("");
  const [marks, setMarks] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.addQuestion(a.id, {
        number,
        text,
        marks: marks.trim() ? Number(marks) : null,
        after_id: afterId,
      });
      await reload();
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet title={afterId ? "Add a question after the selected one" : "Add a question"}>
      <div className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-[120px_120px]">
          <TextField label="Number" value={number} maxLength={32} onChange={(e) => setNumber(e.target.value)} />
          <TextField label="Marks" type="number" min={0} step="0.5" value={marks} onChange={(e) => setMarks(e.target.value)} />
        </div>
        <TextArea label="Question text" rows={4} value={text} maxLength={20000} onChange={(e) => setText(e.target.value)} autoFocus />
        {error && <Notice tone="error">{error}</Notice>}
        <div className="flex gap-2">
          <Button variant="primary" loading={busy} disabled={!text.trim()} onClick={submit}>
            Add question
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </div>
    </Sheet>
  );
}

export function StepQuestions() {
  const { a, reload, replace, go, watch } = useWorkspace();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState<{ after: string | null } | null>(null);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmReextract, setConfirmReextract] = useState(false);

  const extracting = a.active_jobs.some((j) => j.type === "extract") || a.status === "extracting";
  const qs = a.questions;
  const included = qs.filter((q) => !q.excluded);
  const needsReview = included.filter((q) => q.needs_review).length;

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const move = (i: number, dir: -1 | 1) =>
    run("move", async () => {
      const ids = qs.map((q) => q.id);
      const j = i + dir;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      await api.reorderQuestions(a.id, ids);
      await reload();
    });

  const merge = () =>
    run("merge", async () => {
      await api.mergeQuestions(a.id, qs.filter((q) => selected.has(q.id)).map((q) => q.id));
      setSelected(new Set());
      await reload();
    });

  const reExtract = () =>
    run("extract", async () => {
      try {
        const j = await api.extract(a.id);
        watch(j.id);
        await reload();
      } finally {
        setConfirmReextract(false);
      }
    });

  const confirm = () =>
    run("confirm", async () => {
      replace(await api.confirmExtraction(a.id));
      go("preferences");
    });

  const showPage = (p: number) => {
    setHighlight(p);
    document.getElementById(`src-page-${p}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (extracting) {
    return (
      <p className="text-sm text-muted">
        The assignment is being read. Questions appear here as soon as that finishes; progress is shown above.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-3xl text-sm text-muted">
        Check every question against the original pages: the wording, numbering and marks. Fix mistakes, merge parts that
        were split, split questions that were run together, and exclude anything that should not be answered.
      </p>

      {a.status === "extraction_failed" && (
        <Notice tone="error" title="The questions could not be read automatically">
          {a.error ?? "Extraction failed."} Try reading the PDF again, or add the questions yourself.
        </Notice>
      )}
      {a.extraction_confirmed ? (
        <Notice tone="ok" title="Questions confirmed">
          Changing them is still possible. Adding a question asks you to confirm again.
        </Notice>
      ) : (
        needsReview > 0 && (
          <Notice tone="warn" title={`${needsReview} ${needsReview === 1 ? "question needs" : "questions need"} checking`}>
            They are marked below with the reason. Edit or mark each one reviewed.
          </Notice>
        )
      )}

      <InstructionsEditor />

      <div className="sticky top-14 z-20 -mx-1 flex flex-wrap items-center gap-2 rounded-[5px] border border-line bg-paper/95 px-3 py-2 backdrop-blur">
        <Button
          variant="primary"
          icon={<CheckCheck className="size-4" aria-hidden />}
          loading={busy === "confirm"}
          disabled={included.length === 0}
          onClick={confirm}
        >
          Confirm questions
        </Button>
        <Button icon={<Plus className="size-4" aria-hidden />} onClick={() => setAdding({ after: null })}>
          Add question
        </Button>
        <Button
          icon={<Combine className="size-4" aria-hidden />}
          disabled={selected.size < 2}
          loading={busy === "merge"}
          onClick={merge}
        >
          Merge selected{selected.size ? ` (${selected.size})` : ""}
        </Button>
        <Button variant="ghost" icon={<RotateCw className="size-4" aria-hidden />} onClick={() => setConfirmReextract(true)}>
          Read the PDF again
        </Button>
        <span className="ml-auto text-[13px] text-muted">
          {included.length} included{qs.length !== included.length ? `, ${qs.length - included.length} excluded` : ""}
        </span>
      </div>
      {error && <Notice tone="error">{error}</Notice>}

      {adding && adding.after === null && <AddQuestionForm afterId={null} onDone={() => setAdding(null)} />}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="order-2 xl:order-1">
          <div className="xl:sticky xl:top-32 xl:max-h-[calc(100vh-9rem)] xl:overflow-y-auto xl:pr-1">
            <PageViewer highlight={highlight} />
          </div>
        </div>
        <div className="order-1 xl:order-2">
          {qs.length === 0 ? (
            <EmptyState
              title="No questions found"
              action={
                <Button variant="primary" onClick={() => setAdding({ after: null })}>
                  Add a question
                </Button>
              }
            >
              Nothing that looks like a question was found. Add them yourself, copying the text from the pages.
            </EmptyState>
          ) : (
            <ol className="flex flex-col gap-3" aria-label="Questions">
              {qs.map((q, i) => (
                <Fragment key={q.id}>
                  <QuestionItem
                    q={q}
                    index={i}
                    total={qs.length}
                    selected={selected.has(q.id)}
                    onSelect={(v) =>
                      setSelected((prev) => {
                        const n = new Set(prev);
                        if (v) n.add(q.id);
                        else n.delete(q.id);
                        return n;
                      })
                    }
                    onMove={(d) => move(i, d)}
                    onShowPage={showPage}
                    onAddAfter={() => setAdding({ after: q.id })}
                  />
                  {adding?.after === q.id && (
                    <li className="list-none">
                      <AddQuestionForm afterId={q.id} onDone={() => setAdding(null)} />
                    </li>
                  )}
                </Fragment>
              ))}
            </ol>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmReextract}
        title="Read the PDF again?"
        confirmLabel="Read again"
        busy={busy === "extract"}
        onConfirm={reExtract}
        onClose={() => setConfirmReextract(false)}
      >
        Questions found in the PDF are replaced, including your edits to them and their answers. Questions you added
        yourself are kept. This is refused if any extracted question already has an approved answer.
      </ConfirmDialog>
    </div>
  );
}
