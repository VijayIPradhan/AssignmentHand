"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { RotateCcw, RotateCw } from "lucide-react";
import { Button, Slider } from "../ui";

export type Box = [number, number, number, number];
export const FULL: Box = [0, 0, 1, 1];

const MIN = 0.06;
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

/** Split a clockwise rotation into a quarter-turn count and a fine offset in -45..45. */
export function splitRotation(deg: number): { quarter: number; fine: number } {
  const n = ((((deg + 180) % 360) + 360) % 360) - 180;
  const quarter = Math.round(n / 90);
  return { quarter: ((quarter % 4) + 4) % 4, fine: Math.round((n - quarter * 90) * 10) / 10 };
}

export function joinRotation(quarter: number, fine: number) {
  return (((quarter * 90 + fine) % 360) + 360) % 360;
}

type Drag = { mode: "move" | "nw" | "ne" | "sw" | "se"; x: number; y: number; start: Box };

/**
 * Crop (drawn on the original, unrotated image, as the backend applies crop before rotation) and
 * rotation controls, with a live canvas preview of the result.
 */
export function CorrectionEditor({
  src,
  crop,
  rotate,
  onChange,
}: {
  src: string;
  crop: Box;
  rotate: number;
  onChange: (crop: Box, rotate: number) => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const img = useRef<HTMLImageElement | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [drag, setDrag] = useState<Drag | null>(null);
  const { quarter, fine } = splitRotation(rotate);

  const setBox = useCallback((b: Box) => onChange(b, rotate), [onChange, rotate]);

  // Live preview: crop, then rotate clockwise (same order as the server).
  useEffect(() => {
    const el = img.current;
    const c = canvas.current;
    if (!loaded || !el || !c) return;
    const [x0, y0, x1, y1] = crop;
    const sw = (x1 - x0) * el.naturalWidth;
    const sh = (y1 - y0) * el.naturalHeight;
    const rad = (rotate * Math.PI) / 180;
    const cos = Math.abs(Math.cos(rad));
    const sin = Math.abs(Math.sin(rad));
    const bw = sw * cos + sh * sin;
    const bh = sw * sin + sh * cos;
    const scale = Math.min(1, 360 / Math.max(bw, bh));
    c.width = Math.max(1, Math.round(bw * scale));
    c.height = Math.max(1, Math.round(bh * scale));
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.save();
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate(rad);
    ctx.scale(scale, scale);
    ctx.drawImage(el, x0 * el.naturalWidth, y0 * el.naturalHeight, sw, sh, -sw / 2, -sh / 2, sw, sh);
    ctx.restore();
  }, [crop, rotate, loaded]);

  function point(e: PointerEvent) {
    const r = frame.current!.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
  }

  function onDown(e: PointerEvent<HTMLElement>) {
    const mode = (e.currentTarget.dataset.mode ?? "move") as Drag["mode"];
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = point(e);
    setDrag({ mode, x: p.x, y: p.y, start: crop });
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    const p = point(e);
    const dx = p.x - drag.x;
    const dy = p.y - drag.y;
    let [x0, y0, x1, y1] = drag.start;
    if (drag.mode === "move") {
      const w = x1 - x0;
      const h = y1 - y0;
      x0 = clamp(x0 + dx, 0, 1 - w);
      y0 = clamp(y0 + dy, 0, 1 - h);
      x1 = x0 + w;
      y1 = y0 + h;
    } else {
      if (drag.mode.includes("w")) x0 = clamp(x0 + dx, 0, x1 - MIN);
      if (drag.mode.includes("e")) x1 = clamp(x1 + dx, x0 + MIN, 1);
      if (drag.mode.includes("n")) y0 = clamp(y0 + dy, 0, y1 - MIN);
      if (drag.mode.includes("s")) y1 = clamp(y1 + dy, y0 + MIN, 1);
    }
    setBox([x0, y0, x1, y1]);
  }

  function keys(e: KeyboardEvent) {
    const step = e.shiftKey ? 0.05 : 0.01;
    const d: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const v = d[e.key];
    if (!v) return;
    e.preventDefault();
    const [x0, y0, x1, y1] = crop;
    const w = x1 - x0;
    const h = y1 - y0;
    const nx = clamp(x0 + v[0], 0, 1 - w);
    const ny = clamp(y0 + v[1], 0, 1 - h);
    setBox([nx, ny, nx + w, ny + h]);
  }

  function setEdge(i: number, pct: number) {
    const b = [...crop] as Box;
    b[i] = clamp(pct / 100);
    if (b[2] - b[0] < MIN || b[3] - b[1] < MIN) return;
    setBox(b);
  }

  const [x0, y0, x1, y1] = crop;
  const handle = "absolute size-3.5 rounded-full border-2 border-white bg-ink shadow touch-none";

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div>
        <p className="mb-2 text-[13px] text-muted">
          Drag the frame or its corners to crop. With the frame focused, arrow keys move it (Shift for larger steps).
        </p>
        <div
          ref={frame}
          className="relative mx-auto w-fit max-w-full touch-none select-none overflow-hidden rounded border border-line bg-paper-deep"
          onPointerMove={move}
          onPointerUp={() => setDrag(null)}
          onPointerCancel={() => setDrag(null)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={img}
            src={src}
            alt="Original sample"
            className="block max-h-[480px] max-w-full"
            draggable={false}
            onLoad={() => setLoaded(true)}
          />
          <div
            role="group"
            tabIndex={0}
            aria-label="Crop frame"
            onKeyDown={keys}
            data-mode="move"
            onPointerDown={onDown}
            className="absolute cursor-move border-2 border-ink shadow-[0_0_0_9999px_rgb(38_42_51/0.4)] outline-offset-2"
            style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
          >
            <span className={`${handle} left-0.5 top-0.5 cursor-nwse-resize`} data-mode="nw" onPointerDown={onDown} />
            <span className={`${handle} right-0.5 top-0.5 cursor-nesw-resize`} data-mode="ne" onPointerDown={onDown} />
            <span className={`${handle} bottom-0.5 left-0.5 cursor-nesw-resize`} data-mode="sw" onPointerDown={onDown} />
            <span className={`${handle} bottom-0.5 right-0.5 cursor-nwse-resize`} data-mode="se" onPointerDown={onDown} />
          </div>
        </div>
        <fieldset className="mt-3">
          <legend className="text-[13px] font-medium">Crop edges (% of the original)</legend>
          <div className="mt-1.5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {(["Left", "Top", "Right", "Bottom"] as const).map((name, i) => (
              <label key={name} className="flex flex-col gap-1 text-[12.5px] text-muted">
                {name}
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={Math.round(crop[i] * 100)}
                  onChange={(e) => setEdge(i, Number(e.target.value))}
                  className="h-9 rounded-[5px] border border-line-strong bg-sheet px-2 text-sm text-graphite focus:border-ink focus:outline-none"
                />
              </label>
            ))}
          </div>
          <Button size="sm" variant="ghost" className="mt-2" onClick={() => setBox(FULL)}>
            Reset crop
          </Button>
        </fieldset>
      </div>
      <div className="flex flex-col gap-4">
        <div>
          <p className="mb-2 text-[13px] font-medium">Rotate</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              icon={<RotateCcw className="size-3.5" aria-hidden />}
              onClick={() => onChange(crop, joinRotation((quarter + 3) % 4, fine))}
            >
              90° left
            </Button>
            <Button
              size="sm"
              icon={<RotateCw className="size-3.5" aria-hidden />}
              onClick={() => onChange(crop, joinRotation((quarter + 1) % 4, fine))}
            >
              90° right
            </Button>
          </div>
        </div>
        <Slider
          label="Straighten"
          min={-15}
          max={15}
          step={0.5}
          value={Math.max(-15, Math.min(15, fine))}
          onChange={(v) => onChange(crop, joinRotation(quarter, v))}
          format={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°`}
          hint="Small clockwise (+) or anticlockwise (−) turn to level the lines."
        />
        <div>
          <p className="mb-2 text-[13px] font-medium">Result</p>
          <canvas
            ref={canvas}
            className="max-h-[300px] max-w-full rounded border border-line bg-white"
            aria-label="Preview of the cropped and rotated sample"
            role="img"
          />
          <p className="mt-1 text-[12.5px] text-muted">Total rotation {rotate.toFixed(1)}° clockwise.</p>
        </div>
      </div>
    </div>
  );
}
