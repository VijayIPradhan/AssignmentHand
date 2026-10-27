"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import type { Job } from "./types";

export const TERMINAL: ReadonlySet<string> = new Set(["succeeded", "failed", "cancelled"]);

export interface Resource<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  /** Re-fetch. With `quiet`, keeps showing current data and does not flip `loading`. */
  reload: (quiet?: boolean) => Promise<T | undefined>;
  setData: (update: T | ((prev: T | undefined) => T | undefined)) => void;
}

/** Minimal fetch-and-cache hook: loads on mount / when `key` changes, exposes reload and local updates. */
export function useResource<T>(key: string | null, fetcher: () => Promise<T>): Resource<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(key !== null);
  const fetcherRef = useRef(fetcher);
  const seq = useRef(0);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const reload = useCallback(async (quiet = false) => {
    const mine = ++seq.current;
    if (!quiet) setLoading(true);
    try {
      const value = await fetcherRef.current();
      if (mine === seq.current) {
        setDataState(value);
        setError(null);
      }
      return value;
    } catch (err) {
      if (mine === seq.current) setError(errorMessage(err));
      return undefined;
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (key === null) return;
    // Loading state for a new key is set synchronously inside reload.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [key, reload]);

  const setData = useCallback((update: T | ((prev: T | undefined) => T | undefined)) => {
    setDataState((prev) => (typeof update === "function" ? (update as (p: T | undefined) => T | undefined)(prev) : update));
  }, []);

  return { data, error, loading, reload, setData };
}

export interface JobState {
  job: Job | null;
  error: string | null;
  running: boolean;
}

/**
 * Polls GET /jobs/{id} (about once a second) until the job reaches a terminal state, then calls
 * `onDone(job)` once. Pass `null` to stop tracking.
 */
export function useJob(jobId: string | null, onDone?: (job: Job) => void, intervalMs = 1000): JobState {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(onDone);

  useEffect(() => {
    doneRef.current = onDone;
  });

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ctrl = new AbortController();
    let failures = 0;

    const tick = async () => {
      try {
        const j = await api.job(jobId, ctrl.signal);
        if (stopped) return;
        failures = 0;
        setJob(j);
        setError(null);
        if (TERMINAL.has(j.status)) {
          doneRef.current?.(j);
          return;
        }
      } catch (err) {
        if (stopped) return;
        failures += 1;
        setError(errorMessage(err));
        if (failures > 10) return;
      }
      timer = setTimeout(tick, intervalMs);
    };
    void tick();
    return () => {
      stopped = true;
      ctrl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, intervalMs]);

  const current = jobId && job && job.id === jobId ? job : null;
  return {
    job: current,
    error: jobId ? error : null,
    running: !!jobId && (!current || !TERMINAL.has(current.status)),
  };
}

/** Returns a debounced version of `fn`; the latest call wins. `flush` runs any pending call now. */
export function useDebounced<A extends unknown[]>(fn: (...args: A) => void, delay: number, flushOnUnmount = false) {
  const fnRef = useRef(fn);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<A | null>(null);

  useEffect(() => {
    fnRef.current = fn;
  });

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (pending.current) {
      const args = pending.current;
      pending.current = null;
      fnRef.current(...args);
    }
  }, []);

  const call = useCallback(
    (...args: A) => {
      pending.current = args;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, delay);
    },
    [delay, flush],
  );

  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    pending.current = null;
  }, []);

  useEffect(() => (flushOnUnmount ? flush : cancel), [cancel, flush, flushOnUnmount]);

  return { call, flush, cancel };
}
