"use client";

import { urls } from "@/lib/api";
import type { Profile } from "@/lib/types";
import { cx, Notice, Sheet, StatusBadge } from "../ui";

const LOWER = "abcdefghijklmnopqrstuvwxyz".split("");
const UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const DIGITS = "0123456789".split("");

function fmt(v: number | undefined, digits = 2, unit = "") {
  return v === undefined || v === null ? "Not measured" : `${v.toFixed(digits)}${unit}`;
}

function CharGrid({ title, chars, coverage, missing }: { title: string; chars: string[]; coverage: Record<string, number>; missing: Set<string> }) {
  if (!chars.length) return null;
  const have = chars.filter((c) => (coverage[c] ?? 0) > 0).length;
  return (
    <div>
      <p className="mb-1.5 text-[13px] text-muted">
        <span className="font-medium text-graphite">{title}</span>, {have} of {chars.length} captured
      </p>
      <ul className="flex flex-wrap gap-1">
        {chars.map((c) => {
          const n = coverage[c] ?? 0;
          const miss = missing.has(c) || n === 0;
          return (
            <li
              key={c}
              title={miss ? `${c}: missing` : `${c}: ${n} ${n === 1 ? "variant" : "variants"}`}
              className={cx(
                "relative flex size-9 flex-col items-center justify-center rounded border font-serif text-lg leading-none",
                miss ? "border-dashed border-margin/60 bg-margin-soft text-margin" : "border-line bg-sheet text-ink",
              )}
            >
              <span aria-hidden>{c}</span>
              <span className="sr-only">{miss ? `${c} missing` : `${c}, ${n} variants`}</span>
              {!miss && (
                <span aria-hidden className="absolute bottom-0 right-0.5 font-sans text-[9px] tabular-nums text-faint">
                  {n}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ProfileResults({ profile }: { profile: Profile }) {
  const m = profile.metrics ?? {};
  const qr = profile.quality_report ?? {};
  const coverage = profile.coverage ?? {};
  const miss = qr.missing ?? {};
  const missing = new Set([...(miss.lowercase ?? []), ...(miss.uppercase ?? []), ...(miss.digits ?? []), ...(miss.punctuation ?? [])]);
  const alnum = new Set([...LOWER, ...UPPER, ...DIGITS]);
  const other = Array.from(new Set([...Object.keys(coverage), ...(miss.punctuation ?? [])].filter((c) => !alnum.has(c)))).sort();
  const hasGlyphs = (m.glyph_count ?? 0) > 0;
  const processed = profile.status === "ready" || profile.status === "needs_samples" || profile.status === "failed";

  if (!processed && !hasGlyphs) return null;

  return (
    <Sheet
      title="What was captured"
      description="Measured from your processed samples."
      actions={<StatusBadge status={profile.status} />}
    >
      {profile.status === "failed" && (
        <Notice tone="error" className="mb-5" title="Processing failed">
          {profile.error ?? "The samples could not be processed. Check the issues on each sample and try again."}
        </Notice>
      )}
      {profile.status === "needs_samples" && (
        <Notice tone="warn" className="mb-5" title="Usable, but more samples would help">
          Missing characters are written with a printed fallback font. Fill in another template page to cover them.
        </Notice>
      )}
      {profile.status === "ready" && (
        <Notice tone="ok" className="mb-5" title="Ready to write assignments">
          Choose this profile in an assignment&rsquo;s handwriting step.
        </Notice>
      )}

      {qr.advice && qr.advice.length > 0 && (
        <div className="mb-6">
          <h3 className="text-lg">Suggestions</h3>
          <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-sm marker:text-ink-soft">
            {qr.advice.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}

      {hasGlyphs && (
        <dl className="mb-6 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          {[
            ["Letter height (x-height)", fmt(m.x_height_mm, 2, " mm")],
            ["Slant", fmt(m.slant_deg, 1, "°")],
            ["Stroke thickness", fmt(m.stroke_mm, 2, " mm")],
            ["Letter spacing", fmt(m.letter_gap_ratio, 2, "×")],
            ["Word spacing", fmt(m.word_gap_ratio, 2, "×")],
            ["Line spacing", fmt(m.line_spacing_ratio, 2, "×")],
            ["Characters", String(m.char_count ?? 0)],
            ["Letter images", String(m.glyph_count ?? 0)],
          ].map(([k, v]) => (
            <div key={k} className="border-l-2 border-rule pl-3">
              <dt className="text-[12.5px] text-muted">{k}</dt>
              <dd className="font-medium tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="flex flex-col gap-4">
        <h3 className="text-lg">Character coverage</h3>
        <p className="-mt-2 text-[13px] text-muted">
          Small numbers show how many copies of each character were captured. Dashed red boxes are missing.
        </p>
        <CharGrid title="Lowercase" chars={LOWER} coverage={coverage} missing={missing} />
        <CharGrid title="Capitals" chars={UPPER} coverage={coverage} missing={missing} />
        <CharGrid title="Digits" chars={DIGITS} coverage={coverage} missing={missing} />
        <CharGrid title="Punctuation and symbols" chars={other} coverage={coverage} missing={missing} />
      </div>

      {hasGlyphs && (
        <div className="mt-6">
          <h3 className="text-lg">Captured letters</h3>
          <p className="mt-1 text-[13px] text-muted">Every letter image that will be used when writing.</p>
          <div className="mt-3 overflow-auto rounded border border-line bg-white p-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={urls.glyphSheet(profile.id, profile.library_version)}
              alt="Contact sheet of all captured letters"
              className="max-w-full"
            />
          </div>
        </div>
      )}
    </Sheet>
  );
}
