import { useEffect, useState } from "react";

export type StageState = "pending" | "running" | "done" | "corrected" | "failed";
export interface Stage {
  label: string;
  state: StageState;
}

const ORDER = ["Question", "DSL", "Validated", "SQL", "Rows"];

/** Question → DSL → Validated → SQL → n rows, one segment per stage. */
export function PipelineStrip({ stages }: { stages: Stage[] }) {
  return (
    <ol className="pipeline" aria-label="compilation pipeline">
      {stages.map((stage, i) => (
        <li key={stage.label + i} className={`stage ${stage.state}`}>
          <span className="stage-dot" aria-hidden />
          <span className="stage-label">{stage.label}</span>
        </li>
      ))}
    </ol>
  );
}

/** The strip as a loading indicator: it fills left to right while the request is out.
 *  The backend answers in one shot (no progress events), so the pace is an estimate —
 *  it stops at the last stage until the real answer replaces it. */
export function PipelineProgress() {
  const [reached, setReached] = useState(0);
  useEffect(() => {
    const motionOk = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!motionOk) {
      setReached(ORDER.length - 2);
      return;
    }
    const timer = window.setInterval(
      () => setReached((r) => Math.min(r + 1, ORDER.length - 2)),
      650,
    );
    return () => window.clearInterval(timer);
  }, []);

  return (
    <PipelineStrip
      stages={ORDER.slice(0, ORDER.length - 1).map((label, i) => ({
        label,
        state: i < reached ? "done" : i === reached ? "running" : "pending",
      }))}
    />
  );
}

/** The strip for a finished answer. */
export function stagesForResult(rowCount: number, corrected: boolean, attempts: number): Stage[] {
  return [
    { label: "Question", state: "done" },
    { label: attempts > 1 ? "DSL · retried" : "DSL", state: attempts > 1 ? "corrected" : "done" },
    { label: corrected ? "Validated · corrected" : "Validated", state: corrected ? "corrected" : "done" },
    { label: "SQL", state: "done" },
    { label: `${rowCount.toLocaleString("en-IN")} row${rowCount === 1 ? "" : "s"}`, state: "done" },
  ];
}

/** The strip for a failure: everything up to the failing stage is done, that one failed. */
export function stagesForError(stage: string): Stage[] {
  const failedAt: Record<string, number> = {
    translate: 1, scope: 1, parse: 1, validate: 2, resolve: 2, clarification: 2, compile: 3,
    request: 0, network: 0,
  };
  const failed = failedAt[stage] ?? 1;
  return ORDER.slice(0, 4).map((label, i) => ({
    label,
    state: i < failed ? "done" : i === failed ? "failed" : "pending",
  }));
}
