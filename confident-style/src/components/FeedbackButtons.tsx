"use client";

import { useState } from "react";
import type { RecommendationKind } from "@/lib/schema";

interface Props {
  sessionId: string;
  kind: RecommendationKind;
  title: string;
}

export function FeedbackButtons({ sessionId, kind, title }: Props) {
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(next: "up" | "down") {
    if (busy || vote) return;
    setBusy(true);
    setVote(next);
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, kind, title, vote: next }),
      });
    } catch {
      /* feedback is best-effort */
    } finally {
      setBusy(false);
    }
  }

  const btn = (which: "up" | "down", glyph: string, label: string) => (
    <button
      type="button"
      aria-label={label}
      aria-pressed={vote === which}
      disabled={busy || (vote !== null && vote !== which)}
      onClick={() => send(which)}
      className={`flex h-10 w-10 items-center justify-center rounded-full text-lg ring-1 transition ${
        vote === which
          ? "bg-accent text-white ring-accent"
          : "bg-white text-ink ring-line hover:bg-blush disabled:opacity-40"
      }`}
    >
      {glyph}
    </button>
  );

  return (
    <div className="flex items-center gap-2">
      <span className="mr-1 text-xs text-muted">{vote ? "Thanks!" : "Helpful?"}</span>
      {btn("up", "👍", "This suits me")}
      {btn("down", "👎", "Not for me")}
    </div>
  );
}
