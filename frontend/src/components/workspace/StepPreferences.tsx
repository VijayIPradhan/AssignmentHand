"use client";

import { useState } from "react";
import { FileText, Trash2 } from "lucide-react";
import { api, errorMessage, urls } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { AnswerPreferences, Estimate } from "@/lib/types";
import { FileDrop } from "../FileDrop";
import { Button, Checkbox, ErrorState, formatDate, Notice, SaveState, SelectField, Sheet, Spinner, TextArea, TextField } from "../ui";
import { useWorkspace } from "./context";

const DEFAULTS: Required<Omit<AnswerPreferences, "target_words">> & { target_words: number | null } = {
  academic_level: "undergraduate",
  detail: "moderate",
  language: "English",
  target_words: null,
  technical_depth: "standard",
  citations: "none",
  show_working: true,
  tone: "formal",
  extra_instructions: "",
};

function EstimateBox() {
  const { a } = useWorkspace();
  const key = `${a.id}:${a.questions.filter((q) => !q.excluded).length}:${a.updated_at}`;
  const est = useResource<Estimate>(`estimate:${key}`, () => api.estimate(a.id));
  if (est.loading && !est.data) return <Spinner label="Estimating" />;
  if (est.error && !est.data) return <ErrorState message={est.error} onRetry={() => void est.reload()} />;
  const e = est.data!;
  const usd = (v: number | null) => (v === null ? "" : v < 0.01 ? "under $0.01" : `$${v.toFixed(2)}`);
  return (
    <div className="text-sm">
      <p>
        {e.questions} {e.questions === 1 ? "question" : "questions"}, about {e.input_tokens.toLocaleString()} input tokens
        and {e.output_tokens_min.toLocaleString()} to {e.output_tokens_max.toLocaleString()} output tokens.
      </p>
      {!e.ai.configured ? (
        <p className="mt-1 text-muted">No AI provider is set up, so generating costs nothing here; you write the answers.</p>
      ) : e.pricing_known && e.cost_usd_min !== null ? (
        <p className="mt-1">
          Estimated cost with <span className="font-medium">{e.model}</span>:{" "}
          <span className="font-medium">
            {usd(e.cost_usd_min)} to {usd(e.cost_usd_max)}
          </span>
          . Reasoning tokens vary, hence the range.
        </p>
      ) : (
        <p className="mt-1 text-muted">
          Pricing is unknown for the model {e.model ? <span className="font-medium text-graphite">{e.model}</span> : ""} with
          this provider, so no cost estimate can be given.
        </p>
      )}
    </div>
  );
}

