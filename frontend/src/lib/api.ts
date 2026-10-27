/**
 * Typed client for the AssignmentHand API.
 *
 * Every request is same-origin (`/api/v1/...`, proxied to the backend by next.config rewrites) and
 * sends the httpOnly `ah_session` cookie. Errors are normalised to {@link ApiError}; a 401 sends the
 * user to /login (except for the auth endpoints themselves).
 */
import type {
  Answer,
  Assignment,
  AssignmentSummary,
  AssignmentUpdate,
  Dashboard,
  DocumentInfo,
  Estimate,
  Job,
  Profile,
  PublicConfig,
  Question,
  QuestionIn,
  QuestionUpdate,
  Rendered,
  RenderSettings,
  Sample,
  StyleIn,
  User,
} from "./types";

export const API = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get notConfigured() {
    return this.status === 503 && this.code === "not_configured";
  }
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "validation_error" && Array.isArray(err.details) && err.details.length) {
      const first = err.details[0] as { loc?: unknown[]; msg?: string };
      const field = first.loc?.filter((p) => p !== "body").join(".");
      return field ? `${field}: ${first.msg}` : (first.msg ?? err.message);
    }
    return err.message;
  }
  if (err instanceof DOMException && err.name === "AbortError") return "The request was cancelled.";
  if (err instanceof TypeError) return "Could not reach the server. Check your connection and try again.";
  return err instanceof Error ? err.message : "Something went wrong.";
}

const NO_AUTH_REDIRECT = ["/auth/login", "/auth/register", "/auth/me"];

function redirectToLogin() {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  if (window.location.pathname.startsWith("/login") || window.location.pathname.startsWith("/register")) return;
  // A full page load is intended here: it drops all client state of the expired session.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `/login?next=${encodeURIComponent(here)}`;
}

async function toError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `Request failed (${res.status})`;
  let details: unknown;
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string; details?: unknown } };
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    }
  } catch {
    if (res.status === 413) message = "The file is too large.";
    else if (res.status >= 500) message = "The server had a problem handling this request. Try again shortly.";
  }
  return new ApiError(res.status, code, message, details);
}

type Body = { json?: unknown; form?: FormData };

export interface RequestOptions extends Body {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  signal?: AbortSignal;
}

/** Low-level request returning the raw Response (already checked for errors). */
export async function raw(path: string, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  const res = await fetch(`${API}${path}`, {
    method: opts.method ?? (body ? "POST" : "GET"),
    credentials: "include",
    headers,
    body,
    signal: opts.signal,
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await toError(res);
    if (res.status === 401 && !NO_AUTH_REDIRECT.some((p) => path.startsWith(p))) redirectToLogin();
    throw err;
  }
  return res;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await raw(path, opts);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("json")) return undefined as T;
  return (await res.json()) as T;
}

function form(fields: Record<string, string | Blob | number | null | undefined>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) {
    if (v === null || v === undefined || v === "") continue;
    fd.append(k, typeof v === "number" ? String(v) : v);
  }
  return fd;
}

const enc = encodeURIComponent;

// ------------------------------------------------------------------------------------ URLs
// Images and downloads are fetched by the browser directly (the session cookie goes along).

export const urls = {
  templatePdf: () => `${API}/profiles/template.pdf`,
  sampleImage: (pid: string, sid: string, variant: "original" | "corrected" | "processed", maxSide = 1200, v?: string) =>
    `${API}/profiles/${pid}/samples/${sid}/image?variant=${variant}&max_side=${maxSide}${v ? `&v=${enc(v)}` : ""}`,
  glyphSheet: (pid: string, version: number) => `${API}/profiles/${pid}/glyphs.png?v=${version}`,
  docPage: (aid: string, did: string, n: number) => `${API}/assignments/${aid}/documents/${did}/pages/${n}.png`,
  docPdf: (aid: string, did: string) => `${API}/assignments/${aid}/documents/${did}/source.pdf`,
  figure: (aid: string, key: string) => `${API}/assignments/${aid}/figures?key=${enc(key)}`,
  renderedPage: (rid: string, n: number) => `${API}/rendered/${rid}/pages/${n}.png`,
  download: (rid: string, inline = false) => `${API}/rendered/${rid}/download${inline ? "?inline=true" : ""}`,
};

// ------------------------------------------------------------------------------------ endpoints

