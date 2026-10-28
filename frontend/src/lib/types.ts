/**
 * Types for the AssignmentHand API.
 *
 * Generated shapes come from docs/openapi.json (`npm run gen:api` regenerates api-schema.d.ts).
 * Several response fields are declared as free-form objects in the spec; their concrete shapes
 * are written out below to match what the backend actually returns.
 */
import type { components } from "./api-schema";

type S = components["schemas"];

export type User = S["UserOut"];
export type ModelRouting = S["ModelRoutingOut"];
export type Job = S["JobOut"];

/** One entry of a job's step timeline (``events`` on JobOut). */
export interface JobEvent {
  at: string;
  level: "info" | "warning" | "error";
  msg: string;
  prev_step_ms?: number;
  [key: string]: unknown;
}
export type AnswerUpdate = S["AnswerUpdate"];
// Partial: every field of an update is optional (the generator marks defaulted fields as required)
export type AssignmentUpdate = Partial<S["AssignmentUpdate"]>;
export type QuestionIn = Partial<S["QuestionIn"]> & { text: string };
export type QuestionUpdate = Partial<S["QuestionUpdate"]>;
export type StyleIn = S["StyleIn"];
export type Margins = Required<S["Margins"]>;
export type RenderSettings = Omit<S["RenderSettings"], "margins"> & { margins: Margins };

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type AssignmentStatus =
  | "uploaded"
  | "extracting"
  | "extraction_failed"
  | "awaiting_review"
  | "generating_answers"
  | "awaiting_approval"
  | "rendering"
  | "render_failed"
  | "completed";
export type ProfileStatus = "draft" | "processing" | "ready" | "needs_samples" | "failed";
export type SampleStatus = "uploaded" | "processed" | "rejected" | "failed";
export type AnswerStatus = "pending" | "generating" | "generated" | "edited" | "approved" | "failed";

export interface AiStatus {
  provider: string;
  model: string;
  configured: boolean;
}

export interface PublicConfig {
  max_upload_mb: number;
  max_pdf_pages: number;
  allow_registration: boolean;
  ai: AiStatus;
}

export interface SampleQuality {
  width?: number;
  height?: number;
  sharpness?: number;
  contrast?: number;
  ink_ratio?: number;
  issues?: string[];
  ok?: boolean;
}

export interface SampleCorrections {
  rotate?: number;
  crop?: [number, number, number, number];
}

export type Sample = Omit<S["SampleOut"], "corrections" | "quality"> & {
  corrections: SampleCorrections;
  quality: SampleQuality;
};

export interface ProfileMetrics {
  x_height_mm?: number;
  cap_height_mm?: number;
  stroke_mm?: number;
  slant_deg?: number;
  letter_gap_ratio?: number;
  word_gap_ratio?: number;
  line_spacing_ratio?: number;
  glyph_count?: number;
  char_count?: number;
}

export interface QualityReport {
  coverage?: Record<string, number>;
  missing?: {
    lowercase?: string[];
    uppercase?: string[];
    digits?: string[];
    punctuation?: string[];
  };
  outliers_removed?: number | string[];
  advice?: string[];
  template_pages?: number[];
  freeform_samples?: number;
}

export interface ProfileStyle {
  handwriting_size_mm?: number;
  stroke_weight?: number;
  slant_offset_deg?: number;
  char_spacing?: number;
  word_spacing?: number;
  variation?: number;
  ink_color?: string;
}

export type Profile = Omit<
  S["ProfileOut"],
  "samples" | "style" | "metrics" | "coverage" | "quality_report" | "status"
> & {
  status: ProfileStatus;
  style: ProfileStyle;
  metrics: ProfileMetrics;
  coverage: Record<string, number>;
  quality_report: QualityReport;
  samples?: Sample[];
};

export interface DocPage {
  index: number;
  width: number;
  height: number;
  has_text?: boolean;
  ocr?: boolean;
  confidence?: number;
  issues?: string[];
}

export type DocumentInfo = Omit<S["DocumentOut"], "pages"> & {
  role: "assignment" | "reference" | string;
  pages: DocPage[];
};

/** Where an answer is written on the original page, in PDF points from the top-left corner. */
export interface AnswerSpace {
  page: number;
  top: number;
  bottom: number;
  x0: number;
  x1?: number; // only on hand-placed areas
  fraction?: number;
  manual?: boolean; // placed by the user in the answer area editor
}

export interface QuestionPageRef {
  page: number;
  y0: number;
  y1: number;
  answer_space?: AnswerSpace;
}

