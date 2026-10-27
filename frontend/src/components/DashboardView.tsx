"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { FilePlus2, FileText, PenLine } from "lucide-react";
import { api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { Dashboard, Job } from "@/lib/types";
import { ActiveJobs, AiStatusLine, ExportRow, JOB_LABEL, NewProfileForm } from "./shared";
import { Button, EmptyState, ErrorState, formatDate, LinkButton, LoadingBlock, PageHeader, Sheet, StatusBadge } from "./ui";

export function DashboardView() {
  const dash = useResource<Dashboard>("dashboard", api.dashboard);
  const [creatingProfile, setCreatingProfile] = useState(false);
  const active = dash.data?.active_jobs.length ?? 0;
  const { reload } = dash;

  // While anything is processing, refresh every 1.5 s so progress is live.
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void reload(true), 1500);
    return () => clearInterval(t);
  }, [active, reload]);

  if (dash.loading && !dash.data) return <LoadingBlock label="Loading your dashboard" />;
  if (dash.error && !dash.data) return <ErrorState message={dash.error} onRetry={() => void dash.reload()} />;
  const d = dash.data!;

  const titleFor = (j: Job) => {
    const label = JOB_LABEL[j.type] ?? j.type;
    const a = j.assignment_id && d.assignments.find((x) => x.id === j.assignment_id);
    const p = j.profile_id && d.profiles.find((x) => x.id === j.profile_id);
    return a ? `${label}: ${a.title}` : p ? `${label}: ${p.name}` : label;
  };

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={<AiStatusLine ai={d.ai} />}
        actions={
          <>
            <LinkButton href="/assignments/new" variant="primary" icon={<FilePlus2 className="size-4" aria-hidden />}>
              New assignment
            </LinkButton>
            <Button icon={<PenLine className="size-4" aria-hidden />} onClick={() => setCreatingProfile(true)}>
              New handwriting profile
            </Button>
          </>
        }
      />

      {creatingProfile && (
        <Sheet title="New handwriting profile" className="mb-8 max-w-xl">
          <NewProfileForm onCancel={() => setCreatingProfile(false)} />
        </Sheet>
      )}

      {active > 0 && (
        <section aria-labelledby="active-h" className="mb-8">
          <h2 id="active-h" className="mb-3 text-lg">
            In progress
          </h2>
          <ActiveJobs jobs={d.active_jobs} describe={titleFor} />
        </section>
      )}

      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Sheet
          title="Recent assignments"
          actions={
            d.assignments.length > 0 && (
              <Link href="/assignments" className="text-sm font-medium text-ink underline-offset-4 hover:underline">
                See all
              </Link>
            )
          }
        >
          {d.assignments.length === 0 ? (
            <EmptyState
              icon={<FileText className="size-7" aria-hidden />}
              title="No assignments yet"
              action={
                <LinkButton href="/assignments/new" variant="primary">
                  Upload an assignment PDF
                </LinkButton>
              }
            >
              Upload the assignment you were given. Its questions are found for you to check, then you answer them and
              export the pages in your handwriting.
            </EmptyState>
          ) : (
            <ul className="-my-3 divide-y divide-line">
              {d.assignments.map((a) => (
                <li key={a.id}>
                  <Link
                    href={`/assignments/${a.id}`}
                    className="-mx-2 flex items-center justify-between gap-4 rounded px-2 py-3 hover:bg-ink-wash/50"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium text-graphite">{a.title}</p>
                      <p className="text-[12.5px] text-muted">
                        {a.subject ? `${a.subject}, ` : ""}updated {formatDate(a.updated_at)}
                      </p>
                    </div>
                    <StatusBadge status={a.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Sheet>

        <div className="flex flex-col gap-8">
          <Sheet
            title="Handwriting profiles"
            actions={
              d.profiles.length > 0 && (
                <Link href="/profiles" className="text-sm font-medium text-ink underline-offset-4 hover:underline">
                  Manage
                </Link>
              )
            }
          >
            {d.profiles.length === 0 ? (
              <EmptyState title="No handwriting yet" className="py-6">
                Create a profile, fill in the printable sample sheet and upload a photo of it.
                <div className="mt-4">
                  <Button variant="primary" size="sm" onClick={() => setCreatingProfile(true)}>
                    New handwriting profile
                  </Button>
                </div>
              </EmptyState>
            ) : (
              <ul className="-my-2 divide-y divide-line">
                {d.profiles.map((p) => (
                  <li key={p.id}>
                    <Link
                      href={`/profiles/${p.id}`}
                      className="-mx-2 flex items-center justify-between gap-3 rounded px-2 py-2.5 hover:bg-ink-wash/50"
                    >
                      <span className="truncate text-sm font-medium">{p.name}</span>
                      <StatusBadge status={p.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Sheet>

          <Sheet title="Exports">
            {d.exports.length === 0 ? (
              <p className="text-sm text-muted">
                Final PDFs appear here after you export them from an assignment.
              </p>
            ) : (
              <ul className="-my-3 divide-y divide-line">
                {d.exports.map((r) => (
                  <ExportRow key={r.id} r={r} title={r.assignment_title || "Assignment"} />
                ))}
              </ul>
            )}
          </Sheet>
        </div>
      </div>
    </>
  );
}
