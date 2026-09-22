import { Wordmark } from "./Wordmark";

/** Monthly transaction volume from the sample dataset, drawn very faintly behind the
 *  empty state: the product's own material rather than decoration. Static on purpose —
 *  the page shouldn't issue a query before the user asks one. */
const SERIES = [201, 213, 226, 234, 241, 250, 258, 269, 277, 288, 297, 309];

export function Backdrop() {
  const width = 1200;
  const height = 360;
  const step = width / (SERIES.length - 1);
  const low = Math.min(...SERIES);
  const high = Math.max(...SERIES);
  const y = (value: number) => height - ((value - low) / (high - low)) * (height * 0.7) - 30;
  const line = SERIES.map((value, i) => `${i === 0 ? "M" : "L"}${i * step},${y(value)}`).join(" ");

  return (
    <svg className="backdrop" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <path d={`${line} L${width},${height} L0,${height} Z`} className="backdrop-fill" />
      <path d={line} className="backdrop-line" />
      {SERIES.map((value, i) => (
        <rect key={i} x={i * step - 14} y={y(value)} width={28} height={height - y(value)} className="backdrop-bar" />
      ))}
    </svg>
  );
}

interface EmptyProps {
  examples: string[];
  onPick: (question: string) => void;
  children: React.ReactNode;  // the composer
}

export function EmptyState({ examples, onPick, children }: EmptyProps) {
  return (
    <div className="empty">
      <Backdrop />
      <div className="empty-inner">
        <Wordmark size="hero" />
        <p className="positioning">
          Ask a question about card payments. IntentQL compiles it to a DSL, then to SQL you
          can read before you trust the number.
        </p>
        {children}
        <div className="chips examples">
          {examples.map((question) => (
            <button key={question} className="chip" onClick={() => onPick(question)}>
              {question}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
