"use client";

import Link from "next/link";
import { useState } from "react";
import { FileDown, PenLine } from "lucide-react";
import { api, urls } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { Profile } from "@/lib/types";
import { NewProfileForm } from "../shared";
import { Button, EmptyState, ErrorState, formatDate, LinkButton, LoadingBlock, PageHeader, Sheet, StatusBadge } from "../ui";

export function ProfilesView() {
  const list = useResource<Profile[]>("profiles", api.profiles);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Handwriting profiles"
        description="A profile holds the letters captured from your handwriting samples. Assignments are written with the profile you choose."
        actions={
          <>
            <LinkButton href={urls.templatePdf()} download icon={<FileDown className="size-4" aria-hidden />}>
              Download template sheet
            </LinkButton>
            <Button variant="primary" icon={<PenLine className="size-4" aria-hidden />} onClick={() => setCreating(true)}>
              New profile
            </Button>
          </>
        }
      />
      {creating && (
        <Sheet title="New handwriting profile" className="mb-8 max-w-xl">
          <NewProfileForm onCancel={() => setCreating(false)} />
        </Sheet>
      )}
      {list.loading && !list.data ? (
        <LoadingBlock />
      ) : list.error && !list.data ? (
        <ErrorState message={list.error} onRetry={() => void list.reload()} />
      ) : list.data!.length === 0 ? (
        !creating && (
          <EmptyState
            icon={<PenLine className="size-7" aria-hidden />}
            title="Create your first handwriting profile"
            action={
              <Button variant="primary" onClick={() => setCreating(true)}>
                New profile
              </Button>
            }
          >
            You will print a sample sheet, fill it in by hand and upload a photo or scan of it.
          </EmptyState>
        )
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {list.data!.map((p) => {
            const m = p.metrics;
            return (
              <li key={p.id}>
                <Link
                  href={`/profiles/${p.id}`}
                  className="flex h-full flex-col gap-3 rounded-sheet border border-line bg-sheet p-5 hover:border-ink-soft"
                >
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="text-lg leading-snug">{p.name}</h2>
                    <StatusBadge status={p.status} />
                  </div>
                  <p className="text-[13px] text-muted">
                    {p.samples?.length ?? 0} {(p.samples?.length ?? 0) === 1 ? "sample" : "samples"}
                    {m.char_count ? `, ${m.char_count} characters captured` : ""}
                  </p>
                  <p className="mt-auto text-[12.5px] text-faint">Updated {formatDate(p.updated_at)}</p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
