"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ArrowLeft } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { PublicConfig } from "@/lib/types";
import { FileDrop } from "./FileDrop";
import { AiStatusLine } from "./shared";
import { Button, Notice, PageHeader, Sheet, TextField } from "./ui";

export function NewAssignmentView() {
  const router = useRouter();
  const config = useResource<PublicConfig>("config", api.publicConfig);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const maxMb = config.data?.max_upload_mb;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const a = await api.createAssignment(file, title.trim() || undefined, subject.trim() || undefined);
      router.push(`/assignments/${a.id}?step=questions`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        back={
          <Link href="/assignments" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
            <ArrowLeft className="size-4" aria-hidden /> Assignments
          </Link>
        }
        title="New assignment"
        description="Upload the assignment PDF. The questions are read out of it for you to check before anything is answered."
      />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Sheet>
          <form onSubmit={submit} className="flex flex-col gap-5">
            <FileDrop
              accept="application/pdf,.pdf"
              label="Choose the assignment PDF"
              hint={
                config.data
                  ? `PDF up to ${config.data.max_upload_mb} MB and ${config.data.max_pdf_pages} pages. Scanned PDFs work too.`
                  : "PDF files only. Scanned PDFs work too."
              }
              file={file}
              maxBytes={maxMb ? maxMb * 1024 * 1024 : undefined}
              onFile={(f, err) => {
                setFile(f);
                setError(err ?? null);
                if (f && !title) setTitle(f.name.replace(/\.pdf$/i, ""));
              }}
              disabled={busy}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Title"
                value={title}
                maxLength={255}
                onChange={(e) => setTitle(e.target.value)}
                hint="Defaults to the file name."
              />
              <TextField
                label="Subject (optional)"
                value={subject}
                maxLength={120}
                placeholder="e.g. Physics"
                onChange={(e) => setSubject(e.target.value)}
              />
            </div>
            {error && <Notice tone="error">{error}</Notice>}
            <div>
              <Button type="submit" variant="primary" loading={busy} disabled={!file}>
                {busy ? "Uploading" : "Upload and find questions"}
              </Button>
            </div>
          </form>
        </Sheet>
        <aside className="flex flex-col gap-4 text-sm text-muted">
          <h2 className="text-lg">What happens next</h2>
          <ol className="flex list-decimal flex-col gap-2 pl-5 marker:text-ink-soft">
            <li>Questions, marks and instructions are found in the PDF.</li>
            <li>You check them, fix anything that was read wrongly and confirm.</li>
            <li>You write the answers, or draft them with AI if it is set up, and approve each one.</li>
            <li>The answers are laid out in your handwriting and exported as a PDF.</li>
          </ol>
          {config.data && <AiStatusLine ai={config.data.ai} />}
        </aside>
      </div>
    </>
  );
}
