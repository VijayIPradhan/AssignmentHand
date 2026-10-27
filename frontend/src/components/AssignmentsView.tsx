"use client";

import Link from "next/link";
import { FilePlus2, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { AssignmentSummary } from "@/lib/types";
import { EmptyState, ErrorState, formatDate, LinkButton, LoadingBlock, PageHeader, Sheet, StatusBadge } from "./ui";

export function AssignmentsView() {
  const list = useResource<AssignmentSummary[]>("assignments", api.assignments);
  return (
    <>
      <PageHeader
        title="Assignments"
        actions={
          <LinkButton href="/assignments/new" variant="primary" icon={<FilePlus2 className="size-4" aria-hidden />}>
            New assignment
          </LinkButton>
        }
      />
      {list.loading && !list.data ? (
        <LoadingBlock />
      ) : list.error && !list.data ? (
        <ErrorState message={list.error} onRetry={() => void list.reload()} />
      ) : list.data!.length === 0 ? (
        <EmptyState
          icon={<FileText className="size-7" aria-hidden />}
          title="No assignments yet"
          action={
            <LinkButton href="/assignments/new" variant="primary">
              Upload an assignment PDF
            </LinkButton>
          }
        >
          Start by uploading the PDF you were given.
        </EmptyState>
      ) : (
        <Sheet>
          <ul className="-my-3 divide-y divide-line">
            {list.data!.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/assignments/${a.id}`}
                  className="-mx-2 flex items-center justify-between gap-4 rounded px-2 py-3.5 hover:bg-ink-wash/50"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{a.title}</p>
                    <p className="text-[12.5px] text-muted">
                      {a.subject ? `${a.subject}, ` : ""}created {formatDate(a.created_at)}, updated{" "}
                      {formatDate(a.updated_at)}
                    </p>
                  </div>
                  <StatusBadge status={a.status} />
                </Link>
              </li>
            ))}
          </ul>
        </Sheet>
      )}
    </>
  );
}
