/** The wordmark. The only mark is the cyan caret, which is also the composer's glyph. */
export function Wordmark({ size = "header" }: { size?: "header" | "hero" }) {
  return (
    <span className={`wordmark ${size}`}>
      <span className="caret" aria-hidden>›</span>
      <span>
        Intent<span className="ql">QL</span>
      </span>
    </span>
  );
}
