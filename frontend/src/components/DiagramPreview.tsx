/** On-screen preview of a ```diagram block (the PDF draws it by hand in the user's ink and handwriting). */

type XY = [number, number];
interface Spec {
  type?: string;
  title?: string;
  caption?: string;
  x_label?: string;
  y_label?: string;
  x_range?: XY;
  y_range?: XY;
  legend?: boolean;
  series?: { name?: string; marker?: string; points?: XY[]; connect?: boolean; bars?: boolean }[];
  points?: { x: number; y: number; marker?: string; label?: string }[];
  circles?: { x: number; y: number; r?: number; around_nearest?: number; dashed?: boolean; label?: string }[];
  lines?: { points: XY[]; arrow?: boolean; dashed?: boolean; label?: string }[];
  labels?: { x: number; y: number; text: string }[];
}

const W = 320;
const H = 200;
const L = 34; // left padding (y label)
const T = 18;

function range(vals: number[]): XY {
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (!(hi > lo)) [lo, hi] = [lo - 1, hi + 1];
  const pad = (hi - lo) * 0.15;
  lo = lo >= 0 && lo - pad < (hi - lo) * 0.6 ? 0 : lo - pad;
  return [lo, hi + pad];
}

function Marker({ kind, x, y, s = 5 }: { kind?: string; x: number; y: number; s?: number }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.4 };
  switch (kind) {
    case "triangle":
      return <polygon {...p} points={`${x},${y - s} ${x + s},${y + s * 0.75} ${x - s},${y + s * 0.75}`} />;
    case "circle":
      return <circle {...p} cx={x} cy={y} r={s * 0.8} />;
    case "square":
      return <rect {...p} x={x - s * 0.75} y={y - s * 0.75} width={s * 1.5} height={s * 1.5} />;
    case "cross":
      return <path {...p} d={`M${x - s * 0.8} ${y - s * 0.8}L${x + s * 0.8} ${y + s * 0.8}M${x - s * 0.8} ${y + s * 0.8}L${x + s * 0.8} ${y - s * 0.8}`} />;
    case "plus":
      return <path {...p} d={`M${x - s} ${y}H${x + s}M${x} ${y - s}V${y + s}`} />;
    case "star": {
      const pts = Array.from({ length: 10 }, (_, k) => {
        const r = k % 2 ? s * 0.6 : s * 1.5;
        return `${x + r * Math.sin((k * Math.PI) / 5)},${y - r * Math.cos((k * Math.PI) / 5)}`;
      });
      return <polygon {...p} points={pts.join(" ")} />;
    }
    default:
      return <circle cx={x} cy={y} r={2.2} fill="currentColor" />;
  }
}

