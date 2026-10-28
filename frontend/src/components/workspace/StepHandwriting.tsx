"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, errorMessage, urls } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { AssignmentFields, Profile, RenderSettings } from "@/lib/types";
import { StyleControls, type StyleValues } from "../StyleControls";
import {
  Button,
  Checkbox,
  cx,
  ErrorState,
  LoadingBlock,
  Notice,
  SaveState,
  SelectField,
  Sheet,
  Slider,
  StatusBadge,
  TextField,
} from "../ui";
import { useWorkspace } from "./context";

const PAPERS: { value: RenderSettings["paper"]; title: string; desc: string }[] = [
  { value: "source", title: "On the assignment pages", desc: "Written into the space under each question on the original PDF." },
  { value: "ruled", title: "Ruled paper", desc: "Fresh lined pages, like an exercise book." },
  { value: "blank", title: "Blank paper", desc: "Plain pages with no lines." },
  { value: "grid", title: "Grid paper", desc: "Squared pages, handy for maths." },
];

function ProfilePicker() {
  const { a, replace } = useWorkspace();
  const profiles = useResource<Profile[]>("profiles", api.profiles);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);

  async function choose(pid: string) {
    setChosen(pid);
    setBusy(true);
    setError(null);
    try {
      replace(await api.updateAssignment(a.id, { profile_id: pid || null }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      setChosen(null);
    }
  }

  if (profiles.loading && !profiles.data) return <LoadingBlock label="Loading profiles" />;
  if (profiles.error && !profiles.data) return <ErrorState message={profiles.error} onRetry={() => void profiles.reload()} />;
  const list = profiles.data ?? [];
  const usable = (p: Profile) => (p.metrics?.glyph_count ?? 0) > 0 && (p.status === "ready" || p.status === "needs_samples");
  const current = list.find((p) => p.id === a.profile_id);

  return (
    <Sheet title="Handwriting" description="Whose handwriting the answers are written in.">
      {list.length === 0 ? (
        <Notice tone="warn" title="You have no handwriting profiles yet">
          <Link href="/profiles" className="font-medium text-ink underline underline-offset-4">
            Create one
          </Link>{" "}
          from your samples, then come back to choose it.
        </Notice>
      ) : (
        <fieldset aria-busy={busy}>
          <legend className="sr-only">Handwriting profile</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {list.map((p) => (
              <label
                key={p.id}
                className={cx(
                  "flex cursor-pointer items-start gap-3 rounded-[5px] border px-3.5 py-3 text-sm",
                  (chosen ?? a.profile_id) === p.id ? "border-ink bg-ink-wash" : "border-line-strong bg-sheet hover:border-ink-soft",
                  !usable(p) && "opacity-70",
                )}
              >
                <input
                  type="radio"
                  name="profile"
                  className="mt-0.5 accent-ink"
                  checked={(chosen ?? a.profile_id) === p.id}
                  onChange={() => void choose(p.id)}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.name}</span>
                    <StatusBadge status={p.status} />
                  </span>
                  <span className="mt-0.5 block text-[12.5px] text-muted">
                    {usable(p)
                      ? `${p.metrics.char_count ?? 0} characters captured`
                      : "Not processed yet; process its samples before rendering."}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {current && !usable(current) && (
        <Notice tone="warn" className="mt-3">
          {current.name} cannot be used for rendering until its samples are processed.{" "}
          <Link href={`/profiles/${current.id}`} className="font-medium text-ink underline underline-offset-4">
            Open the profile
          </Link>
        </Notice>
      )}
      {error && (
        <Notice tone="error" className="mt-3">
          {error}
        </Notice>
      )}
    </Sheet>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  suffix?: string;
}) {
  return (
    <TextField
      label={suffix ? `${label} (${suffix})` : label}
      type="number"
      min={min}
      max={max}
      step={step}
      value={Number.isFinite(value) ? value : ""}
      onChange={(e) => {
        const v = Number(e.target.value);
        if (e.target.value !== "" && Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v)));
      }}
    />
  );
}

function SettingsForm({ initial, hasSource }: { initial: RenderSettings; hasSource: boolean }) {
  const { a, reload } = useWorkspace();
  const [s, setS] = useState<RenderSettings>(initial);
  const [state, setState] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof RenderSettings>(k: K, v: RenderSettings[K]) => {
    setS((prev) => ({ ...prev, [k]: v }));
    setState("dirty");
  };
  const setMargin = (k: keyof RenderSettings["margins"], v: number) => {
    setS((prev) => ({ ...prev, margins: { ...prev.margins, [k]: v } }));
    setState("dirty");
  };

  async function save() {
    setState("saving");
    setError(null);
    try {
      const saved = await api.saveRenderConfig(a.id, s);
      setS(saved);
      setState("saved");
      await reload();
    } catch (err) {
      setError(errorMessage(err));
      setState("error");
    }
  }

  const style: StyleValues = {
    handwriting_size_mm: s.handwriting_size_mm,
    stroke_weight: s.stroke_weight,
    slant_offset_deg: s.slant_offset_deg,
    char_spacing: s.char_spacing,
    word_spacing: s.word_spacing,
    variation: s.variation,
    ink_color: s.ink_color,
  };

  const source = s.paper === "source";

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <Sheet title="Paper">
        <fieldset>
          <legend className="sr-only">Paper</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {PAPERS.filter((p) => p.value !== "source" || hasSource).map((p) => (
              <label
                key={p.value}
                className={cx(
                  "flex cursor-pointer gap-3 rounded-[5px] border px-3.5 py-3 text-sm",
                  s.paper === p.value ? "border-ink bg-ink-wash" : "border-line-strong bg-sheet hover:border-ink-soft",
                )}
              >
                <input
                  type="radio"
                  name="paper"
                  className="mt-0.5 accent-ink"
                  checked={s.paper === p.value}
                  onChange={() => set("paper", p.value)}
                />
                <span>
                  <span className="block font-medium">{p.title}</span>
                  <span className="block text-[13px] text-muted">{p.desc}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {source ? (
          <div className="mt-5 flex flex-col gap-4">
            <p className="text-sm text-muted">
              Each answer is written in the blank space below its question on the original pages. If an answer is too
              long, the handwriting is shrunk to fit, down to the smallest size you allow; only if it still does not
              fit is an extra page added after that page. This is chosen for you when the PDF leaves room for answers.
            </p>
            <Checkbox
              label="Shrink handwriting to fit the space"
              checked={s.fit_to_space}
              onChange={(v) => set("fit_to_space", v)}
            />
            {s.fit_to_space && (
              <div className="max-w-sm">
                <Slider
                  label="Smallest allowed size"
                  min={0.4}
                  max={1}
                  step={0.05}
                  value={s.min_fit_scale}
                  onChange={(v) => set("min_fit_scale", v)}
                  format={(v) => `${Math.round(v * 100)}% of normal`}
                />
              </div>
            )}
            <SelectField
              label="Extra pages, when needed"
              value={s.extra_page_paper}
              onChange={(e) => set("extra_page_paper", e.target.value as RenderSettings["extra_page_paper"])}
              className="max-w-xs"
            >
              <option value="ruled">Ruled</option>
              <option value="blank">Blank</option>
              <option value="grid">Grid</option>
            </SelectField>
          </div>
        ) : (
          <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <SelectField label="Page size" value={s.page_size} onChange={(e) => set("page_size", e.target.value as RenderSettings["page_size"])}>
              <option value="A4">A4</option>
              <option value="Letter">US Letter</option>
            </SelectField>
            <SelectField
              label="Orientation"
              value={s.orientation}
              onChange={(e) => set("orientation", e.target.value as RenderSettings["orientation"])}
            >
              <option value="portrait">Portrait</option>
              <option value="landscape">Landscape</option>
            </SelectField>
            <NumberField label="Line spacing" suffix="mm" min={4} max={20} step={0.5} value={s.line_spacing_mm} onChange={(v) => set("line_spacing_mm", v)} />
            <div className="flex items-end gap-3">
              <label className="flex flex-col gap-1.5 text-[13px] font-medium">
                Paper colour
                <input type="color" value={s.paper_color} onChange={(e) => set("paper_color", e.target.value)} className="h-10 w-14 rounded border border-line-strong bg-sheet p-1" />
              </label>
              <label className="flex flex-col gap-1.5 text-[13px] font-medium">
                Line colour
                <input type="color" value={s.rule_color} onChange={(e) => set("rule_color", e.target.value)} className="h-10 w-14 rounded border border-line-strong bg-sheet p-1" />
              </label>
            </div>
            <div className="sm:col-span-2 lg:col-span-4">
              <Checkbox label="Red margin line" checked={s.margin_line} onChange={(v) => set("margin_line", v)} />
            </div>
            <fieldset className="sm:col-span-2 lg:col-span-4">
              <legend className="mb-2 text-[13px] font-medium">Margins (mm)</legend>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {(["top", "bottom", "left", "right"] as const).map((k) => (
                  <NumberField key={k} label={k[0].toUpperCase() + k.slice(1)} min={5} max={80} value={s.margins[k]} onChange={(v) => setMargin(k, v)} />
                ))}
              </div>
            </fieldset>
          </div>
        )}
      </Sheet>

      {source && (
        <FieldsSheet
          s={s}
          set={set}
          setValues={(v) => {
            setS((prev) => ({ ...prev, field_values: v }));
            setState("dirty");
          }}
        />
      )}

      <Sheet title="Handwriting style" description="Starts from the profile’s saved style; changes here apply to this assignment only.">
        <StyleControls
          value={style}
          onChange={(v) => {
            setS((prev) => ({ ...prev, ...v }));
            setState("dirty");
          }}
        />
        <div className="mt-5 max-w-sm">
          <NumberField
            label="Variation seed"
            min={0}
            max={2147483647}
            value={s.seed}
            onChange={(v) => set("seed", Math.round(v))}
          />
          <p className="mt-1 text-[12.5px] text-muted">Change it to get a different, equally natural arrangement of letter variants.</p>
        </div>
      </Sheet>

      <Sheet title="Layout">
        <div className="grid gap-4 sm:grid-cols-2">
          <Checkbox label="Write out the question text before each answer" checked={s.include_question_text} onChange={(v) => set("include_question_text", v)} />
          <Checkbox label="Underline question numbers" checked={s.underline_question_numbers} onChange={(v) => set("underline_question_numbers", v)} />
          <Checkbox label="Show marks" checked={s.show_marks} onChange={(v) => set("show_marks", v)} />
          <Checkbox label="Page numbers" checked={s.page_numbers} onChange={(v) => set("page_numbers", v)} />
          <NumberField label="Paragraph indent" suffix="mm" min={0} max={40} step={0.5} value={s.paragraph_indent_mm} onChange={(v) => set("paragraph_indent_mm", v)} />
          <NumberField label="Blank lines between questions" min={0} max={6} value={s.question_spacing_lines} onChange={(v) => set("question_spacing_lines", Math.round(v))} />
          <TextField label="Answer prefix" value={s.answer_prefix} maxLength={20} placeholder="e.g. Ans:" onChange={(e) => set("answer_prefix", e.target.value)} />
          <span />
          <TextField label="Header text" value={s.header_text} maxLength={200} placeholder="e.g. your name and student number" onChange={(e) => set("header_text", e.target.value)} />
          <TextField label="Footer text" value={s.footer_text} maxLength={200} onChange={(e) => set("footer_text", e.target.value)} />
        </div>
      </Sheet>

      {error && <Notice tone="error">{error}</Notice>}
      <div className="sticky bottom-3 z-10 flex max-w-full flex-wrap items-center gap-3 self-start rounded-[5px] border border-line bg-paper/95 px-3 py-2 backdrop-blur">
        <Button type="submit" variant="primary" loading={state === "saving"} disabled={state === "idle" || state === "saved"}>
          Save settings
        </Button>
        <Button
          variant="ghost"
          disabled={state === "idle" || state === "saved"}
          onClick={() => {
            setS(initial);
            setState("idle");
          }}
        >
          Discard changes
        </Button>
        <SaveState state={state} />
      </div>
    </form>
  );
}

export function StepHandwriting() {
  const { a, go } = useWorkspace();
  const cfg = useResource<RenderSettings>(`render-config:${a.id}:${a.profile_id ?? ""}`, () => api.renderConfig(a.id));
  const hasSource = a.documents.some((d) => d.role === "assignment" && d.pages.length > 0);

  return (
    <div className="flex flex-col gap-6">
      <ProfilePicker />
      {cfg.loading && !cfg.data ? (
        <LoadingBlock label="Loading settings" />
      ) : cfg.error && !cfg.data ? (
        <ErrorState message={cfg.error} onRetry={() => void cfg.reload()} />
      ) : (
        cfg.data && <SettingsForm key={JSON.stringify(cfg.data)} initial={cfg.data} hasSource={hasSource} />
      )}
      {a.profile_id && (
        <div>
          <Button variant="primary" onClick={() => go("export")}>
            Continue to preview
          </Button>
        </div>
      )}
    </div>
  );
}

/** Fill-in fields printed on the original pages (Registration Number, signature lines, ...). */
function FieldsSheet({
  s,
  setValues,
  set,
}: {
  s: RenderSettings;
  setValues: (v: Record<string, string>) => void;
  set: <K extends keyof RenderSettings>(k: K, v: RenderSettings[K]) => void;
}) {
  const { a } = useWorkspace();
  const info = useResource<AssignmentFields>(`fields:${a.id}`, () => api.assignmentFields(a.id));
  const values = s.field_values ?? {};
  const data = info.data;
  const suggested = data && Object.keys(values).length === 0 ? data.suggestions : null;

  useEffect(() => {
    if (suggested && Object.keys(suggested).length) setValues(suggested);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- apply suggestions once, when they arrive
  }, [data]);

  if (info.loading && !data) return <LoadingBlock label="Looking for fields on the pages" />;
  if (info.error && !data) return <ErrorState message={info.error} onRetry={() => void info.reload()} />;
  if (!data || data.fields.length === 0) return null;
  const textFields = data.fields.filter((f) => f.kind === "text");
  const hasSignatureLine = data.fields.some((f) => f.kind === "signature");

  return (
    <Sheet title="Fields on the pages" description="Printed blanks such as “Registration Number: ____”, filled in your handwriting.">
      <div className="flex flex-col gap-5">
        {textFields.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            {textFields.map((f) => (
              <TextField
                key={f.key}
                label={f.label}
                value={values[f.key] ?? ""}
                maxLength={80}
                placeholder="Leave empty to skip"
                hint={`On page ${f.pages.join(", ")}${data.suggestions[f.key] ? " · suggested from the pages" : ""}`}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
              />
            ))}
          </div>
        )}
        {hasSignatureLine && (
          <div className="flex flex-col gap-3">
            {data.signature_available ? (
              <>
                <Checkbox label="Sign the pages with my signature" checked={!!s.sign_fields} onChange={(v) => set("sign_fields", v)} />
                {s.sign_fields && (
                  <div className="flex flex-wrap items-end gap-5">
                    <div className="rounded-[5px] border border-line bg-white px-3 py-2">
                      {/* eslint-disable-next-line @next/next/no-img-element -- private, authenticated image */}
                      <img src={urls.signaturePreview(s.signature_color)} alt="Your signature preview" className="h-12 w-auto" />
                    </div>
                    <label className="flex flex-col gap-1.5 text-[13px] font-medium">
                      Signature ink
                      <input
                        type="color"
                        value={s.signature_color}
                        onChange={(e) => set("signature_color", e.target.value)}
                        className="h-10 w-14 rounded border border-line-strong bg-sheet p-1"
                      />
                    </label>
                    <div className="w-56">
                      <Slider
                        label="Signature height"
                        min={6}
                        max={20}
                        step={0.5}
                        value={s.signature_height_mm}
                        onChange={(v) => set("signature_height_mm", v)}
                        format={(v) => `${v} mm`}
                      />
                    </div>
                  </div>
                )}
              </>
            ) : (
              <Notice tone="neutral">
                The pages have a signature line. Add your signature on the <a className="underline" href="/account">Account</a>{" "}
                page to sign them here.
              </Notice>
            )}
          </div>
        )}
      </div>
    </Sheet>
  );
}
