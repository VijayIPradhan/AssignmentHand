"use client";

import Link from "next/link";
import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Info, Loader2, RotateCw } from "lucide-react";
import type { Job, JobEvent } from "@/lib/types";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------------------------ buttons

type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet-danger";
type Size = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-2 rounded-[5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55 whitespace-nowrap";
const variants: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-ink-hover shadow-[inset_0_-1px_0_rgb(0_0_0/0.2)]",
  secondary: "bg-sheet text-ink border border-line-strong hover:border-ink-soft hover:bg-ink-wash",
  ghost: "text-ink hover:bg-ink-wash",
  danger: "bg-margin text-white hover:bg-[#962c25]",
  "quiet-danger": "text-margin hover:bg-margin-soft",
};
const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-10 px-4 text-sm",
};

export function buttonClass(variant: Variant = "secondary", size: Size = "md", extra?: string) {
  return cx(base, variants[variant], sizes[size], extra);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, icon, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClass(variant, size, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export function LinkButton({
  href,
  variant = "secondary",
  size = "md",
  icon,
  children,
  className,
  download,
  external,
}: {
  href: string;
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  download?: boolean;
  external?: boolean;
}) {
  if (download || external) {
    return (
      <a
        href={href}
        className={buttonClass(variant, size, className)}
        download={download || undefined}
        target={external ? "_blank" : undefined}
        rel={external ? "noopener" : undefined}
      >
        {icon}
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={buttonClass(variant, size, className)}>
      {icon}
      {children}
    </Link>
  );
}

// ------------------------------------------------------------------------------------ form fields

const control =
  "w-full rounded-[5px] border border-line-strong bg-sheet px-3 text-[15px] text-graphite placeholder:text-faint focus:border-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-ink/40 disabled:bg-paper-deep disabled:text-muted";

interface FieldShell {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  className?: string;
}

function Shell({
  id,
  label,
  hint,
  error,
  className,
  children,
}: FieldShell & { id: string; children: ReactNode }) {
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-medium text-graphite">
        {label}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-[13px] text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-hint`} className="text-[13px] text-margin">
          {error}
        </p>
      )}
    </div>
  );
}

export function TextField({
  label,
  hint,
  error,
  className,
  ...rest
}: FieldShell & Omit<InputHTMLAttributes<HTMLInputElement>, "className">) {
  const id = useId();
  return (
    <Shell id={rest.id ?? id} label={label} hint={hint} error={error} className={className}>
      <input
        id={rest.id ?? id}
        aria-describedby={hint || error ? `${rest.id ?? id}-hint` : undefined}
        aria-invalid={error ? true : undefined}
        className={cx(control, "h-10")}
        {...rest}
      />
    </Shell>
  );
}

export const TextArea = forwardRef<
  HTMLTextAreaElement,
  FieldShell & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className"> & { inputClassName?: string }
>(function TextArea({ label, hint, error, className, inputClassName, ...rest }, ref) {
  const id = useId();
  const fid = rest.id ?? id;
  return (
    <Shell id={fid} label={label} hint={hint} error={error} className={className}>
      <textarea
        ref={ref}
        id={fid}
        aria-describedby={hint || error ? `${fid}-hint` : undefined}
        aria-invalid={error ? true : undefined}
        className={cx(control, "py-2 leading-relaxed", inputClassName)}
        {...rest}
      />
    </Shell>
  );
});

export function SelectField({
  label,
  hint,
  error,
  className,
  children,
  ...rest
}: FieldShell & Omit<SelectHTMLAttributes<HTMLSelectElement>, "className">) {
  const id = useId();
  const fid = rest.id ?? id;
  return (
    <Shell id={fid} label={label} hint={hint} error={error} className={className}>
      <select
        id={fid}
        aria-describedby={hint || error ? `${fid}-hint` : undefined}
        className={cx(control, "h-10 pr-8")}
        {...rest}
      >
        {children}
      </select>
    </Shell>
  );
}

export function Checkbox({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: ReactNode;
  hint?: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-2.5">
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 size-4 accent-ink"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      <div>
        <label htmlFor={id} className="text-sm text-graphite">
          {label}
        </label>
        {hint && (
          <p id={`${id}-hint`} className="text-[13px] text-muted">
            {hint}
          </p>
        )}
      </div>
    </div>
  );
}

/** Labelled range slider with the current value shown. */
export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
  hint,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-[13px] font-medium text-graphite">
          {label}
        </label>
        <output htmlFor={id} className="text-[13px] tabular-nums text-muted">
          {format ? format(value) : value}
        </output>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-ink"
        aria-describedby={hint ? `${id}-hint` : undefined}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-[12px] text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------ status

export type Tone = "neutral" | "ink" | "ok" | "warn" | "error";

const toneClass: Record<Tone, string> = {
  neutral: "bg-paper-deep text-muted border-line",
  ink: "bg-ink-wash text-ink border-rule",
  ok: "bg-ok-soft text-ok border-[#c4dfcc]",
  warn: "bg-warn-soft text-warn border-[#ecd9a8]",
  error: "bg-margin-soft text-margin border-[#efc9c5]",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] font-medium leading-4 whitespace-nowrap",
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS: Record<string, { label: string; tone: Tone }> = {
  // assignments
  uploaded: { label: "Uploaded", tone: "neutral" },
  extracting: { label: "Reading questions", tone: "ink" },
  extraction_failed: { label: "Extraction failed", tone: "error" },
  awaiting_review: { label: "Review questions", tone: "warn" },
  generating_answers: { label: "Writing answers", tone: "ink" },
  awaiting_approval: { label: "Awaiting approval", tone: "warn" },
  rendering: { label: "Rendering", tone: "ink" },
  render_failed: { label: "Render failed", tone: "error" },
  completed: { label: "Completed", tone: "ok" },
  // profiles
  draft: { label: "Not processed", tone: "neutral" },
  processing: { label: "Processing", tone: "ink" },
  ready: { label: "Ready", tone: "ok" },
  needs_samples: { label: "Needs more samples", tone: "warn" },
  failed: { label: "Failed", tone: "error" },
  // samples
  processed: { label: "Processed", tone: "ok" },
  rejected: { label: "Rejected", tone: "error" },
  // answers
  pending: { label: "Not written", tone: "neutral" },
  generating: { label: "Generating", tone: "ink" },
  generated: { label: "Draft", tone: "ink" },
  edited: { label: "Edited", tone: "warn" },
  approved: { label: "Approved", tone: "ok" },
  // jobs
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "ink" },
  succeeded: { label: "Done", tone: "ok" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const s = STATUS[status] ?? { label: status.replace(/_/g, " "), tone: "neutral" as Tone };
  return (
    <Badge tone={s.tone} className={className}>
      {s.label}
    </Badge>
  );
}

const noticeIcon: Record<Tone, ReactNode> = {
  neutral: <Info className="size-4 shrink-0" aria-hidden />,
  ink: <Info className="size-4 shrink-0" aria-hidden />,
  ok: <CheckCircle2 className="size-4 shrink-0" aria-hidden />,
  warn: <AlertTriangle className="size-4 shrink-0" aria-hidden />,
  error: <AlertCircle className="size-4 shrink-0" aria-hidden />,
};

export function Notice({
  tone = "ink",
  title,
  children,
  action,
  className,
  role,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
  role?: "alert" | "status";
}) {
  return (
    <div
      role={role ?? (tone === "error" ? "alert" : undefined)}
      className={cx("flex gap-3 rounded-[5px] border px-3.5 py-3 text-sm", toneClass[tone], className)}
    >
      <span className="mt-0.5">{noticeIcon[tone]}</span>
      <div className="min-w-0 flex-1 text-graphite">
        {title && <p className="font-medium text-inherit">{title}</p>}
        {children && <div className={cx(title ? "mt-0.5" : undefined, "text-[13.5px] leading-relaxed")}>{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry, className }: { message: string; onRetry?: () => void; className?: string }) {
  return (
    <Notice
      tone="error"
      title="This could not be loaded"
      className={className}
      action={
        onRetry && (
          <Button size="sm" variant="secondary" icon={<RotateCw className="size-3.5" aria-hidden />} onClick={onRetry}>
            Try again
          </Button>
        )
      }
    >
      {message}
    </Notice>
  );
}

export function Spinner({ label = "Loading", className }: { label?: string; className?: string }) {
  return (
    <span role="status" className={cx("inline-flex items-center gap-2 text-sm text-muted", className)}>
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </span>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-40 items-center justify-center">
      <Spinner label={label} />
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("rounded-sheet border border-dashed border-line-strong bg-sheet/60 px-6 py-8 text-center", className)}>
      {icon && <div className="mx-auto mb-3 flex size-10 items-center justify-center text-ink-soft">{icon}</div>}
      <p className="font-serif text-lg text-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Real progress of a background job (0..1 from the API), announced politely to screen readers. */
export function JobProgress({ job, label, error }: { job: Job | null; label: string; error?: string | null }) {
  const pct = job ? Math.round(Math.max(0, Math.min(1, job.progress)) * 100) : 0;
  const status = job?.status ?? "queued";
  const failed = status === "failed" || status === "cancelled";
  return (
    <div className="rounded-[5px] border border-rule bg-ink-wash/60 px-3.5 py-3" aria-live="polite">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex items-center gap-2 font-medium text-ink">
          {!failed && status !== "succeeded" && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {label}
        </span>
        <span className="tabular-nums text-muted">{status === "queued" ? "Waiting to start" : `${pct}%`}</span>
      </div>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-rule-soft"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className={cx("h-full rounded-full transition-[width] duration-500", failed ? "bg-margin" : "bg-ink")}
          style={{ width: `${pct}%` }}
        />
      </div>
      {(job?.message || error) && (
        <p className={cx("mt-1.5 text-[13px]", error ? "text-margin" : "text-muted")}>{error ?? job?.message}</p>
      )}
      <JobSteps events={(job?.events ?? []) as JobEvent[]} />
    </div>
  );
}

/** Collapsible timeline of a job's steps, retries and errors. */
export function JobSteps({ events }: { events: JobEvent[] }) {
  if (events.length < 2) return null;
  return (
    <details className="mt-2 text-[12.5px]">
      <summary className="cursor-pointer text-muted hover:text-graphite">Show steps ({events.length})</summary>
      <ol className="mt-1.5 space-y-0.5 border-l border-rule pl-3">
        {events.map((e, i) => (
          <li
            key={`${e.at}-${i}`}
            className={cx(e.level === "error" ? "text-margin" : e.level === "warning" ? "text-warn" : "text-muted")}
          >
            <span className="tabular-nums">{new Date(e.at).toLocaleTimeString()}</span> {e.msg}
            {typeof e.prev_step_ms === "number" && i > 0 && <span> (previous step {(e.prev_step_ms / 1000).toFixed(1)}s)</span>}
          </li>
        ))}
      </ol>
    </details>
  );
}

// ------------------------------------------------------------------------------------ layout

export function PageHeader({
  title,
  description,
  actions,
  back,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {back && <div className="mb-3">{back}</div>}
        <h1 className="page-title text-[2rem] leading-tight sm:text-[2.35rem]">{title}</h1>
        {description && <div className="mt-2 max-w-2xl pl-[1.1rem] text-[15px] text-muted">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Sheet({
  title,
  description,
  actions,
  children,
  className,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cx("rounded-sheet border border-line bg-sheet", className)}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-xl">{title}</h2>}
            {description && <div className="mt-1 text-[13.5px] text-muted">{description}</div>}
          </div>
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------------------------ dialog

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger,
  busy,
  onConfirm,
  onClose,
  confirmDisabled,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={() => {
        // Only user-initiated closes (Esc); programmatic closes come from the parent already.
        if (open) onClose();
      }}
      onCancel={(e) => {
        if (busy) e.preventDefault();
      }}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-sheet border border-line bg-sheet p-0 text-graphite shadow-[0_20px_50px_-20px_rgb(27_42_107/0.35)]"
      aria-labelledby="confirm-title"
    >
      {open && (
        <div className="p-6">
          <h2 id="confirm-title" className="text-xl">
            {title}
          </h2>
          <div className="mt-3 text-sm leading-relaxed text-muted">{children}</div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant={danger ? "danger" : "primary"}
              onClick={onConfirm}
              loading={busy}
              disabled={confirmDisabled}
            >
              {confirmLabel}
            </Button>
          </div>
        </div>
      )}
    </dialog>
  );
}

/** Small "Saved"/"Saving" text indicator that fades in. */
export function SaveState({ state }: { state: "idle" | "saving" | "saved" | "error" | "dirty" }) {
  const text = { idle: "", dirty: "Unsaved changes", saving: "Saving", saved: "Saved", error: "Not saved" }[state];
  return (
    <span
      aria-live="polite"
      className={cx("text-[12.5px]", state === "error" ? "text-margin" : state === "saved" ? "text-ok" : "text-muted")}
    >
      {text}
    </span>
  );
}

export function useFlag(initial = false) {
  const [v, setV] = useState(initial);
  return [v, () => setV(true), () => setV(false)] as const;
}

export function formatDate(iso: string | null | undefined) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
