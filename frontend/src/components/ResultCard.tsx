import { useMemo, useRef, useState } from "react";
import type { OkResponse } from "../api";
import { chartToPng, rowsAsText } from "../chartImage";
import { columnLabel, formatValue } from "../format";
import { ChartView } from "./ChartView";
import { CompiledQuery } from "./CompiledQuery";
import { DataTable, ROW_CAPS } from "./DataTable";
import { OverflowMenu } from "./OverflowMenu";
import { seriesColors } from "../theme";



/** A successful answer: the number, chart or table, then how it was computed. */
export function ResultCard({ result }: { result: OkResponse }) {
  const drawable = result.chart_type !== "TABLE" && result.chart_type !== "KPI";
  const [showTable, setShowTable] = useState(!drawable);
  const [cap, setCap] = useState(ROW_CAPS[0].value);
  const chartArea = useRef<HTMLDivElement>(null);

  // metric -> series colour, shared with the DSL and SQL highlighting
  const colors = useMemo(() => {
    const palette = seriesColors();
    const map: Record<string, string> = {};
    result.metric_columns.forEach((metric, i) => (map[metric] = palette[i % palette.length]));
    return map;
  }, [result.metric_columns]);

  const headers = result.columns.map((c) => columnLabel(c, result));
  const asText = () => rowsAsText(result.columns, headers, result.rows);
  const asCsv = () =>
    [headers, ...result.rows.map((row) => result.columns.map((c) => row[c] ?? ""))]
      .map((line) => line.map(csvCell).join(","))
      .join("\n");

  const actions = [
    ...(drawable && !showTable
      ? [{
          label: "Copy chart",
          run: async () => {
            const png = await chartToPng(chartArea.current?.querySelector("svg") ?? null);
            if (png) await copyImage(png, `intentql-${result.chart_type.toLowerCase()}.png`);
          },
        }]
      : []),
    { label: "Copy table", run: () => navigator.clipboard.writeText(asText()) },
    { label: "Copy DSL", run: () => navigator.clipboard.writeText(result.dsl) },
    { label: "Copy SQL", run: () => navigator.clipboard.writeText(result.sql) },
    { label: "Download CSV", run: () => download(asCsv(), "intentql-result.csv") },
  ];

  return (
    <article className="card result">
      <header className="card-head">
        <span className="rows">
          {result.row_count.toLocaleString("en-IN")} row{result.row_count === 1 ? "" : "s"}
        </span>
        <div className="head-actions">
          {showTable && (
            <label className="cap">
              Show
              <select value={String(cap)} onChange={(e) => setCap(Number(e.target.value))}>
                {ROW_CAPS.map((option) => (
                  <option key={option.label} value={String(option.value)}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {drawable && (
            <div className="segmented tiny" role="group" aria-label="view">
              <button className={!showTable ? "active" : ""} onClick={() => setShowTable(false)}>
                Chart
              </button>
              <button className={showTable ? "active" : ""} onClick={() => setShowTable(true)}>
                Table
              </button>
            </div>
          )}
          <OverflowMenu items={actions} />
        </div>
      </header>

      {result.row_count === 0 ? (
        <p className="empty-result">No rows matched this query.</p>
      ) : result.chart_type === "KPI" ? (
        <div className="kpi">
          <div className="kpi-value" style={{ color: colors[result.metric_columns[0]] }}>
            {formatValue(result.rows[0][result.metric_columns[0]], result.metric_columns[0])}
          </div>
          <div className="kpi-label">{columnLabel(result.metric_columns[0], result)}</div>
        </div>
      ) : showTable || !drawable ? (
        <DataTable result={result} cap={cap} />
      ) : (
        <div ref={chartArea} className="chart">
          <ChartView result={result} colors={colors} />
        </div>
      )}

      {result.assumptions.length > 0 && (
        <ul className="assumptions">
          {result.assumptions.map((a, i) => (
            <li key={`${a.key}-${i}`}>
              {a.key === "value_corrected" && a.params?.raw ? (
                <>
                  <span className="pill corrected">
                    {a.params.raw} → {a.params.resolved}
                  </span>
                  <span className="assumption-text">matched in {a.params.field}</span>
                </>
              ) : (
                <span className="assumption-text">{a.text}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <CompiledQuery result={result} colors={colors} />
    </article>
  );
}

function csvCell(value: unknown): string {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function download(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function copyImage(png: Blob, filename: string) {
  try {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
  } catch {
    const url = URL.createObjectURL(png);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }
}
