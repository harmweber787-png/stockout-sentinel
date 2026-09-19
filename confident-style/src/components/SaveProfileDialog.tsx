"use client";

import { useState, type FormEvent } from "react";
import type { SavedProfile } from "@/lib/schema";
import { Button } from "./Button";

interface Props {
  profile: SavedProfile;
  onClose: () => void;
}

export function SaveProfileDialog({ profile, onClose }: Props) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setState("saving");
    setMessage(null);
    try {
      const res = await fetch("/api/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, profile }),
      });
      const json = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) throw new Error(json.error || "We couldn't save your profile just now.");
      setState("saved");
    } catch (err) {
      setState("error");
      setMessage(err instanceof Error ? err.message : "We couldn't save your profile just now.");
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="save-title"
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-4 sm:items-center"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-3xl bg-white p-6 shadow-soft"
        onClick={(e) => e.stopPropagation()}
      >
        {state === "saved" ? (
          <>
            <h2 id="save-title" className="font-serif text-2xl text-ink">
              Saved
            </h2>
            <p className="mt-2 text-[15px] leading-relaxed text-ink/80">
              Your style profile is stored as text against your email address. Your photos were
              never kept.
            </p>
            <Button className="mt-6 w-full" onClick={onClose}>
              Done
            </Button>
          </>
        ) : (
          <form onSubmit={submit}>
            <h2 id="save-title" className="font-serif text-2xl text-ink">
              Save my style profile
            </h2>
            <p className="mt-2 text-[15px] leading-relaxed text-ink/80">
              We&apos;ll keep the written advice (your shape, colour palette and the six
              recommendations) so you can come back to it. Photos and previews are not stored.
            </p>
            <label htmlFor="email" className="mt-5 block text-sm font-medium text-ink">
              Email address
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-2xl border border-line bg-cream px-4 py-3 text-base text-ink outline-none focus:border-accent"
              placeholder="you@example.com"
            />
            {message && <p className="mt-2 text-sm text-accent-deep">{message}</p>}
            <div className="mt-5 flex gap-3">
              <Button type="button" variant="secondary" className="flex-1" onClick={onClose}>
                Not now
              </Button>
              <Button type="submit" className="flex-1" disabled={state === "saving"}>
                {state === "saving" ? "Saving…" : "Save"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
