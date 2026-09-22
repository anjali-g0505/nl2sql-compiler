import { useEffect, useRef, useState } from "react";

interface Props {
  label: string;
  /** Text to copy, or a function producing it (and optionally a PNG blob for charts). */
  value: () => string | Promise<string>;
  blob?: () => Promise<Blob | null>;  // when set, an image is copied instead of text
  filename?: string;                  // used if the browser refuses an image copy
}

type State = "idle" | "done" | "saved" | "failed";

const MESSAGES: Record<State, string> = {
  idle: "",
  done: "Copied",
  saved: "Downloaded",
  failed: "Press Ctrl+C",
};

/** Copy-to-clipboard button. Falls back to a download when an image can't be copied. */
export function CopyButton({ label, value, blob, filename }: Props) {
  const [state, setState] = useState<State>("idle");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const flash = (next: State) => {
    setState(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1800);
  };

  const copy = async () => {
    try {
      if (blob) {
        const image = await blob();
        if (!image) throw new Error("nothing to copy");
        try {
          await navigator.clipboard.write([new ClipboardItem({ "image/png": image })]);
          flash("done");
        } catch {
          download(image, filename ?? "chart.png");  // Firefox, and any insecure context
          flash("saved");
        }
        return;
      }
      flash((await copyText(await value())) ? "done" : "failed");
    } catch {
      flash("failed");
    }
  };

  return (
    <button className={`copy ${state !== "idle" ? "flash" : ""}`} onClick={copy} title={`Copy ${label}`}>
      {state === "idle" ? label : MESSAGES[state]}
    </button>
  );
}

/** navigator.clipboard needs a secure context and a focused document; this also works
 *  over plain http on a LAN address, and when the page has just lost focus. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } finally {
      area.remove();
    }
  }
}

function download(image: Blob, filename: string) {
  const url = URL.createObjectURL(image);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
