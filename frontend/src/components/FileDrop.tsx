"use client";

import { useId, useState, type ReactNode } from "react";
import { Upload } from "lucide-react";
import { cx, formatBytes } from "./ui";

/** A keyboard-accessible file picker that also accepts drag and drop. */
export function FileDrop({
  accept,
  label,
  hint,
  file,
  onFile,
  disabled,
  maxBytes,
  compact,
}: {
  accept: string;
  label: ReactNode;
  hint?: ReactNode;
  file: File | null;
  onFile: (f: File | null, error?: string) => void;
  disabled?: boolean;
  maxBytes?: number;
  compact?: boolean;
}) {
  const id = useId();
  const [over, setOver] = useState(false);

  function take(f: File | undefined | null) {
    if (!f) return;
    if (maxBytes && f.size > maxBytes) {
      onFile(null, `That file is ${formatBytes(f.size)}; the limit is ${formatBytes(maxBytes)}.`);
      return;
    }
    onFile(f);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) take(e.dataTransfer.files?.[0]);
      }}
      className={cx(
        "relative rounded-sheet border border-dashed bg-sheet transition-colors focus-within:border-ink focus-within:ring-2 focus-within:ring-ink/20",
        over ? "border-ink bg-ink-wash" : "border-line-strong",
        disabled && "opacity-60",
      )}
    >
      <label
        htmlFor={id}
        className={cx("flex cursor-pointer flex-col items-center gap-1.5 text-center", compact ? "px-4 py-4" : "px-6 py-8")}
      >
        <Upload className="size-5 text-ink-soft" aria-hidden />
        <span className="text-sm font-medium text-ink">{file ? file.name : label}</span>
        <span className="text-[12.5px] text-muted">
          {file ? `${formatBytes(file.size)}. Choose another file to replace it.` : (hint ?? "Drop a file here or click to choose.")}
        </span>
      </label>
      <input
        id={id}
        type="file"
        accept={accept}
        disabled={disabled}
        className="absolute inset-0 size-full cursor-pointer opacity-0"
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}
