import { useEffect, useRef, useState } from "react";

export type Mode = "question" | "dsl";

interface Props {
  busy: boolean;
  onSend: (text: string, mode: Mode) => void;
  /** Text to drop into the box, e.g. an unknown value the user should correct. */
  draft?: { text: string; mode: Mode; nonce: number } | null;
  autoFocus?: boolean;
}

/** The input. DSL mode sends straight to the compiler, skipping the model. */
export function Composer({ busy, onSend, draft, autoFocus }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("question");
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!draft) return;
    setText(draft.text);
    setMode(draft.mode);
    box.current?.focus();
  }, [draft?.nonce]);

  useEffect(() => {
    if (autoFocus) box.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    const focusOnShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        box.current?.focus();
      }
    };
    window.addEventListener("keydown", focusOnShortcut);
    return () => window.removeEventListener("keydown", focusOnShortcut);
  }, []);

  const grow = (element: HTMLTextAreaElement) => {
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  };

  const send = () => {
    const value = text.trim();
    if (!value || busy) return;
    onSend(value, mode);
    setText("");
    if (box.current) box.current.style.height = "auto";
  };

  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="segmented tiny" role="group" aria-label="input mode">
          <button
            className={mode === "question" ? "active" : ""}
            onClick={() => setMode("question")}
            aria-pressed={mode === "question"}
          >
            Ask
          </button>
          <button
            className={mode === "dsl" ? "active" : ""}
            onClick={() => setMode("dsl")}
            aria-pressed={mode === "dsl"}
          >
            DSL
          </button>
        </div>
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={mode === "dsl" ? "DSL query" : "Question"}
          placeholder={mode === "dsl" ? "SHOW value BY issuer" : "Ask a question"}
          className={mode === "dsl" ? "mono" : ""}
          onChange={(e) => {
            setText(e.target.value);
            grow(e.target);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="primary" onClick={send} disabled={busy || !text.trim()}>
          Send
        </button>
      </div>
      <p className="composer-hint">
        Enter to send · Shift+Enter for a new line · <kbd>Ctrl</kbd>+<kbd>K</kbd> to focus
      </p>
    </div>
  );
}
