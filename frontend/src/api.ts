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
  expires_in_seconds?: number;
  attempts?: number;
  /** Set when the agent paused: the answer goes to /agent/clarify for this session. */
  session_id?: string;
  /** How many more times the agent may ask before giving up (max 2 a question). */
  interrupts_left?: number;
}

export interface ErrorResponse {
  status: "error";
  stage: string;
  errors: string[];
  dsl?: string;
  attempts?: number;
  /** Over the per-minute token budget: seconds until it frees up. */
  retry_after?: number;
}

/** One query the investigation ran, with what came back. */
export interface InvestigationStep {
  purpose: string;
  dsl: string;
  /** What the planner wrote, when the clauses had to be put back in order. */
  dsl_planned?: string;
  status: "ok" | "error" | "needs_clarification";
  rows?: Row[];
  row_count?: number;
  columns?: string[];
  metric_columns?: string[];
  dimension_columns?: string[];
  labels?: Record<string, string>;
  chart_type?: OkResponse["chart_type"];
  unit?: OkResponse["unit"];
  sql?: string;
  assumptions?: string[];
  errors?: string[];
}

/** A "why" question: a plan of up to five queries, their results, and a conclusion
 *  drawn only from those results. Only /agent/ask returns this. */
export interface InvestigationResponse {
  status: "investigation";
  question: string;
  text: string;
  steps: InvestigationStep[];
  /** Why the remaining steps were skipped, e.g. the entity isn't an outlier. */
  stopped_early: string | null;
  plan_note: string | null;
  entities: { field: string; value: string; matched: string }[];
  citations: string[];
  passages: string[];
  grounded: boolean;
}

/** Prose rather than rows: an answer from the documentation, or an explanation of a
 *  result. Only `/ask` and `/agent/ask` return this — `/query` never does. */
export interface AnswerResponse {
  status: "answer";
  text: string;
  /** Sources the model actually cited, as "kb-09 > Mixed-outcome tables". */
  citations: string[];
  passages: string[];   // chunk ids, for debugging
  /** False when nothing was retrieved: `text` then says it isn't in the docs. */
  grounded: boolean;
  /** Present only for an explanation: the result being explained. */
  explains?: { dsl: string | null; question: string | null };
}

/** Every response from /agent/ask also says which branch handled it. */
export type Route = "data" | "knowledge" | "explain" | "investigate";

export type ApiResponse = (
  | OkResponse
  | ClarificationResponse
  | ErrorResponse
  | AnswerResponse
  | InvestigationResponse
) & { route?: Route };

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

/** One conversation per page load. The agent keys its memory on it: "explain this"
 *  refers to the last result in this session, and a paused clarification waits here. */
export const SESSION_ID: string =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** A question goes to the agent, which routes it: rows, an answer from the docs, an
 *  explanation of the last result, or an investigation. DSL skips the agent and goes
 *  straight to the compiler — no model call. */
export function ask(input: { question: string } | { dsl: string }): Promise<ApiResponse> {
  if ("dsl" in input) return post("/query", input);
  return post("/agent/ask", { session_id: SESSION_ID, message: input.question });
}

/** The agent resumes its own paused turn; a clarification from DSL goes back to /query's. */
export function answerClarification(
  clarification: ClarificationResponse,
  answers: Record<string, string>,
): Promise<ApiResponse> {
  if (clarification.session_id) {
    return post("/agent/clarify", { session_id: clarification.session_id, answers });
  }
  return post(`/clarifications/${encodeURIComponent(clarification.clarification_id)}`, { answers });
}
