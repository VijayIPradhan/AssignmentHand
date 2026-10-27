"use client";

import { createContext, useContext } from "react";
import type { Assignment } from "@/lib/types";

export const STEPS = [
  { key: "upload", label: "Upload" },
  { key: "questions", label: "Review questions" },
  { key: "preferences", label: "Answer preferences" },
  { key: "generate", label: "Generate" },
  { key: "review", label: "Review and approve" },
  { key: "handwriting", label: "Handwriting and paper" },
  { key: "export", label: "Preview and export" },
] as const;

export type StepKey = (typeof STEPS)[number]["key"];

export interface WorkspaceCtx {
  a: Assignment;
  /** Re-fetch the assignment quietly (keeps current data on screen). */
  reload: () => Promise<Assignment | undefined>;
  /** Replace the assignment with a fresh server copy (e.g. from a PATCH response). */
  replace: (a: Assignment) => void;
  /** Patch the local copy. */
  update: (fn: (a: Assignment) => Assignment) => void;
  go: (step: StepKey) => void;
  /** Start watching a job the user just started so the page polls until it finishes. */
  watch: (jobId: string) => void;
}

export const Ctx = createContext<WorkspaceCtx | null>(null);

export function useWorkspace(): WorkspaceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWorkspace outside workspace");
  return c;
}
