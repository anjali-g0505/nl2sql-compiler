import { useState } from "react";
import type { InvestigationResponse, InvestigationStep, OkResponse } from "../api";
import { highlight } from "../syntax";
import { Prose } from "./AnswerCard";
import { CopyButton } from "./CopyButton";
import { DataTable } from "./DataTable";

/** A "why" question, answered by evidence.
 *
 * The conclusion comes first, and every figure in it must come from one of the steps
 * below, so the steps are open by default. Each shows what it set out to establish,
 * the DSL that ran, and the rows it returned. A step that failed is shown as failed;
 * the conclusion was told not to guess its result, and the reader should see why.
 */
export function InvestigationCard({ result }: { result: InvestigationResponse }) {
  const [open, setOpen] = useState(true);
  const ran = result.steps.filter((s) => s.status === "ok").length;

  return (
    <article className="card answer investigation">
      <header className="answer-head">
        <span className="answer-kind">Investigation</span>
        <CopyButton label="Copy" value={() => result.text} />
      </header>

      {result.entities.length > 0 && (
        <p className="investigation-entities">
          {result.entities.map((e) => (
            <span key={`${e.field}-${e.value}`} className="pill entity" title={`matched "${e.matched}"`}>
              {e.field}: {e.value}
            </span>
          ))}
        </p>
      )}

      <Prose text={result.text} citations={result.citations} />

      {result.stopped_early && <p className="investigation-note">{result.stopped_early}</p>}

      <section className={`disclosure ${open ? "open" : ""}`}>
        <button className="disclosure-head" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="chevron" aria-hidden>{open ? "▾" : "▸"}</span>
          How this was worked out
          <span className="tag">
            {ran} of {result.steps.length} quer{result.steps.length === 1 ? "y" : "ies"} ran
          </span>
        </button>
        {open && (
          <ol className="steps">
            {result.steps.map((step, i) => (
              <Step key={i} number={i + 1} step={step} />
            ))}
            {result.plan_note && <li className="investigation-note">{result.plan_note}</li>}
          </ol>
        )}
      </section>
    </article>
  );
}

function Step({ number, step }: { number: number; step: InvestigationStep }) {
  return (
    <li className={`step ${step.status}`}>
      <div className="step-head">
        <span className="step-number">{number}</span>
        <span className="step-purpose">{step.purpose || "Query"}</span>
        <span className="step-status">{STATUS[step.status]}</span>
      </div>
      <pre className="code-inline">
        <code>{highlight(step.dsl, "dsl")}</code>
      </pre>
      {step.dsl_planned && (
        <p className="step-note">Clauses reordered to the grammar's order before running.</p>
      )}
      {step.status === "ok" ? (
        step.row_count ? (
          <>
            <DataTable result={asResult(step)} cap={Number.POSITIVE_INFINITY} />
            {(step.row_count ?? 0) > (step.rows?.length ?? 0) && (
              <p className="table-note">
                First {step.rows?.length} of {step.row_count} rows, as given to the model.
              </p>
            )}
          </>
        ) : (
          <p className="empty-result">No rows matched.</p>
        )
      ) : (
        <ul className="step-errors">
          {(step.errors ?? []).map((message, i) => (
            <li key={i}>{message}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

const STATUS: Record<InvestigationStep["status"], string> = {
  ok: "ran",
  error: "failed",
  needs_clarification: "needed a clarification",
};

/** A step's result in the shape the table component reads. */
function asResult(step: InvestigationStep): OkResponse {
  const rows = step.rows ?? [];
  return {
    status: "ok",
    question: null,
    dsl: step.dsl,
    sql: step.sql ?? "",
    chart_type: step.chart_type ?? "TABLE",
    assumptions: [],
    metric_columns: step.metric_columns ?? [],
    dimension_columns: step.dimension_columns ?? [],
    labels: step.labels ?? {},
    unit: step.unit ?? null,
    columns: step.columns ?? Object.keys(rows[0] ?? {}),
    rows,
    row_count: rows.length,
    attempts: 1,
  };
}
