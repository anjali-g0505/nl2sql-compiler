// Types and calls for the backend contract (app/pipeline.py). Every response has a
// `status`; the HTTP code is informational, so non-2xx bodies are parsed the same way.

export type Row = Record<string, string | number | null>;

export interface Assumption {
  key: string;
  text: string;
  /** The template's values, e.g. {raw, resolved, field} for a corrected value. */
  params?: Record<string, string>;
}

export interface OkResponse {
  status: "ok";
  question: string | null;
  dsl: string;
  sql: string;
  chart_type: "KPI" | "TABLE" | "BAR" | "LINE" | "PIE";
  assumptions: Assumption[];
  metric_columns: string[];
  dimension_columns: string[];
  labels: Record<string, string>;
  unit: "CRORE" | "LAKH" | null;
  columns: string[];
  rows: Row[];
  row_count: number;
  attempts: number;
}

export interface ClarificationQuestion {
  id: string;
  field: string;
  value: string;
  reason: "ambiguous" | "unknown";
  message: string;
  options: { id: string; label: string }[];
}

export interface ClarificationResponse {
  status: "needs_clarification";
  clarification_id: string;
  question: string | null;
  dsl: string;
  questions: ClarificationQuestion[];
  expires_in_seconds: number;
  attempts: number;
}

export interface ErrorResponse {
  status: "error";
  stage: string;
  errors: string[];
  dsl?: string;
  attempts?: number;
}

export type ApiResponse = OkResponse | ClarificationResponse | ErrorResponse;

async function post(url: string, body: unknown): Promise<ApiResponse> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return networkError("Can't reach the server. Is the API running?");
  }
  try {
    return (await response.json()) as ApiResponse;
  } catch {
    return networkError(`The server returned HTTP ${response.status} without a readable body.`);
  }
}

function networkError(message: string): ErrorResponse {
  return { status: "error", stage: "network", errors: [message] };
}

export function ask(input: { question: string } | { dsl: string }): Promise<ApiResponse> {
  return post("/query", input);
}

export function answerClarification(
  id: string,
  answers: Record<string, string>,
): Promise<ApiResponse> {
  return post(`/clarifications/${encodeURIComponent(id)}`, { answers });
}
