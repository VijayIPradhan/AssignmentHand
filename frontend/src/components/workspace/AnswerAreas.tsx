"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { AnswerSpace, DocPage, Question } from "@/lib/types";
import { cx } from "../ui";
import { useWorkspace } from "./context";

/** An answer area in PDF points from the page's top-left corner. */
interface Area {
  top: number;
  bottom: number;
  x0: number;
  x1: number;
}

type Handle = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

const MIN_W = 36; // points: a few words
const MIN_H = 14; // points: about one line
const HANDLES: { h: Handle; className: string }[] = [
  { h: "nw", className: "-left-1.5 -top-1.5 cursor-nwse-resize" },
  { h: "n", className: "left-1/2 -top-1.5 -translate-x-1/2 cursor-ns-resize" },
  { h: "ne", className: "-right-1.5 -top-1.5 cursor-nesw-resize" },
  { h: "e", className: "-right-1.5 top-1/2 -translate-y-1/2 cursor-ew-resize" },
  { h: "se", className: "-bottom-1.5 -right-1.5 cursor-nwse-resize" },
  { h: "s", className: "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" },
  { h: "sw", className: "-bottom-1.5 -left-1.5 cursor-nesw-resize" },
  { h: "w", className: "-left-1.5 top-1/2 -translate-y-1/2 cursor-ew-resize" },
];

/** The answer area currently used for a question (hand-placed or detected), if any. */
export function answerSpaceOf(q: Question): AnswerSpace | null {
  for (const p of q.pages) if (p.answer_space) return p.answer_space;
  return null;
}

/** A starting area for a question that has none: below its text on its last page, inside the margins. */
export function defaultArea(q: Question, pages: DocPage[]): { page: number } & Area {
  const last = q.pages[q.pages.length - 1];
  const page = last?.page ?? 0;
  const pg = pages[page] ?? { width: 595, height: 842 };
  const top = Math.min(pg.height * 0.8, (last?.y1 ?? pg.height * 0.2) + 6);
  return { page, top, bottom: Math.max(top + 60, pg.height * 0.88), x0: pg.width * 0.1, x1: pg.width * 0.9 };
}

function apply(a: Area, h: Handle, dx: number, dy: number, pg: DocPage): Area {
  let { top, bottom, x0, x1 } = a;
  if (h === "move") {
    const w = x1 - x0;
    const hh = bottom - top;
    x0 = Math.min(Math.max(0, x0 + dx), pg.width - w);
    top = Math.min(Math.max(0, top + dy), pg.height - hh);
    return { top, bottom: top + hh, x0, x1: x0 + w };
  }
  if (h.includes("n")) top = Math.min(Math.max(0, top + dy), bottom - MIN_H);
  if (h.includes("s")) bottom = Math.max(Math.min(pg.height, bottom + dy), top + MIN_H);
  if (h.includes("w")) x0 = Math.min(Math.max(0, x0 + dx), x1 - MIN_W);
  if (h.includes("e")) x1 = Math.max(Math.min(pg.width, x1 + dx), x0 + MIN_W);
  return { top, bottom, x0, x1 };
}