export function DiagramPreview({ src }: { src: string }) {
  let spec: Spec;
  try {
    spec = JSON.parse(src);
    if (typeof spec !== "object" || spec === null) throw new Error("not an object");
  } catch (err) {
    return (
      <p className="rounded border border-[#efc9c5] bg-margin-soft px-3 py-2 text-[13px] text-margin">
        This diagram could not be read ({err instanceof Error ? err.message : "invalid"}). Fix the JSON in the answer text.
      </p>
    );
  }
  const all: XY[] = [
    ...(spec.series ?? []).flatMap((s) => s.points ?? []),
    ...(spec.points ?? []).map((p) => [p.x, p.y] as XY),
    ...(spec.lines ?? []).flatMap((l) => l.points),
  ];
  const [x0, x1] = spec.x_range ?? range(all.map((p) => p[0]).concat(all.length ? [] : [0, 1]));
  const [y0, y1] = spec.y_range ?? range(all.map((p) => p[1]).concat(all.length ? [] : [0, 1]));
  const pw = W - L - 12;
  const ph = H - T - 22;
  const to = ([x, y]: XY): XY => [L + ((x - x0) / (x1 - x0)) * pw, T + ph - ((y - y0) / (y1 - y0)) * ph];
  const plotted = [...(spec.series ?? []).flatMap((s) => (s.points ?? []).map(to)), ...(spec.points ?? []).map((p) => to([p.x, p.y]))];
  const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);

  return (
    <figure className="my-2 text-ink">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-md" role="img" aria-label={spec.caption || spec.title || "Diagram"}>
        <path d={`M${L} ${T - 6}V${T + ph}H${L + pw + 8}`} fill="none" stroke="currentColor" strokeWidth={1.4} />
        {spec.y_label && (
          <text x={L + 4} y={T - 4} fontSize={10} fill="currentColor">
            {spec.y_label}
          </text>
        )}
        {spec.x_label && (
          <text x={L + pw + 8} y={T + ph + 14} fontSize={10} textAnchor="end" fill="currentColor">
            {spec.x_label}
          </text>
        )}
        {(spec.series ?? []).map((s, i) => {
          const pts = (s.points ?? []).map(to);
          return (
            <g key={i}>
              {s.connect && pts.length > 1 && <polyline points={pts.map((p) => p.join(",")).join(" ")} fill="none" stroke="currentColor" strokeWidth={1.2} />}
              {pts.map(([x, y], j) =>
                s.bars ? (
                  <rect key={j} x={x - 6} y={y} width={12} height={T + ph - y} fill="none" stroke="currentColor" strokeWidth={1.2} />
                ) : (
                  <Marker key={j} kind={s.marker} x={x} y={y} />
                ),
              )}
            </g>
          );
        })}
        {(spec.points ?? []).map((p, i) => {
          const [x, y] = to([p.x, p.y]);
          return (
            <g key={i}>
              <Marker kind={p.marker} x={x} y={y} s={p.marker === "star" ? 6 : 5} />
              {p.label && (
                <text x={x + 9} y={y - 7} fontSize={9.5} fill="currentColor">
                  {p.label}
                </text>
              )}
            </g>
          );
        })}
        {(spec.circles ?? []).map((c, i) => {
          const cp = to([c.x, c.y]);
          let r = c.r ? (c.r / (x1 - x0)) * pw : 20;
          if (c.around_nearest) {
            const d = plotted.map((q) => dist(cp, q)).filter((v) => v > 1e-6).sort((a, b) => a - b);
            const k = Math.min(c.around_nearest, d.length);
            if (k) r = k < d.length ? Math.max(d[k - 1] + 4, (d[k - 1] + d[k]) / 2) : d[k - 1] + 6;
          }
          return (
            <g key={i}>
              <circle cx={cp[0]} cy={cp[1]} r={r} fill="none" stroke="currentColor" strokeWidth={1.2} strokeDasharray={c.dashed ? "4 3" : undefined} />
              {c.label && (
                <text x={cp[0] + r * 0.75} y={cp[1] - r * 0.75} fontSize={9.5} fill="currentColor">
                  {c.label}
                </text>
              )}
            </g>
          );
        })}
        {(spec.lines ?? []).map((l, i) => (
          <polyline
            key={i}
            points={l.points.map((p) => to(p).join(",")).join(" ")}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.2}
            strokeDasharray={l.dashed ? "4 3" : undefined}
          />
        ))}
        {(spec.labels ?? []).map((lb, i) => {
          const [x, y] = to([lb.x, lb.y]);
          return (
            <text key={i} x={x} y={y} fontSize={9.5} fill="currentColor">
              {lb.text}
            </text>
          );
        })}
        {spec.legend !== false &&
          (spec.series ?? [])
            .filter((s) => s.name)
            .map((s, i) => (
              <g key={i}>
                <Marker kind={s.bars ? "square" : s.marker} x={W - 78} y={T + 4 + i * 14} s={4} />
                <text x={W - 70} y={T + 8 + i * 14} fontSize={9.5} fill="currentColor">
                  {s.name}
                </text>
              </g>
            ))}
      </svg>
      <figcaption className="text-[13px] text-muted">
        {spec.caption ? `${spec.caption} · ` : ""}drawn by hand in your ink in the PDF
      </figcaption>
    </figure>
  );
}
