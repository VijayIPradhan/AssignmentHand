"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LogOut } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useUser } from "./AppShell";
import { ModelRoutingSection } from "./ModelRoutingSection";
import { SignatureSection } from "./SignatureSection";
import { Button, ConfirmDialog, formatDate, LoadingBlock, Notice, PageHeader, Sheet, TextField } from "./ui";

export function AccountView() {
  const user = useUser();
  const router = useRouter();
  const [busy, setBusy] = useState<"logout" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [typed, setTyped] = useState("");

  async function logout() {
    setBusy("logout");
    setError(null);
    try {
      await api.logout();
      router.replace("/login");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    setError(null);
    try {
      await api.deleteAccount();
      router.replace("/register");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
      setStep(0);
    }
  }

  if (!user) return <LoadingBlock label="Loading your account" />;
  return (
    <>
      <PageHeader title="Account" />
      <div className="flex max-w-2xl flex-col gap-8">
        <Sheet title="Signed in">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-muted">Email</dt>
            <dd className="font-medium">{user.email}</dd>
            <dt className="text-muted">Member since</dt>
            <dd>{formatDate(user.created_at)}</dd>
          </dl>
          <Button
            className="mt-5"
            icon={<LogOut className="size-4" aria-hidden />}
            loading={busy === "logout"}
            onClick={logout}
          >
            Sign out
          </Button>
        </Sheet>

        <SignatureSection />
        <ModelRoutingSection />
        <Sheet title="Delete account">
          <p className="text-sm text-muted">
            Deleting your account permanently removes your handwriting profiles and samples, every assignment you
            uploaded, all answers and every exported PDF. This cannot be undone.
          </p>
          {error && (
            <Notice tone="error" className="mt-4">
              {error}
            </Notice>
          )}
          <Button variant="danger" className="mt-5" onClick={() => setStep(1)}>
            Delete account
          </Button>
        </Sheet>
      </div>

      <ConfirmDialog
        open={step === 1}
        title="Delete your account?"
        confirmLabel="Continue"
        danger
        onConfirm={() => {
          setTyped("");
          setStep(2);
        }}
        onClose={() => setStep(0)}
      >
        All of your data will be removed from this server, including handwriting samples and exported PDFs. Download
        anything you want to keep first.
      </ConfirmDialog>
      <ConfirmDialog
        open={step === 2}
        title="Confirm deletion"
        confirmLabel="Delete everything"
        danger
        busy={busy === "delete"}
        confirmDisabled={typed.trim().toLowerCase() !== user.email.toLowerCase()}
        onConfirm={remove}
        onClose={() => setStep(0)}
      >
        <TextField
          label={`Type your email (${user.email}) to confirm`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
        />
      </ConfirmDialog>
    </>
  );
}
