import { useState } from "react";
import type { OkResponse } from "../api";
import { highlight } from "../syntax";
import { CopyButton } from "./CopyButton";
import { PipelineStrip, stagesForResult } from "./PipelineStrip";

interface Props {
  result: OkResponse;
  colors: Record<string, string>;
}

/** Collapsed by default: the pipeline, then the DSL, then the SQL that ran. */
export function CompiledQuery({ result, colors }: Props) {
  const [open, setOpen] = useState(false);
  const corrected = result.assumptions.some((a) => a.key === "value_corrected");

  return (
    <div className={`disclosure ${open ? "open" : ""}`}>
      <button className="disclosure-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="chevron" aria-hidden>{open ? "▾" : "▸"}</span>
        Compiled query
        {result.attempts > 1 && <span className="tag">retried once</span>}
      </button>
      {open && (
        <div className="disclosure-body">
          <PipelineStrip stages={stagesForResult(result.row_count, corrected, result.attempts)} />
          <CodeBlock
            title="DSL"
            code={result.dsl}
            language="dsl"
            colors={colors}
            copyLabel="Copy DSL"
          />
          <CodeBlock
            title="SQL"
            code={result.sql}
            language="sql"
            colors={colors}
            copyLabel="Copy SQL"
          />
        </div>
      )}
    </div>
  );
}

function CodeBlock({
  title,
  code,
  language,
  colors,
  copyLabel,
}: {
  title: string;
  code: string;
  language: "dsl" | "sql";
  colors: Record<string, string>;
  copyLabel: string;
}) {
  return (
    <div className="code">
      <div className="code-head">
        <span className="code-title">{title}</span>
        <CopyButton label={copyLabel} value={() => code} />
      </div>
      <pre>
        <code>{highlight(code, language, { metricColors: colors })}</code>
      </pre>
    </div>
  );
}
