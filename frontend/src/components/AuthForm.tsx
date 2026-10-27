"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "@/lib/api";
import type { PublicConfig } from "@/lib/types";
import { Button, Notice, TextField } from "./ui";
import { Wordmark } from "./Wordmark";

function safeNext(raw: string | null) {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<PublicConfig | null>(null);

  useEffect(() => {
    api.publicConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  const isRegister = mode === "register";
  const tooShort = password.length > 0 && password.length < 8;
  const mismatch = isRegister && confirm.length > 0 && confirm !== password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password.length < 8) {
      setError("Use a password of at least 8 characters.");
      return;
    }
    if (isRegister && confirm !== password) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (isRegister) await api.register(email.trim(), password);
      else await api.login(email.trim(), password);
      router.replace(next);
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const registrationClosed = isRegister && config && !config.allow_registration;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex justify-center">
          <Wordmark large />
        </div>
        <div className="ruled rounded-sheet border border-line px-6 pb-7 pt-6 shadow-[0_1px_0_var(--color-line)] sm:px-8">
          <h1 className="page-title text-[1.75rem]">{isRegister ? "Create your account" : "Sign in"}</h1>
          <p className="mt-2 text-sm text-muted">
            {isRegister
              ? "Your samples, assignments and exports stay private to this account."
              : "Pick up where you left off with your assignments."}
          </p>
          {registrationClosed ? (
            <Notice tone="warn" className="mt-6" title="Registration is closed">
              New accounts cannot be created on this server. Ask the administrator for access.
            </Notice>
          ) : (
            <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
              <TextField
                label="Email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <TextField
                label="Password"
                type="password"
                autoComplete={isRegister ? "new-password" : "current-password"}
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                hint={isRegister ? "At least 8 characters." : undefined}
                error={isRegister && tooShort ? "At least 8 characters." : null}
              />
              {isRegister && (
                <TextField
                  label="Confirm password"
                  type="password"
                  autoComplete="new-password"
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  error={mismatch ? "Does not match the password above." : null}
                />
              )}
              {error && <Notice tone="error">{error}</Notice>}
              <Button type="submit" variant="primary" loading={busy} disabled={!email || !password} className="mt-1">
                {isRegister ? "Create account" : "Sign in"}
              </Button>
            </form>
          )}
        </div>
        <p className="mt-6 text-center text-sm text-muted">
          {isRegister ? "Already have an account? " : "New here? "}
          <Link
            href={`${isRegister ? "/login" : "/register"}${next !== "/" ? `?next=${encodeURIComponent(next)}` : ""}`}
            className="font-medium text-ink underline decoration-rule underline-offset-4 hover:decoration-ink"
          >
            {isRegister ? "Sign in" : "Create an account"}
          </Link>
        </p>
      </div>
    </div>
  );
}
