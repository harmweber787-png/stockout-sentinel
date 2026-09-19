import Link from "next/link";
import type { ReactNode } from "react";

/** Mobile-first page frame: centred column, comfortable gutters, soft header. */
export function Shell({
  children,
  step,
}: {
  children: ReactNode;
  step?: { current: number; total: number; label: string };
}) {
  return (
    <div className="flex min-h-full flex-col">
      <header className="mx-auto flex w-full max-w-xl items-center justify-between px-5 pt-6">
        <Link href="/" className="font-serif text-lg tracking-tight text-ink">
          Confident <span className="text-accent-deep">Style</span>
        </Link>
        {step && (
          <span className="text-xs uppercase tracking-widest text-muted">
            {step.label} · {step.current}/{step.total}
          </span>
        )}
      </header>
      <main className="mx-auto w-full max-w-xl flex-1 px-5 pb-16 pt-6">{children}</main>
      <footer className="mx-auto w-full max-w-xl px-5 pb-8 text-center text-xs text-muted">
        Your photos are analysed once and deleted straight away. Nothing is kept unless you
        choose to save your style profile.
      </footer>
    </div>
  );
}