export interface FigureRef {
  key: string;
  page: number;
  bbox: number[];
}

export interface AnswerFlag {
  type: string;
  message: string;
}

export interface AnswerHistoryEntry {
  revision: number;
  content: string;
  status: string;
  at: string;
}

export type Answer = Omit<S["AnswerOut"], "flags" | "history" | "status"> & {
  status: AnswerStatus;
  flags: AnswerFlag[];
  history: AnswerHistoryEntry[];
};

export type Question = Omit<S["QuestionOut"], "pages" | "figures" | "review_reasons" | "answer"> & {
  pages: QuestionPageRef[];
  figures: FigureRef[];
  review_reasons: string[];
  answer?: Answer | null;
};

export interface RenderWarning {
  type: string;
  message: string;
  page?: number;
}

export type Rendered = Omit<S["RenderedOut"], "warnings"> & {
  kind: "handwritten" | "digital" | "review" | string;
  warnings: RenderWarning[];
};

export interface AnswerPreferences {
  academic_level?: string;
  detail?: "concise" | "moderate" | "detailed";
  language?: string;
  target_words?: number | null;
  technical_depth?: "introductory" | "standard" | "advanced";
  citations?: "none" | "if_available" | "required";
  show_working?: boolean;
  tone?: "formal" | "neutral" | "conversational";
  extra_instructions?: string;
}

export interface AssignmentStats {
  questions?: number;
  needs_review?: number;
  answered?: number;
  approved?: number;
  words?: number;
  cost_usd?: number;
  ai?: AiStatus;
}

export type AssignmentSummary = Omit<S["AssignmentOut"], "preferences" | "status"> & {
  status: AssignmentStatus;
  preferences: AnswerPreferences;
};

export type Assignment = Omit<
  S["AssignmentDetail"],
  "documents" | "questions" | "rendered" | "render_settings" | "active_jobs" | "stats" | "preferences" | "status"
> & {
  status: AssignmentStatus;
  preferences: AnswerPreferences;
  documents: DocumentInfo[];
  questions: Question[];
  rendered: Rendered[];
  render_settings: RenderSettings;
  active_jobs: Job[];
  stats: AssignmentStats;
};

export type DashboardExport = Rendered & { assignment_id: string; assignment_title: string };

export interface Dashboard {
  assignments: AssignmentSummary[];
  profiles: Profile[];
  active_jobs: Job[];
  exports: DashboardExport[];
  ai: AiStatus;
}

export interface Estimate {
  questions: number;
  input_tokens: number;
  output_tokens_min: number;
  output_tokens_max: number;
  cost_usd_min: number | null;
  cost_usd_max: number | null;
  model: string;
  /** Per-model split: each question is routed like generation does (light model, or strongest for hard ones). */
  models: EstimateModel[];
  pricing_known: boolean;
  ai: AiStatus;
}

export interface EstimateModel {
  model: string;
  questions: number;
  hard: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd_min: number | null;
  cost_usd_max: number | null;
}

// ------------------------------------------------------------------------------------ AI usage

export interface UsageAgg {
  calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  unpriced_calls: number;
  estimated: boolean;
  avg_latency_ms: number;
}

export type UsageGroup = UsageAgg & { key: string; title?: string };

export interface UsageCall {
  at: string;
  task: string;
  model: string;
  served_model: string | null;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  status: string;
  error_type: string | null;
  cost_usd: number | null;
  pricing_source: string;
  assignment_id: string | null;
  assignment_title: string | null;
  profile_id: string | null;
}

export interface Budget {
  limit_usd: number | null;
  spent_usd: number;
  remaining_usd: number | null;
  percent: number | null;
  exceeded: boolean;
  warning?: boolean;
}

export interface Usage {
  period_days: number;
  totals: UsageAgg;
  all_time: { calls: number; cost_usd: number };
  by_model: UsageGroup[];
  by_task: UsageGroup[];
  by_day: UsageGroup[];
  by_assignment: UsageGroup[];
  recent: UsageCall[];
  pricing: { model: string; input_per_mtok: number | null; output_per_mtok: number | null; source: string }[];
  routing: Record<string, string>;
  budget: Budget;
}

// ------------------------------------------------------------------------------------ signature & form fields

export interface SignatureInfo {
  available: boolean;
  width?: number;
  height?: number;
  version?: string;
}

export interface FormFieldInfo {
  key: string;
  label: string;
  kind: "signature" | "text";
  pages: number[];
}

export interface AssignmentFields {
  fields: FormFieldInfo[];
  suggestions: Record<string, string>;
  signature_available: boolean;
}
