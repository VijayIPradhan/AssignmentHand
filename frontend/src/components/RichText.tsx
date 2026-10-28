import { Fragment } from "react";
import { DiagramPreview } from "./DiagramPreview";
import { cx } from "./ui";

type Block =
  | { kind: "text"; text: string }
  | { kind: "table"; header: string[] | null; rows: string[][] }
  | { kind: "diagram"; src: string };

const SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(line: string) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function parse(text: string): Block[] {
  const out: Block[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let buf: string[] = [];
  const flush = () => {
    if (buf.length) out.push({ kind: "text", text: buf.join("\n") });
    buf = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```diagram/i.test(line.trim())) {
      flush();
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) body.push(lines[i]);
      out.push({ kind: "diagram", src: body.join("\n") });
      continue;
    }
    if (line.trim().startsWith("|") && line.includes("|", line.indexOf("|") + 1)) {
      const tbl: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) tbl.push(lines[i++]);
      i--;
      if (tbl.length >= 2) {
        flush();
        const hasSep = tbl.length > 1 && SEP.test(tbl[1]);
        const header = hasSep ? cells(tbl[0]) : null;
        const rows = (hasSep ? tbl.slice(2) : tbl).filter((r) => !SEP.test(r)).map(cells);
        out.push({ kind: "table", header, rows });
        continue;
      }
      buf.push(...tbl);
      continue;
    }
    buf.push(line);
  }
  flush();
  return out;
}

/** Plain text with pipe tables rendered as tables and ```diagram blocks previewed (questions and answers). */
export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks = parse(text);
  return (
    <div className={cx("prose-q text-[15px] leading-relaxed", className)}>
      {blocks.map((b, i) => (
        <Fragment key={i}>
          {b.kind === "diagram" ? (
            <DiagramPreview src={b.src} />
          ) : b.kind === "text" ? (
            b.text.trim() && <p className="whitespace-pre-wrap break-words">{b.text.replace(/^\n+|\n+$/g, "")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table>
                {b.header && (
                  <thead>
                    <tr>
                      {b.header.map((h, j) => (
                        <th key={j} scope="col">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                )}
                <tbody>
                  {b.rows.map((r, j) => (
                    <tr key={j}>
                      {r.map((c, k) => (
                        <td key={k}>{c}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Fragment>
      ))}
    </div>
  );
}
