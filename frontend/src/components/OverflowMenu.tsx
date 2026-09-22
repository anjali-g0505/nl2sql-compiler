import { useEffect, useRef, useState } from "react";

export interface MenuItem {
  label: string;
  run: () => void | Promise<void>;
  disabled?: boolean;
}

/** One "more actions" button instead of a row of competing buttons.
 *  An item reports back inline ("Copied") rather than firing a toast. */
export function OverflowMenu({ items }: { items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const run = async (item: MenuItem) => {
    await item.run();
    setDone(item.label);
    window.setTimeout(() => setDone(null), 1500);
    setOpen(false);
  };

  return (
    <div className="menu-wrap" ref={wrapper}>
      {done && <span className="menu-done" role="status">{done.startsWith("Download") ? "Saved" : "Copied"}</span>}
      <button
        className="icon-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
        onClick={() => setOpen(!open)}
      >
        ⋯
      </button>
      {open && (
        <div className="menu" role="menu">
          {items.map((item) => (
            <button key={item.label} role="menuitem" disabled={item.disabled} onClick={() => run(item)}>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
