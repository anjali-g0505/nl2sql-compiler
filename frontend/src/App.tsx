import { useEffect, useRef, useState } from "react";
import { answerClarification, ask, type ApiResponse } from "./api";
import { Composer, type Mode } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { PipelineProgress } from "./components/PipelineStrip";
import { ResultCard } from "./components/ResultCard";
import { Clarification, ErrorCard } from "./components/Resolution";
import { Wordmark } from "./components/Wordmark";

type Turn =
  | { id: number; role: "question"; text: string; mode: Mode }
  | { id: number; role: "answer"; response: ApiResponse; answered?: boolean };

const EXAMPLES = [
  "Success rate by issuer this quarter",
  "Top 10 merchants by value this month",
  "Decline reasons for credit cards in May",
];

let nextId = 1;

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{ text: string; mode: Mode; nonce: number } | null>(null);
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    foot.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, busy]);

  const add = (turn: Turn) => setTurns((all) => [...all, turn]);

  const run = async (request: () => Promise<ApiResponse>) => {
    setBusy(true);
    const response = await request();
    add({ id: nextId++, role: "answer", response });
    setBusy(false);
  };

  const send = (text: string, mode: Mode) => {
    add({ id: nextId++, role: "question", text, mode });
    run(() => ask(mode === "dsl" ? { dsl: text } : { question: text }));
  };

  const answer = (turnId: number, clarificationId: string, answers: Record<string, string>) => {
    setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, answered: true } : t)));
    run(() => answerClarification(clarificationId, answers));
  };

  const started = turns.length > 0 || busy;

  return (
    <div className={`app ${started ? "started" : "blank"}`}>
      {started && (
        <header className="top">
          <Wordmark />
        </header>
      )}

      <main>
        {!started && (
          <EmptyState examples={EXAMPLES} onPick={(question) => send(question, "question")}>
            <Composer busy={busy} onSend={send} draft={draft} autoFocus />
          </EmptyState>
        )}

        {turns.map((turn) =>
          turn.role === "question" ? (
            <h2 key={turn.id} className={`turn-question ${turn.mode === "dsl" ? "mono" : ""}`}>
              <span className="caret" aria-hidden>›</span>
              {turn.text}
            </h2>
          ) : (
            <div key={turn.id} className="turn-answer">
              <ErrorBoundary>
                {turn.response.status === "ok" && <ResultCard result={turn.response} />}
                {turn.response.status === "error" && (
                  <ErrorCard
                    error={turn.response}
                    onEdit={(text) => setDraft({ text, mode: "dsl", nonce: Date.now() })}
                  />
                )}
                {turn.response.status === "needs_clarification" && (
                  <Clarification
                    clarification={turn.response}
                    answered={!!turn.answered}
                    onSubmit={(answers) =>
                      turn.response.status === "needs_clarification" &&
                      answer(turn.id, turn.response.clarification_id, answers)
                    }
                  />
                )}
              </ErrorBoundary>
            </div>
          ),
        )}

        {busy && (
          <div className="turn-answer working">
            <PipelineProgress />
          </div>
        )}
        <div ref={foot} />
      </main>

      {started && <Composer busy={busy} onSend={send} draft={draft} />}
    </div>
  );
}
