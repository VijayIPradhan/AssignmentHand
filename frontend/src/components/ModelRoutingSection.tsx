"use client";

import { useState } from "react";
import { Cpu } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { ModelRouting } from "@/lib/types";
import { Button, ErrorState, LoadingBlock, Notice, SelectField, Sheet } from "./ui";

function shortModel(m: string) {
  return m.replace(/^(global|us|eu|apac|in)\./, "").replace(/^anthropic\./, "");
}

function priceLabel(c: ModelRouting["choices"][number]) {
  if (c.input_per_mtok == null || c.output_per_mtok == null) return "price unknown";
  return `$${c.input_per_mtok} in / $${c.output_per_mtok} out per M tokens`;
}

/** Which model does each kind of AI work, for this account. Empty = the server default. */
export function ModelRoutingSection() {
  const res = useResource<ModelRouting>("model-routing", api.modelRouting);
  const [draft, setDraft] = useState<Record<string, string>>({}); // unsaved edits only
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const data = res.data;

  const current = (t: ModelRouting["tasks"][number]) => draft[t.task] ?? (t.custom ? t.model : "");

  async function save() {
    if (!data) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api.saveModelRouting(Object.fromEntries(data.tasks.map((t) => [t.task, current(t) || null])));
      await res.reload();
      setDraft({});
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet
      title="AI models"
      description="Pick the model for each kind of work. Lighter models are cheaper; anything that fails on one is retried once on the fallback model."
    >
      {res.loading && !data ? (
        <LoadingBlock label="Loading models" />
      ) : res.error && !data ? (
        <ErrorState message={res.error} onRetry={() => void res.reload()} />
      ) : data ? (
        <div className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            {data.tasks.map((t) => (
              <SelectField
                key={t.task}
                label={t.label}
                hint={t.description}
                value={current(t)}
                onChange={(e) => {
                  setDraft({ ...draft, [t.task]: e.target.value });
                  setSaved(false);
                }}
              >
                <option value="">Default: {shortModel(t.default)}</option>
                {data.choices.map((c) => (
                  <option key={c.model} value={c.model}>
                    {shortModel(c.model)} · {priceLabel(c)}
                  </option>
                ))}
              </SelectField>
            ))}
          </div>
          <p className="text-[12.5px] text-muted">
            Fallback model: <span className="font-medium text-graphite">{shortModel(data.fallback)}</span>. The defaults
            come from the server settings (LLM_MODEL_*).
          </p>
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex items-center gap-3">
            <Button variant="primary" icon={<Cpu className="size-4" aria-hidden />} onClick={save} loading={busy}>
              Save models
            </Button>
            {saved && <span className="text-[13px] text-muted">Saved. New AI work uses these models.</span>}
          </div>
        </div>
      ) : null}
    </Sheet>
  );
}
