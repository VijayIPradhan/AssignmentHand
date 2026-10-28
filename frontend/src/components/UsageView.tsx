"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { UsageAgg, UsageCall, UsageGroup } from "@/lib/types";
import {
  Badge,
  ErrorState,
  LoadingBlock,
  Notice,
  PageHeader,
  SelectField,
  Sheet,
  cx,
} from "./ui";

const TASK_LABEL: Record<string, string> = {
  answer: "Writing answers",
  answer_hard: "Hard answers",
  transcribe: "Transcribing handwriting",
  ocr: "Reading scanned pages",
  verify: "Checking cut-out letters",
};

function usd(n: number | null | undefined, digits = 4) {
  if (n === null || n === undefined) return "-";
  return n < 0.01 && n > 0 ? `$${n.toFixed(digits)}` : `$${n.toFixed(2)}`;
}

function num(n: number) {
  return n.toLocaleString();
}

function shortModel(m: string) {
  return m.replace(/^(global|us|eu|apac|in)\./, "").replace(/^anthropic\./, "");
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-sheet border border-line bg-sheet px-4 py-3">
      <p className="text-[12.5px] uppercase tracking-wide text-muted">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-graphite tabular-nums">
        {value}
      </p>
      {hint && <p className="mt-0.5 text-[12.5px] text-muted">{hint}</p>}
    </div>
  );
}

