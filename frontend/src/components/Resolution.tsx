import { useState } from "react";
import type { ClarificationQuestion, ClarificationResponse, ErrorResponse } from "../api";
import { highlight, errorPosition } from "../syntax";

/** AMBIGUOUS: candidates to choose from. UNKNOWN: what didn't match, and what exists.
 *  A single question resolves on the chip itself; several are answered together. */
export function Clarification({
  clarification,
  answered,
  onSubmit,
}: {
  clarification: ClarificationResponse;
  answered: boolean;
  onSubmit: (answers: Record<string, string>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const single = clarification.questions.length === 1;
  const complete = clarification.questions.every((q) => (answers[q.id] ?? "").trim());
  const set = (id: string, value: string) => setAnswers({ ...answers, [id]: value });

  const choose = (question: ClarificationQuestion, value: string) => {
    if (answered) return;
    const next = { ...answers, [question.id]: value };
    setAnswers(next);
    if (single) onSubmit(next); // one question: picking a chip runs it
  };

  return (
    <article className={`card resolution ${clarification.questions[0]?.reason ?? ""}`}>
      {clarification.questions.map((question) => (
        <div key={question.id} className="question">
          <p className="resolution-head">{question.message}</p>
          {question.options.length > 0 ? (
            <div className="chips">
              {question.options.map((option) => (
                <button
                  key={option.id}
                  className={`chip ${answers[question.id] === option.id ? "on" : ""}`}
                  disabled={answered}
                  onClick={() => choose(question, option.id)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          ) : (
            <p className="resolution-note">
              Nothing in the data matches that value. Type the exact name, or edit the question.
            </p>
          )}
          <input
            type="text"
            placeholder={question.options.length ? "…or type another value" : "Type the exact name"}
            disabled={answered}
            value={question.options.some((o) => o.id === answers[question.id]) ? "" : answers[question.id] ?? ""}
            onChange={(e) => set(question.id, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (answers[question.id] ?? "").trim() && !answered) {
                onSubmit({ ...answers });
              }
            }}
          />
        </div>
      ))}
      <p className="resolution-foot">
        IntentQL needs one match before it can run, so the number it gives you is the one you meant.
      </p>
      {answered ? (
        <p className="resolution-note">Answered.</p>
      ) : (
        !single && (
          <button className="primary" disabled={!complete} onClick={() => onSubmit(answers)}>
            Run
          </button>
        )
      )}
    </article>
  );
}

const HEADLINES: Record<string, string> = {
  scope: "This data can't answer that",
  translate: "The model couldn't be reached",
  network: "The server didn't respond",
  parse: "The query didn't parse",
  validate: "That isn't in this dataset",
  resolve: "That value isn't in the data",
  clarification: "That clarification expired",
  compile: "The query couldn't be prepared",
  request: "Nothing to run",
};

export function ErrorCard({ error, onEdit }: { error: ErrorResponse; onEdit: (text: string) => void }) {
  const position = errorPosition(error.errors);
  return (
    <article className="card failure">
      <p className="failure-head">{HEADLINES[error.stage] ?? "That didn't run"}</p>
      <ul>
        {error.errors.map((message, i) => (
          <li key={i}>{message}</li>
        ))}
      </ul>
      {error.dsl && (
        <pre className="code-inline">
          <code>{highlight(error.dsl, "dsl", { errorAt: position })}</code>
        </pre>
      )}
      {error.dsl && (
        <button className="ghost" onClick={() => onEdit(error.dsl as string)}>
          Edit the DSL
        </button>
      )}
    </article>
  );
}
