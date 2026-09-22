import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useEffect, useState } from "react";
import type { OkResponse } from "../api";
import { categoryOf, columnLabel, formatValue } from "../format";
import { seriesColors, token } from "../theme";

const MAX_TICKS = 8;

/** BAR / LINE / PIE, drawn as the backend chose, in the series colours the DSL uses. */
export function ChartView({ result, colors }: { result: OkResponse; colors: Record<string, string> }) {
  const metrics = result.metric_columns;
  const data = result.rows.map((row) => ({
    ...row,
    __category: categoryOf(row, result.dimension_columns),
  }));
  const ink600 = token("--ink-600");
  const ink400 = token("--ink-400");
  const ink200 = token("--ink-200");
  const narrow = useNarrow();
  const maxTicks = narrow ? 4 : MAX_TICKS;
  const interval = Math.max(0, Math.ceil(data.length / maxTicks) - 1);
  const cut = narrow ? 9 : 14;
  const axis = { tick: { fill: ink400, fontSize: 12, fontFamily: "JetBrains Mono, monospace" } };

  if (result.chart_type === "PIE") {
    const metric = metrics[0];
    return (
      <ResponsiveContainer width="100%" height={300}>
        <PieChart>
          <Pie data={data} dataKey={metric} nameKey="__category" outerRadius={104} stroke={token("--ink-800")}>
            {data.map((_, i) => (
              <Cell key={i} fill={paletteAt(i)} />
            ))}
          </Pie>
          <Legend formatter={(value) => <span style={{ color: ink200 }}>{value}</span>} />
          <Tooltip content={<Readout result={result} />} />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  const isLine = result.chart_type === "LINE";
  const Chart = isLine ? LineChart : BarChart;
  return (
    <ResponsiveContainer width="100%" height={272}>
      <Chart data={data} margin={{ top: 6, right: 8, bottom: 4, left: 4 }}>
        {/* horizontals only, no verticals, no axis domain line */}
        <CartesianGrid stroke={ink600} strokeDasharray="0" vertical={false} />
        <XAxis
          dataKey="__category"
          interval={interval}
          tickLine={false}
          axisLine={false}
          height={26}
          tickFormatter={(value: string) => shorten(value, cut)}
          {...axis}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={narrow ? 56 : 74}
          domain={isLine ? yDomain(data, metrics) : [0, "auto"]}
          tickFormatter={(value: number) => formatValue(value, metrics[0])}
          label={{
            value: unitLabel(result),
            angle: -90,
            position: "insideLeft",
            style: { fill: ink400, fontSize: 12, textAnchor: "middle" },
          }}
          {...axis}
        />
        <Tooltip
          cursor={{ stroke: ink400, strokeWidth: 1 }}
          content={<Readout result={result} />}
        />
        {metrics.length > 1 && (
          <Legend formatter={(value) => <span style={{ color: ink200 }}>{value}</span>} />
        )}
        {metrics.map((metric) =>
          isLine ? (
            <Line
              key={metric}
              type="monotone"
              dataKey={metric}
              name={columnLabel(metric, result)}
              stroke={colors[metric]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          ) : (
            <Bar
              key={metric}
              dataKey={metric}
              name={columnLabel(metric, result)}
              fill={colors[metric]}
              radius={[3, 3, 0, 0]}
              maxBarSize={56}
            />
          ),
        )}
      </Chart>
    </ResponsiveContainer>
  );
}

/** Lines clamp to the data range (an 85-95% series shouldn't read as flat against 0-100).
 *  Bars keep their zero baseline, because a truncated bar misstates the comparison. */
function yDomain(data: Record<string, unknown>[], metrics: string[]): [number, number] {
  const values = data.flatMap((row) => metrics.map((m) => Number(row[m]))).filter(Number.isFinite);
  if (!values.length) return [0, 1];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const pad = (high - low || Math.abs(high) || 1) * 0.15;
  return [low - pad, high + pad];
}

function unitLabel(result: OkResponse): string {
  const label = columnLabel(result.metric_columns[0], result);
  return result.metric_columns.length > 1 ? "" : label;
}

function shorten(value: string, cut: number): string {
  return value.length > cut ? `${value.slice(0, cut - 1)}…` : value;
}

/** Recharts needs the count up front, so label density is decided by viewport width. */
function useNarrow(): boolean {
  const query = "(max-width: 640px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return narrow;
}

function paletteAt(index: number): string {
  const palette = seriesColors();
  return palette[index % palette.length];
}

/** One row per hovered point: label, then value. */
function Readout({ result, active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="readout">
      <span className="readout-label">{payload[0]?.payload?.__category ?? label}</span>
      {payload.map((entry: any) => (
        <span key={entry.dataKey} className="readout-value">
          <span className="swatch" style={{ background: entry.color }} aria-hidden />
          {columnLabel(entry.dataKey, result)}: <b>{formatValue(entry.value, entry.dataKey)}</b>
        </span>
      ))}
    </div>
  );
}
