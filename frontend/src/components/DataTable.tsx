import { useMemo, useState } from "react";
import type { OkResponse, Row } from "../api";
import { columnLabel, formatValue } from "../format";

/** How many rows to put in the DOM. Display only: the query and the data are untouched. */
export const ROW_CAPS = [
  { label: "10k", value: 10_000 },
  { label: "50k", value: 50_000 },
  { label: "1L", value: 100_000 },
  { label: "All", value: Number.POSITIVE_INFINITY },
];

interface Props {
  result: OkResponse;
  cap: number;
}

export function DataTable({ result, cap }: Props) {
  const [sort, setSort] = useState<{ column: string; direction: 1 | -1 } | null>(null);
  const numeric = useMemo(() => new Set(result.metric_columns), [result.metric_columns]);

  const rows = useMemo(() => {
    if (!sort) return result.rows;
    const { column, direction } = sort;
    return [...result.rows].sort((a, b) => direction * compare(a[column], b[column]));
  }, [result.rows, sort]);

  const shown = rows.slice(0, cap);
  const toggle = (column: string) =>
    setSort((current) =>
      current?.column === column
        ? { column, direction: current.direction === 1 ? -1 : 1 }
        : { column, direction: numeric.has(column) ? -1 : 1 },
    );

  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {result.columns.map((column) => (
                <th
                  key={column}
                  className={numeric.has(column) ? "num" : ""}
                  aria-sort={
                    sort?.column === column ? (sort.direction === 1 ? "ascending" : "descending") : "none"
                  }
                >
                  <button onClick={() => toggle(column)}>
                    {columnLabel(column, result)}
                    <span className="sort" aria-hidden>
                      {sort?.column === column ? (sort.direction === 1 ? "↑" : "↓") : "↕"}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i}>
                {result.columns.map((column) => (
                  <td key={column} className={numeric.has(column) ? "num" : ""}>
                    {formatValue(row[column], column)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > shown.length && (
        <p className="table-note">
          Showing {shown.length.toLocaleString("en-IN")} of {rows.length.toLocaleString("en-IN")} rows.
          The query returned every row; this limit only controls what is drawn.
        </p>
      )}
    </>
  );
}

function compare(a: Row[string], b: Row[string]): number {
  if (a === null || a === undefined) return -1;
  if (b === null || b === undefined) return 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "en-IN", { numeric: true });
}
