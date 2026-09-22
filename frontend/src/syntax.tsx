// Syntax highlighting for the DSL and the generated SQL, using the same token colours
// as the chart series: a metric that is cyan in the DSL is cyan in the chart.

import type { ReactNode } from "react";

const DSL_KEYWORDS = new Set([
  "SHOW", "BY", "WHERE", "AND", "NOT", "IN", "PERIOD", "HAVING", "ORDER", "LIMIT",
  "AS", "ASC", "DESC", "LAST", "DAYS", "MONTHS", "FROM", "TO",
  "FTD", "WTD", "MTD", "QTD", "YTD", "CRORE", "LAKH",
  "TABLE", "KPI", "BAR", "LINE", "PIE",
]);

const SQL_KEYWORDS = new Set([
  "SELECT", "FROM", "JOIN", "LEFT", "INNER", "ON", "WHERE", "GROUP", "BY", "ORDER",
  "HAVING", "LIMIT", "AS", "AND", "OR", "NOT", "IN", "CASE", "WHEN", "THEN", "ELSE",
  "END", "SUM", "COUNT", "DISTINCT", "NULLIF", "SUBSTRING", "ASC", "DESC",
]);

export interface Highlight {
  /** metric name -> the series colour it is drawn with, so code and chart agree */
  metricColors?: Record<string, string>;
  /** 0-based offset of a token to mark as the error */
  errorAt?: number | null;
}

/** Split into spans: words, quoted strings, numbers, whitespace, everything else. */
const TOKENS = /('[^']*'|"[^"]*"|[A-Za-z_][A-Za-z0-9_.]*|\d+(?:\.\d+)?|\s+|.)/g;

export function highlight(code: string, language: "dsl" | "sql", options: Highlight = {}): ReactNode[] {
  const keywords = language === "dsl" ? DSL_KEYWORDS : SQL_KEYWORDS;
  const metricColors = options.metricColors ?? {};
  const nodes: ReactNode[] = [];
  let index = 0;

  for (const [token] of code.matchAll(TOKENS)) {
    const start = index;
    index += token.length;
    if (/^\s+$/.test(token)) {
      nodes.push(token);
      continue;
    }

    const errored = options.errorAt != null && start <= options.errorAt && options.errorAt < index;
    const className = ["tok", tokenClass(token, keywords, metricColors), errored ? "tok-error" : ""]
      .filter(Boolean)
      .join(" ");
    const colour = metricColors[bareName(token)];
    nodes.push(
      <span key={start} className={className} style={colour ? { color: colour } : undefined}>
        {token}
      </span>,
    );
  }
  return nodes;
}

function bareName(token: string): string {
  return token.includes(".") ? token.slice(token.lastIndexOf(".") + 1) : token;
}

function tokenClass(token: string, keywords: Set<string>, metricColors: Record<string, string>): string {
  if (/^['"]/.test(token)) return "tok-literal";
  if (/^\d/.test(token)) return "tok-literal";
  if (keywords.has(token.toUpperCase())) return "tok-keyword";
  if (metricColors[bareName(token)]) return "tok-metric";
  if (/^[A-Za-z_]/.test(token)) return "tok-name";
  return "tok-punct";
}

/** "…got 'YEARS' at position 25" -> 25, so the bad token can be underlined. */
export function errorPosition(messages: string[]): number | null {
  for (const message of messages) {
    const match = /at position (\d+)/.exec(message);
    if (match) return Number(match[1]);
  }
  return null;
}