function References() {
  const { a, reload } = useWorkspace();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refs = a.documents.filter((d) => d.role === "reference");

  async function upload() {
    if (!file) return;
    setBusy("upload");
    setError(null);
    try {
      await api.addReference(a.id, file);
      setFile(null);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove(did: string) {
    setBusy(did);
    setError(null);
    try {
      await api.deleteDocument(a.id, did);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Sheet
      title="Reference material"
      description="Lecture notes, readings or a textbook chapter (PDF). Their text is used to ground answers and citations."
    >
      {refs.length > 0 && (
        <ul className="mb-4 divide-y divide-line">
          {refs.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-3 py-2.5">
              <a
                href={urls.docPdf(a.id, d.id)}
                target="_blank"
                rel="noopener"
                className="flex min-w-0 items-center gap-2 text-sm hover:text-ink"
              >
                <FileText className="size-4 shrink-0 text-ink-soft" aria-hidden />
                <span className="truncate font-medium">{d.original_filename}</span>
                <span className="shrink-0 text-[12.5px] text-muted">
                  {d.page_count} pages, {formatDate(d.created_at)}
                </span>
              </a>
              <Button
                size="sm"
                variant="quiet-danger"
                icon={<Trash2 className="size-3.5" aria-hidden />}
                loading={busy === d.id}
                onClick={() => remove(d.id)}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-col gap-3">
        <FileDrop accept="application/pdf,.pdf" label="Choose a reference PDF" file={file} onFile={(f, err) => { setFile(f); setError(err ?? null); }} compact />
        {error && <Notice tone="error">{error}</Notice>}
        <div>
          <Button onClick={upload} loading={busy === "upload"} disabled={!file}>
            Add reference
          </Button>
        </div>
      </div>
    </Sheet>
  );
}

export function StepPreferences() {
  const { a, replace, go } = useWorkspace();
  const [title, setTitle] = useState(a.title);
  const [subject, setSubject] = useState(a.subject ?? "");
  const [prefs, setPrefs] = useState({ ...DEFAULTS, ...a.preferences });
  const [state, setState] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof typeof prefs>(k: K, v: (typeof prefs)[K]) => {
    setPrefs((p) => ({ ...p, [k]: v }));
    setState("dirty");
  };

  async function save() {
    setState("saving");
    setError(null);
    try {
      const next = await api.updateAssignment(a.id, {
        title: title.trim() || a.title,
        subject,
        preferences: {
          ...prefs,
          target_words: prefs.target_words ? Number(prefs.target_words) : null,
        },
      });
      replace(next);
      setState("saved");
    } catch (err) {
      setError(errorMessage(err));
      setState("error");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-3xl text-sm text-muted">
        How answers should be written. These guide AI drafts; if you write the answers yourself they are only a
        reference.
      </p>
      <Sheet title="Answer style">
        <form
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField label="Title" value={title} maxLength={255} onChange={(e) => { setTitle(e.target.value); setState("dirty"); }} />
            <TextField label="Subject" value={subject} maxLength={120} placeholder="e.g. Organic chemistry" onChange={(e) => { setSubject(e.target.value); setState("dirty"); }} />
            <TextField
              label="Academic level"
              value={prefs.academic_level}
              maxLength={80}
              onChange={(e) => set("academic_level", e.target.value)}
              hint="e.g. high school, first-year undergraduate, master's"
            />
            <TextField label="Language" value={prefs.language} maxLength={40} onChange={(e) => set("language", e.target.value)} />
            <SelectField label="Level of detail" value={prefs.detail} onChange={(e) => set("detail", e.target.value as typeof prefs.detail)}>
              <option value="concise">Concise</option>
              <option value="moderate">Moderate</option>
              <option value="detailed">Detailed</option>
            </SelectField>
            <SelectField
              label="Technical depth"
              value={prefs.technical_depth}
              onChange={(e) => set("technical_depth", e.target.value as typeof prefs.technical_depth)}
            >
              <option value="introductory">Introductory</option>
              <option value="standard">Standard</option>
              <option value="advanced">Advanced</option>
            </SelectField>
            <SelectField label="Tone" value={prefs.tone} onChange={(e) => set("tone", e.target.value as typeof prefs.tone)}>
              <option value="formal">Formal</option>
              <option value="neutral">Neutral</option>
              <option value="conversational">Conversational</option>
            </SelectField>
            <SelectField
              label="Citations"
              value={prefs.citations}
              onChange={(e) => set("citations", e.target.value as typeof prefs.citations)}
            >
              <option value="none">None</option>
              <option value="if_available">When reference material supports it</option>
              <option value="required">Required</option>
            </SelectField>
            <TextField
              label="Target words per answer"
              type="number"
              min={20}
              max={3000}
              value={prefs.target_words ?? ""}
              onChange={(e) => set("target_words", e.target.value ? Number(e.target.value) : null)}
              hint="Leave empty to scale with each question's marks."
            />
          </div>
          <Checkbox
            label="Show working for calculations"
            checked={prefs.show_working}
            onChange={(v) => set("show_working", v)}
          />
          <TextArea
            label="Anything else"
            rows={3}
            maxLength={1000}
            value={prefs.extra_instructions}
            onChange={(e) => set("extra_instructions", e.target.value)}
            hint="For example: use British spelling, refer to the case study, avoid bullet points."
          />
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" loading={state === "saving"} disabled={state === "idle" || state === "saved"}>
              Save preferences
            </Button>
            <SaveState state={state} />
            {state === "saved" && (
              <Button variant="ghost" onClick={() => go("generate")}>
                Continue to Generate
              </Button>
            )}
          </div>
        </form>
      </Sheet>
      <References />
      <Sheet title="Cost estimate" description="For generating all included answers with AI.">
        <EstimateBox />
      </Sheet>
    </div>
  );
}