/** Answer areas of the questions on one page, drawn over the page image; drag to move, handles to resize. */
export function AnswerAreaOverlay({
  page,
  index,
  questions,
  selectedId,
  onSelect,
}: {
  page: DocPage;
  index: number;
  questions: Question[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (!page.width || !page.height) return null;
  const here = questions
    .map((q) => ({ q, sp: answerSpaceOf(q) }))
    .filter((x): x is { q: Question; sp: AnswerSpace } => !!x.sp && x.sp.page === index && !x.q.excluded);
  return (
    <div className="pointer-events-none absolute inset-0">
      {here.map(({ q, sp }) => (
        <AreaBox key={q.id} q={q} sp={sp} page={page} index={index} selected={selectedId === q.id} onSelect={onSelect} />
      ))}
    </div>
  );
}

function AreaBox({
  q,
  sp,
  page,
  index,
  selected,
  onSelect,
}: {
  q: Question;
  sp: AnswerSpace;
  page: DocPage;
  index: number;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const { reload } = useWorkspace();
  // A detected area has no right edge: the handwriting runs to the 15 mm right margin (42.5 pt).
  const saved: Area = { top: sp.top, bottom: sp.bottom, x0: sp.x0, x1: sp.x1 ?? page.width - 42.5 };
  const [draft, setDraft] = useState<Area | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drag = useRef<{ h: Handle; x: number; y: number; start: Area; scale: number } | null>(null);
  const keyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const area = draft ?? saved;
  const label = q.number ? `Q${q.number}` : `Question ${q.position + 1}`;

  useEffect(() => () => {
    if (keyTimer.current) clearTimeout(keyTimer.current);
  }, []);

  async function save(a: Area) {
    setSaving(true);
    setError(null);
    try {
      await api.setAnswerSpace(q.id, { page: index, ...a });
      await reload();
      setDraft(null);
    } catch (err) {
      setError(errorMessage(err));
      setDraft(null);
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setSaving(true);
    setError(null);
    try {
      await api.resetAnswerSpace(q.id);
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  function down(e: PointerEvent<HTMLElement>, h: Handle) {
    e.preventDefault();
    e.stopPropagation();
    onSelect(q.id);
    const box = (e.currentTarget.closest("[data-page-surface]") as HTMLElement | null)?.getBoundingClientRect();
    if (!box) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { h, x: e.clientX, y: e.clientY, start: area, scale: page.width / box.width };
  }

  function moveTo(e: PointerEvent<HTMLElement>) {
    const d = drag.current;
    if (!d) return;
    setDraft(apply(d.start, d.h, (e.clientX - d.x) * d.scale, (e.clientY - d.y) * d.scale, page));
  }

  function up() {
    const d = drag.current;
    drag.current = null;
    if (d && draft) void save(draft);
  }

  function key(e: KeyboardEvent<HTMLDivElement>) {
    const step = e.altKey ? 1 : 6;
    const dirs: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const d = dirs[e.key];
    if (!d) return;
    e.preventDefault();
    const next = apply(area, e.shiftKey ? "se" : "move", d[0], d[1], page);
    setDraft(next);
    if (keyTimer.current) clearTimeout(keyTimer.current);
    keyTimer.current = setTimeout(() => void save(next), 700);
  }

  const pct = (v: number, of: number) => `${(v / of) * 100}%`;
  return (
    <div
      role="group"
      tabIndex={0}
      aria-label={`${label} answer area on page ${index + 1}. Arrow keys move it, Shift with arrows resizes it.`}
      onKeyDown={key}
      onFocus={() => onSelect(q.id)}
      className={cx(
        "pointer-events-auto absolute touch-none rounded-[3px] border-2 outline-none",
        sp.manual ? "border-solid" : "border-dashed",
        selected ? "border-ink bg-ink/10 ring-2 ring-ink/25" : "border-ink/60 bg-ink/[0.04] hover:bg-ink/[0.07]",
      )}
      style={{
        left: pct(area.x0, page.width),
        top: pct(area.top, page.height),
        width: pct(area.x1 - area.x0, page.width),
        height: pct(area.bottom - area.top, page.height),
      }}
    >
      <div
        className="absolute inset-0 cursor-move"
        onPointerDown={(e) => down(e, "move")}
        onPointerMove={moveTo}
        onPointerUp={up}
        onPointerCancel={up}
      />
      <div className="pointer-events-auto absolute left-1 top-1 flex items-center gap-1 rounded bg-ink px-1.5 py-0.5 text-[11.5px] font-medium text-white shadow-sm">
        {saving && <Loader2 className="size-3 animate-spin" aria-hidden />}
        {label} answer{sp.manual ? " · placed by you" : ""}
        {sp.manual && !saving && (
          <button
            type="button"
            onClick={() => void reset()}
            className="ml-0.5 rounded p-0.5 hover:bg-white/20"
            title="Back to the area found automatically"
            aria-label={`Reset the ${label} answer area to the one found automatically`}
          >
            <RotateCcw className="size-3" aria-hidden />
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="absolute bottom-1 left-1 right-1 rounded bg-white/95 px-1.5 py-0.5 text-[11.5px] text-margin">
          {error}
        </p>
      )}
      {selected &&
        HANDLES.map(({ h, className }) => (
          <span
            key={h}
            aria-hidden
            className={cx("absolute size-3 rounded-sm border-2 border-ink bg-white", className)}
            onPointerDown={(e) => down(e, h)}
            onPointerMove={moveTo}
            onPointerUp={up}
            onPointerCancel={up}
          />
        ))}
    </div>
  );
}
