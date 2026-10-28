"use client";

import { useState } from "react";
import { PenLine, Trash2 } from "lucide-react";
import { api, errorMessage, urls } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { SignatureInfo } from "@/lib/types";
import { FileDrop } from "./FileDrop";
import { Button, ErrorState, LoadingBlock, Notice, Sheet } from "./ui";

const PREVIEW_INK = "#1f3a93";

/** Upload / replace / remove the user's own signature. It is used only where they turn it on. */
export function SignatureSection() {
  const sig = useResource<SignatureInfo>("signature", api.signature);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<"upload" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function upload() {
    if (!file) return;
    setBusy("upload");
    setError(null);
    try {
      await api.uploadSignature(file);
      setFile(null);
      await sig.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    setError(null);
    try {
      await api.deleteSignature();
      await sig.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Sheet
      title="Your signature"
      description="Used on “Signature” lines of assignment pages, only when you turn on “Sign the pages” for an assignment."
    >
      {sig.loading && !sig.data ? (
        <LoadingBlock label="Loading signature" />
      ) : sig.error && !sig.data ? (
        <ErrorState message={sig.error} onRetry={() => void sig.reload()} />
      ) : (
        <div className="flex flex-col gap-4">
          {sig.data?.available ? (
            <div className="flex flex-wrap items-center gap-4">
              <div className="rounded-[5px] border border-line bg-white px-4 py-3">
                {/* eslint-disable-next-line @next/next/no-img-element -- private, authenticated image */}
                <img
                  src={urls.signaturePreview(PREVIEW_INK, sig.data.version)}
                  alt="Your signature"
                  className="h-16 w-auto"
                />
              </div>
              <Button
                variant="quiet-danger"
                icon={<Trash2 className="size-3.5" aria-hidden />}
                loading={busy === "delete"}
                onClick={remove}
              >
                Remove
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted">
              No signature yet. Sign a plain white sheet with your usual pen, then photograph it close up in good
              light. The paper background is removed automatically; only the ink is kept.
            </p>
          )}
          <FileDrop
            accept="image/png,image/jpeg,.png,.jpg,.jpeg"
            label={sig.data?.available ? "Replace with a new photo" : "Choose a photo of your signature"}
            file={file}
            onFile={(f, err) => {
              setFile(f);
              setError(err ?? null);
            }}
            compact
          />
          {error && <Notice tone="error">{error}</Notice>}
          <div>
            <Button
              variant="primary"
              icon={<PenLine className="size-4" aria-hidden />}
              onClick={upload}
              loading={busy === "upload"}
              disabled={!file}
            >
              Save signature
            </Button>
          </div>
          <p className="text-[12.5px] text-muted">Only upload your own signature.</p>
        </div>
      )}
    </Sheet>
  );
}