function GroupTable({
  rows,
  label,
  render,
}: {
  rows: UsageGroup[];
  label: string;
  render?: (k: string, g: UsageGroup) => string;
}) {
  if (rows.length === 0)
    return <p className="text-sm text-muted">No AI calls in this period.</p>;
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[520px] text-left text-sm tabular-nums">
        <thead className="text-[12.5px] text-muted">
          <tr>
            <th className="px-1 pb-2 font-medium">{label}</th>
            <th className="px-1 pb-2 text-right font-medium">Calls</th>
            <th className="px-1 pb-2 text-right font-medium">Input tokens</th>
            <th className="px-1 pb-2 text-right font-medium">Output tokens</th>
            <th className="px-1 pb-2 text-right font-medium">Avg time</th>
            <th className="px-1 pb-2 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((g) => (
            <tr key={g.key}>
              <td className="px-1 py-2 font-medium text-graphite">
                {render ? render(g.key, g) : g.key}
                {g.errors > 0 && (
                  <Badge tone="warn" className="ml-2">
                    {g.errors} failed
                  </Badge>
                )}
              </td>
              <td className="px-1 py-2 text-right">{num(g.calls)}</td>
              <td className="px-1 py-2 text-right">{num(g.input_tokens)}</td>
              <td className="px-1 py-2 text-right">{num(g.output_tokens)}</td>
              <td className="px-1 py-2 text-right">
                {(g.avg_latency_ms / 1000).toFixed(1)}s
              </td>
              <td className="px-1 py-2 text-right">
                {usd(g.cost_usd)}
                {g.estimated && (
                  <span title="Estimated at Anthropic list price">*</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DayBars({ days }: { days: UsageGroup[] }) {
  if (days.length === 0)
    return <p className="text-sm text-muted">No AI calls in this period.</p>;
  const max = Math.max(...days.map((d) => d.cost_usd), 1e-9);
  return (
    <ul className="space-y-1.5" aria-label="Cost per day">
      {days.map((d) => (
        <li
          key={d.key}
          className="grid grid-cols-[88px_minmax(0,1fr)_88px] items-center gap-3 text-[13px] tabular-nums"
        >
          <span className="text-muted">{d.key}</span>
          <span className="h-3 rounded-full bg-paper-deep" aria-hidden>
            <span
              className="block h-3 rounded-full bg-ink"
              style={{ width: `${Math.max(2, (100 * d.cost_usd) / max)}%` }}
            />
          </span>
          <span className="text-right">
            {usd(d.cost_usd)} · {d.calls}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CallsTable({ calls }: { calls: UsageCall[] }) {
  if (calls.length === 0)
    return <p className="text-sm text-muted">No AI calls yet.</p>;
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full min-w-[760px] text-left text-[13px] tabular-nums">
        <thead className="text-[12.5px] text-muted">
          <tr>
            <th className="px-1 pb-2 font-medium">Time</th>
            <th className="px-1 pb-2 font-medium">Task</th>
            <th className="px-1 pb-2 font-medium">Model</th>
            <th className="px-1 pb-2 font-medium">For</th>
            <th className="px-1 pb-2 text-right font-medium">
              Tokens in / out
            </th>
            <th className="px-1 pb-2 text-right font-medium">Time taken</th>
            <th className="px-1 pb-2 text-right font-medium">Cost</th>
            <th className="px-1 pb-2 font-medium">Result</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {calls.map((c, i) => (
            <tr key={`${c.at}-${i}`}>
              <td className="px-1 py-1.5 whitespace-nowrap text-muted">
                {new Date(c.at).toLocaleString()}
              </td>
              <td className="px-1 py-1.5">{TASK_LABEL[c.task] ?? c.task}</td>
              <td className="px-1 py-1.5">
                {shortModel(c.served_model || c.model)}
              </td>
              <td className="max-w-[200px] truncate px-1 py-1.5 text-muted">
                {c.assignment_title ??
                  (c.profile_id ? "Handwriting profile" : "-")}
              </td>
              <td className="px-1 py-1.5 text-right">
                {num(c.input_tokens)} / {num(c.output_tokens)}
              </td>
              <td className="px-1 py-1.5 text-right">
                {(c.latency_ms / 1000).toFixed(1)}s
              </td>
              <td className="px-1 py-1.5 text-right">
                {usd(c.cost_usd)}
                {c.pricing_source === "list_price_estimate" && (
                  <span title="Estimated at Anthropic list price">*</span>
                )}
              </td>
              <td className="px-1 py-1.5">
                {c.status === "ok" ? (
                  <Badge tone="ok">ok</Badge>
                ) : (
                  <Badge tone="error">{c.error_type ?? "failed"}</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function totalsHint(t: UsageAgg) {
  const parts = [`${num(t.input_tokens)} in · ${num(t.output_tokens)} out`];
  if (t.unpriced_calls)
    parts.push(`${t.unpriced_calls} calls without a known price`);
  return parts.join(" · ");
}

export function UsageView() {
  const [days, setDays] = useState(30);
  const { data, error, loading, reload } = useResource(`usage-${days}`, () =>
    api.usage(days),
  );

  return (
    <div>
      <PageHeader
        title="AI usage"
        description="Every AI call the app makes for you: which model handled it, tokens, time taken and cost."
        actions={
          <SelectField
            label="Period"
            value={String(days)}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
            <option value="366">Last 12 months</option>
          </SelectField>
        }
      />
      {loading && !data ? (
        <LoadingBlock label="Loading usage" />
      ) : error && !data ? (
        <ErrorState message={error} onRetry={() => reload()} />
      ) : data ? (
        <div className="flex flex-col gap-8">
          {data.budget.exceeded ? (
            <Notice tone="error" title="Monthly AI budget reached">
              You have spent {usd(data.budget.spent_usd, 2)} of your{" "}
              {usd(data.budget.limit_usd, 2)} monthly limit. New AI work is
              paused until next month; you can still edit answers yourself.
            </Notice>
          ) : data.budget.warning ? (
            <Notice tone="warn" title="Approaching your monthly AI budget">
              {usd(data.budget.spent_usd, 2)} of {usd(data.budget.limit_usd, 2)}{" "}
              used ({data.budget.percent}%).
            </Notice>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label={`Cost, ${days} days`}
              value={usd(data.totals.cost_usd, 2)}
              hint={data.totals.estimated ? "* estimated" : undefined}
            />
            <Stat
              label="AI calls"
              value={num(data.totals.calls)}
              hint={
                data.totals.errors
                  ? `${data.totals.errors} failed`
                  : "all succeeded"
              }
            />
            <Stat
              label="Tokens"
              value={num(data.totals.input_tokens + data.totals.output_tokens)}
              hint={totalsHint(data.totals)}
            />
            <Stat
              label="This month"
              value={usd(data.budget.spent_usd, 2)}
              hint={
                data.budget.limit_usd
                  ? `of ${usd(data.budget.limit_usd, 2)} budget`
                  : "no budget set"
              }
            />
          </div>

          <div className="grid items-start gap-8 lg:grid-cols-2">
            <Sheet title="By task" description="What the AI was doing.">
              <GroupTable
                rows={data.by_task}
                label="Task"
                render={(k) => TASK_LABEL[k] ?? k}
              />
            </Sheet>
            <Sheet
              title="By model"
              description="Which model actually served the calls."
            >
              <GroupTable
                rows={data.by_model}
                label="Model"
                render={(k) => shortModel(k)}
              />
            </Sheet>
          </div>

          <div className="grid items-start gap-8 lg:grid-cols-2">
            <Sheet title="Cost per day">
              <DayBars days={data.by_day} />
            </Sheet>
            <Sheet title="By assignment">
              <GroupTable
                rows={data.by_assignment}
                label="Assignment"
                render={(_k, g) => g.title ?? _k}
              />
            </Sheet>
          </div>

          <Sheet
            title="Models and prices"
            description="Which model handles each task, and the price used for cost figures."
          >
            <div className="mb-4 flex flex-wrap gap-2 text-[13px]">
              {Object.entries(data.routing).map(([task, model]) => (
                <Badge key={task} tone="ink">
                  {TASK_LABEL[task] ?? task}: {shortModel(model)}
                </Badge>
              ))}
            </div>
            <table className="w-full text-left text-sm tabular-nums">
              <thead className="text-[12.5px] text-muted">
                <tr>
                  <th className="pb-2 font-medium">Model</th>
                  <th className="pb-2 text-right font-medium">
                    Input / 1M tokens
                  </th>
                  <th className="pb-2 text-right font-medium">
                    Output / 1M tokens
                  </th>
                  <th className="pb-2 pl-4 font-medium">Price source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.pricing.map((p) => (
                  <tr key={p.model}>
                    <td className="py-1.5">{p.model}</td>
                    <td className="py-1.5 text-right">
                      {p.input_per_mtok === null ? "-" : `$${p.input_per_mtok}`}
                    </td>
                    <td className="py-1.5 text-right">
                      {p.output_per_mtok === null
                        ? "-"
                        : `$${p.output_per_mtok}`}
                    </td>
                    <td
                      className={cx(
                        "py-1.5 pl-4",
                        p.source === "unknown" && "text-margin",
                      )}
                    >
                      {{
                        configured: "Your configured price",
                        list_price: "Anthropic list price",
                        list_price_estimate:
                          "Estimate (Anthropic list price; cloud pricing may differ)",
                        unknown: "Unknown - set LLM_PRICING",
                      }[p.source] ?? p.source}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-[12.5px] text-muted">
              * Costs marked with an asterisk are estimates. Set{" "}
              <code>LLM_PRICING</code> to your actual Bedrock rates for exact
              figures. Thinking tokens are billed as output tokens and are
              included.
            </p>
          </Sheet>

          <Sheet
            title="Recent AI calls"
            description={`All-time: ${num(data.all_time.calls)} calls, ${usd(data.all_time.cost_usd, 2)}.`}
          >
            <CallsTable calls={data.recent} />
          </Sheet>
        </div>
      ) : null}
    </div>
  );
}
