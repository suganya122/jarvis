import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  id: string;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Extra controls in the page header (e.g. Refresh). */
  actions?: ReactNode;
  wide?: boolean;
}

/** A view in the main area (Skills, Memory, Settings…). Escape or Close returns to the chat. */
export function Page({ id, title, onClose, children, actions, wide }: Props) {
  const ref = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("h2")?.focus(); // screen readers land on the new view
    // On the document, not the section: after Save disables its button, focus falls to <body>.
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as Node;
      const here = target === document.body || ref.current?.contains(target);
      if (e.key === "Escape" && !e.defaultPrevented && here && !(target instanceof HTMLSelectElement)) {
        e.preventDefault();
        close.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <section
      ref={ref}
      className={`page${wide ? " page-wide" : ""}`}
      aria-labelledby={id}
    >
      <header className="page-head">
        <h2 id={id} tabIndex={-1}>
          {title}
        </h2>
        <span className="spacer" />
        {actions}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      {children}
    </section>
  );
}