export const api = {
  // system
  publicConfig: () => request<PublicConfig>("/config/public"),
  dashboard: () => request<Dashboard>("/dashboard"),
  job: (jid: string, signal?: AbortSignal) => request<Job>(`/jobs/${jid}`, { signal }),
  jobs: (q: { assignment_id?: string; profile_id?: string; active?: boolean; limit?: number }) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined) p.set(k, String(v));
    return request<Job[]>(`/jobs?${p.toString()}`);
  },
  retryJob: (jid: string) => request<Job>(`/jobs/${jid}/retry`, { method: "POST" }),
  exports: (limit = 30) => request<Rendered[]>(`/exports?limit=${limit}`),

  // auth
  register: (email: string, password: string) =>
    request<{ user: User }>("/auth/register", { json: { email, password } }),
  login: (email: string, password: string) => request<{ user: User }>("/auth/login", { json: { email, password } }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<User>("/auth/me"),
  deleteAccount: () => request<void>("/auth/me", { method: "DELETE" }),

  // profiles
  profiles: () => request<Profile[]>("/profiles"),
  createProfile: (name: string) => request<Profile>("/profiles", { json: { name } }),
  profile: (pid: string) => request<Profile>(`/profiles/${pid}`),
  updateProfile: (pid: string, body: { name?: string; style?: StyleIn }) =>
    request<Profile>(`/profiles/${pid}`, { method: "PATCH", json: body }),
  deleteProfile: (pid: string) => request<void>(`/profiles/${pid}`, { method: "DELETE" }),
  uploadSample: (
    pid: string,
    file: File,
    kind: "template" | "freeform",
    opts: { transcript?: string; template_page?: number | null } = {},
  ) =>
    request<Sample>(`/profiles/${pid}/samples`, {
      form: form({ file, kind, transcript: opts.transcript, template_page: opts.template_page }),
    }),
  updateSample: (
    pid: string,
    sid: string,
    body: { transcript?: string; rotate?: number; crop?: number[]; template_page?: number },
  ) => request<Sample>(`/profiles/${pid}/samples/${sid}`, { method: "PATCH", json: body }),
  deleteSample: (pid: string, sid: string) => request<void>(`/profiles/${pid}/samples/${sid}`, { method: "DELETE" }),
  transcribeSample: (pid: string, sid: string) =>
    request<Sample>(`/profiles/${pid}/samples/${sid}/transcribe`, { method: "POST" }),
  processProfile: (pid: string) => request<Job>(`/profiles/${pid}/process`, { method: "POST" }),
  /** Returns a PNG blob plus warnings from the X-Render-Warnings header. */
  previewProfile: async (
    pid: string,
    body: { text: string; style?: StyleIn; paper?: string },
    signal?: AbortSignal,
  ): Promise<{ blob: Blob; warnings: string[] }> => {
    const res = await raw(`/profiles/${pid}/preview`, { json: body, signal });
    let warnings: string[] = [];
    const header = res.headers.get("x-render-warnings");
    if (header) {
      try {
        const parsed: unknown = JSON.parse(header);
        if (Array.isArray(parsed)) warnings = parsed.map(String);
      } catch {
        warnings = [];
      }
    }
    return { blob: await res.blob(), warnings };
  },

  // assignments
  assignments: () => request<AssignmentSummary[]>("/assignments"),
  createAssignment: (file: File, title?: string, subject?: string) =>
    request<Assignment>("/assignments", { form: form({ file, title, subject }) }),
  assignment: (aid: string, signal?: AbortSignal) => request<Assignment>(`/assignments/${aid}`, { signal }),
  updateAssignment: (aid: string, body: AssignmentUpdate) =>
    request<Assignment>(`/assignments/${aid}`, { method: "PATCH", json: body }),
  deleteAssignment: (aid: string) => request<void>(`/assignments/${aid}`, { method: "DELETE" }),
  addReference: (aid: string, file: File) =>
    request<DocumentInfo>(`/assignments/${aid}/references`, { form: form({ file }) }),
  deleteDocument: (aid: string, did: string) =>
    request<void>(`/assignments/${aid}/documents/${did}`, { method: "DELETE" }),
  extract: (aid: string) => request<Job>(`/assignments/${aid}/extract`, { method: "POST" }),
  confirmExtraction: (aid: string) => request<Assignment>(`/assignments/${aid}/confirm-extraction`, { method: "POST" }),

  // questions
  addQuestion: (aid: string, body: QuestionIn) =>
    request<Question>(`/assignments/${aid}/questions`, { json: body }),
  updateQuestion: (qid: string, body: QuestionUpdate) =>
    request<Question>(`/questions/${qid}`, { method: "PATCH", json: body }),
  deleteQuestion: (qid: string) => request<void>(`/questions/${qid}`, { method: "DELETE" }),
  reorderQuestions: (aid: string, ids: string[]) =>
    request<Question[]>(`/assignments/${aid}/questions/reorder`, { json: { ids } }),
  mergeQuestions: (aid: string, ids: string[]) =>
    request<Question>(`/assignments/${aid}/questions/merge`, { json: { ids } }),
  splitQuestion: (qid: string, at: number, number?: string) =>
    request<Question[]>(`/questions/${qid}/split`, { json: { at, number: number || null } }),

  // answers
  estimate: (aid: string) => request<Estimate>(`/assignments/${aid}/estimate`),
  generate: (aid: string, body: { question_ids?: string[] | null; include_approved?: boolean }) =>
    request<Job>(`/assignments/${aid}/generate`, { json: body }),
  writeAnswer: (qid: string, content: string, expected_revision?: number | null) =>
    request<Answer>(`/questions/${qid}/answer`, { method: "PUT", json: { content, expected_revision } }),
  approve: (ansId: string) => request<Answer>(`/answers/${ansId}/approve`, { method: "POST" }),
  unapprove: (ansId: string) => request<Answer>(`/answers/${ansId}/unapprove`, { method: "POST" }),
  restore: (ansId: string, revision: number) =>
    request<Answer>(`/answers/${ansId}/restore/${revision}`, { method: "POST" }),
  approveAll: (aid: string) => request<Answer[]>(`/assignments/${aid}/answers/approve-all`, { method: "POST" }),

  // rendering
  renderConfig: (aid: string) => request<RenderSettings>(`/assignments/${aid}/render-config`),
  saveRenderConfig: (aid: string, settings: RenderSettings) =>
    request<RenderSettings>(`/assignments/${aid}/render-config`, { method: "PUT", json: settings }),
  render: (aid: string, preview: boolean) => request<Job>(`/assignments/${aid}/render`, { json: { preview } }),
  exportCopy: (aid: string, kind: "digital" | "review") =>
    request<Job>(`/assignments/${aid}/export`, { json: { kind } }),
  rendered: (rid: string) => request<Rendered>(`/rendered/${rid}`),
  deleteRendered: (rid: string) => request<void>(`/rendered/${rid}`, { method: "DELETE" }),
};
