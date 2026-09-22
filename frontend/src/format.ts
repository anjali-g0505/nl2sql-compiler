// Display formatting: Indian digit grouping, rates as percentages, readable headers.

import type { OkResponse } from "./api";

// Precision follows magnitude, so `IN CRORE` figures stay readable: 0.092981 crore is
// "0.093", not "0.09", while 1,28,772.65 keeps its paise and a count stays whole.
const whole = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const grouped = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const small = new Intl.NumberFormat("en-IN", { maximumSignificantDigits: 3 });
const percent = new Intl.NumberFormat("en-IN", {
  style: "percent",
  maximumFractionDigits: 2,
});

function formatNumber(value: number): string {
  if (Number.isInteger(value)) return whole.format(value);      // counts
  if (Math.abs(value) >= 1) return grouped.format(value);       // rupees, ticket sizes
  return small.format(value);                                    // crore/lakh fractions
}

export function isRate(metric: string): boolean {
  return metric.endsWith("_rate");
}

export function formatValue(value: unknown, column: string): string {
  // SQL aggregates return NULL when no rows matched (e.g. SUM over nothing): show 0
  if (value === null || value === undefined) value = 0;
  if (typeof value === "number") {
    return isRate(column) ? percent.format(value) : formatNumber(value);
  }
  return String(value);
}

/** Header for a column: the metric's config label (with the unit), or a tidied name. */
export function columnLabel(column: string, result: OkResponse): string {
  const label = result.labels[column];
  if (label) {
    if (result.unit && label.includes("₹")) {
      return label.replace("₹", `₹ ${result.unit === "CRORE" ? "crore" : "lakh"}`);
    }
    return label;
  }
  const tidy = column.replace(/^(iss|acq)_/, "").replace(/_/g, " ");
  return tidy.charAt(0).toUpperCase() + tidy.slice(1);
}

/** The category label of a row: its dimension values joined, e.g. "5411 · Grocery". */
export function categoryOf(row: Record<string, unknown>, dimensions: string[]): string {
  return dimensions.map((d) => String(row[d] ?? "—")).join(" · ");
}
