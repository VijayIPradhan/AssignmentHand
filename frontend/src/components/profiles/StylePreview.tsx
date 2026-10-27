"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import type { Profile } from "@/lib/types";
import { STYLE_DEFAULTS, StyleControls, type StyleValues } from "../StyleControls";
import { Button, Notice, SelectField, Sheet, TextArea } from "../ui";

const SAMPLE_TEXT =
  "The quick brown fox jumps over the lazy dog.\nPack my box with five dozen liquor jugs: 0123456789 (x + y = 2z)";

export function styleOf(p: Profile): StyleValues {
  return { ...STYLE_DEFAULTS, ...Object.fromEntries(Object.entries(p.style ?? {}).filter(([, v]) => v !== null && v !== undefined)) };
}

export function StylePreview({ profile, onSaved }: { profile: Profile; onSaved: (p: Profile) => void }) {
  const [style, setStyle] = useState<StyleValues>(() => styleOf(profile));
  const [text, setText] = useState(SAMPLE_TEXT);
  const [paper, setPaper] = useState("ruled");
  const [img, setImg] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const urlRef = useRef<string | null>(null);

  const saved = styleOf(profile);
  const dirty = (Object.keys(style) as (keyof StyleValues)[]).some((k) => style[k] !== saved[k]);

  const render = useDebounced((t: string, s: StyleValues, pp: string) => {
    if (!t.trim()) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    api
      .previewProfile(profile.id, { text: t.slice(0, 2000), style: s, paper: pp }, c.signal)
      .then(({ blob, warnings: w }) => {
        if (c.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = url;
        setImg(url);
        setWarnings(w);
        setError(null);
      })
      .catch((err) => {
        if (c.signal.aborted) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
  }, 400);

  const { call } = render;
  useEffect(() => {
    call(text, style, paper);
  }, [text, style, paper, profile.library_version, call]);

  useEffect(
    () => () => {
      ctrl.current?.abort();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  async function save() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const p = await api.updateProfile(profile.id, { style });
      onSaved(p);
      setSaveMsg("Saved as this profile’s default style.");
    } catch (err) {
      setSaveMsg(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Sheet
      title="Style and live preview"
      description="Adjust how the handwriting is laid out. Assignments start from these settings and can override them."
    >
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="flex flex-col gap-5">
          <StyleControls value={style} onChange={setStyle} />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>
              Save style
            </Button>
            <Button variant="ghost" disabled={!dirty} onClick={() => setStyle(saved)}>
              Discard changes
            </Button>
            <Button variant="ghost" onClick={() => setStyle(STYLE_DEFAULTS)}>
              Reset to defaults
            </Button>
          </div>
          {saveMsg && (
            <p className="text-[13px] text-muted" role="status">
              {saveMsg}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-3">
          <TextArea label="Preview text" rows={3} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
          <SelectField label="Paper" value={paper} onChange={(e) => setPaper(e.target.value)} className="max-w-48">
            <option value="ruled">Ruled</option>
            <option value="blank">Blank</option>
            <option value="grid">Grid</option>
          </SelectField>
          <div className="relative min-h-40 overflow-hidden rounded border border-line bg-white" aria-busy={loading}>
            {img ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={img} alt={`Preview of “${text.slice(0, 80)}” in this handwriting`} className="block w-full" />
            ) : (
              !error && <div className="flex min-h-40 items-center justify-center text-sm text-muted">Rendering preview</div>
            )}
            {loading && (
              <span className="absolute right-2 top-2 inline-flex items-center gap-1.5 rounded bg-white/90 px-2 py-1 text-[12px] text-muted">
                <Loader2 className="size-3.5 animate-spin" aria-hidden /> Updating
              </span>
            )}
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          {warnings.length > 0 && (
            <ul className="flex flex-col gap-1.5" aria-label="Preview warnings">
              {warnings.map((w) => (
                <li key={w} className="flex gap-2 text-[13px] text-warn">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                  {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Sheet>
  );
}
