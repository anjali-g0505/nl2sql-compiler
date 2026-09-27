import type { ReactNode } from "react";
import type { AnswerResponse } from "../api";
import { highlight } from "../syntax";
import { CopyButton } from "./CopyButton";

/** Prose: an answer from the documentation, or an explanation of a result.
 *
 * Citations are the point of this card. The model is told to cite inline, as
 * `[kb-09 > Mixed-outcome tables]` at the end of the sentence it supports, so those
 * markers are lifted out of the prose into numbered references and the sources are
 * listed underneath — the same bargain as the DSL and SQL on a result card, where
 * every claim can be traced to what produced it.
 *
 * A marker naming a source the backend didn't report is left in the text verbatim
 * rather than dropped: an uncheckable citation should look wrong, not disappear.
 */
export function AnswerCard({ answer }: { answer: AnswerResponse }) {
  // citations arrive in the order the model used them, which is the numbering
  const numberOf = new Map(answer.citations.map((citation, i) => [citation, i + 1]));
  const explained = answer.explains?.dsl;

  return (
    <article className={`card answer ${answer.grounded ? "" : "ungrounded"}`}>
      <header className="answer-head">
        <span className="answer-kind">
          {explained ? "Explanation" : answer.grounded ? "From the documentation" : "Not documented"}
        </span>
        <CopyButton label="Copy" value={() => answer.text} />
      </header>

      {explained && (
        <pre className="code-inline answer-explains">
          <code>{highlight(explained, "dsl")}</code>
        </pre>
      )}

      <div className="answer-body">
        {toBlocks(answer.text).map((block, i) =>
          block.kind === "ul" ? (
            <ul key={i}>
              {block.lines.map((line, j) => (
                <li key={j}>{inlineCitations(line, numberOf)}</li>
              ))}
            </ul>
          ) : (
            <p key={i}>{inlineCitations(block.lines[0], numberOf)}</p>
          ),
        )}
      </div>

      {answer.citations.length > 0 && (
        <ol className="sources">
          {answer.citations.map((citation, i) => {
            const [doc, heading] = splitCitation(citation);
            return (
              <li key={citation}>
                <span className="cite">{i + 1}</span>
                <span className="source-doc">{doc}</span>
                {heading && <span className="source-heading">{heading}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </article>
  );
}

/** "kb-09 > Mixed-outcome tables" -> ["kb-09", "Mixed-outcome tables"] */
function splitCitation(citation: string): [string, string] {
  const at = citation.indexOf(">");
  return at === -1
    ? [citation.trim(), ""]
    : [citation.slice(0, at).trim(), citation.slice(at + 1).trim()];
}

type Block = { kind: "p" | "ul"; lines: string[] };

// "- ", "* ", "• " or "1. " — the shapes a model reaches for when asked for a few points
const BULLET = /^(?:[-*•]|\d+[.)])\s+/;

/** The answers are short by instruction — a few sentences, sometimes a few points — so
 *  this handles exactly that: one paragraph per line, with runs of bullets grouped into
 *  a list. It is deliberately not a Markdown parser. */
function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const last = blocks[blocks.length - 1];
    if (BULLET.test(line)) {
      const item = line.replace(BULLET, "");
      if (last?.kind === "ul") last.lines.push(item);
      else blocks.push({ kind: "ul", lines: [item] });
    } else {
      blocks.push({ kind: "p", lines: [line] });
    }
  }
  return blocks;
}

/** Replaces `[kb-09 > Heading]` with a superscript reference to the numbered source. */
function inlineCitations(text: string, numberOf: Map<string, number>): ReactNode[] {
  const out: ReactNode[] = [];
  const marker = /\[([^\]]+)\]/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = marker.exec(text)) !== null) {
    const number = numberOf.get(match[1].trim());
    if (number === undefined) continue;  // not a source we can link: leave it in the prose
    out.push(text.slice(cursor, match.index));
    out.push(
      <sup key={match.index} className="cite" title={match[1].trim()}>
        {number}
      </sup>,
    );
    cursor = match.index + match[0].length;
  }
  out.push(text.slice(cursor));
  return out;
}
