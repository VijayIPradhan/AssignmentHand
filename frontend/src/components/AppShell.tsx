"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, Suspense, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { User } from "@/lib/types";
import { cx, ErrorState } from "./ui";
import { Wordmark } from "./Wordmark";

const UserContext = createContext<User | null>(null);

export function useUser() {
  return useContext(UserContext);
}

const NAV = [
  { href: "/", label: "Dashboard", match: (p: string) => p === "/" },
  { href: "/assignments", label: "Assignments", match: (p: string) => p.startsWith("/assignments") },
  { href: "/profiles", label: "Handwriting", match: (p: string) => p.startsWith("/profiles") },
  { href: "/account", label: "Account", match: (p: string) => p.startsWith("/account") },
];

function NavLinks({ pathname }: { pathname: string | null }) {
  return NAV.map((n) => {
    const active = pathname !== null && n.match(pathname);
    return (
      <Link
        key={n.href}
        href={n.href}
        aria-current={active ? "page" : undefined}
        className={cx(
          "relative shrink-0 rounded px-2.5 py-1.5 text-sm transition-colors",
          active ? "text-ink font-medium" : "text-muted hover:text-ink",
        )}
      >
        {n.label}
        {active && <span className="absolute inset-x-2.5 -bottom-[11px] h-[2px] bg-ink" aria-hidden />}
      </Link>
    );
  });
}

function ActiveNavLinks() {
  return <NavLinks pathname={usePathname()} />;
}

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((u) => {
        if (!cancelled) {
          setUser(u);
          setError(null);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          const next = window.location.pathname + window.location.search;
          router.replace(`/login?next=${encodeURIComponent(next)}`);
        } else {
          setError(errorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router, attempt]);

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-ink focus:px-3 focus:py-2 focus:text-white"
      >
        Skip to content
      </a>
      <header className="border-b border-line bg-paper/95 backdrop-blur supports-[backdrop-filter]:bg-paper/80 sticky top-0 z-30">
        <div className="mx-auto flex h-14 max-w-[1240px] items-center gap-6 px-4 sm:px-6">
          <Link href="/" className="shrink-0 rounded" aria-label="AssignmentHand dashboard">
            <Wordmark />
          </Link>
          <nav aria-label="Main" className="relative -mx-1 flex min-w-0 flex-1 gap-0.5 overflow-x-auto">
            <Suspense fallback={<NavLinks pathname={null} />}>
              <ActiveNavLinks />
            </Suspense>
          </nav>
          {user && <span className="hidden truncate text-[13px] text-muted md:block max-w-56">{user.email}</span>}
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-8 sm:px-6 sm:py-10">
        {/* Pages render straight away and load their own data in parallel; any 401 sends the user to /login. */}
        {error ? (
          <ErrorState message={error} onRetry={() => setAttempt((a) => a + 1)} />
        ) : (
          <UserContext.Provider value={user}>{children}</UserContext.Provider>
        )}
      </main>
      <footer className="border-t border-line py-5 text-center text-[12.5px] text-faint">
        AssignmentHand composes letters captured from your own handwriting samples. Check every answer before you hand
        it in.
      </footer>
    </div>
  );
}
