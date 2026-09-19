"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/Button";
import { PhotoPicker } from "@/components/PhotoPicker";
import { Shell } from "@/components/Shell";
import { prepareForUpload } from "@/lib/image-utils";
import { StyleAnalysisSchema } from "@/lib/schema";
import { useStyleSession } from "@/lib/session-context";

const ANALYSING_LINES = [
  "Looking at your proportions…",
  "Reading your colouring…",
  "Choosing outfits that celebrate you…",
  "Picking hairstyles for your face shape…",
];

export default function UploadPage() {
  const router = useRouter();
  const { photos, setPhoto, setAnalysis } = useStyleSession();
  const [status, setStatus] = useState<"idle" | "preparing" | "analysing">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lineIndex, setLineIndex] = useState(0);

  const busy = status !== "idle";

  async function pick(slot: "body" | "face", file: File | null) {
    setError(null);
    if (!file) {
      setPhoto(slot, null);
      return;
    }
    try {
      setStatus("preparing");
      const blob = await prepareForUpload(file);
      setPhoto(slot, blob);
    } catch (err) {
      setError(err instanceof Error ? err.message : "We couldn't open that photo.");
    } finally {
      setStatus("idle");
    }
  }

  async function analyse() {
    if (!photos.body || !photos.face) return;
    setError(null);
    setStatus("analysing");
    const ticker = setInterval(() => setLineIndex((i) => (i + 1) % ANALYSING_LINES.length), 2800);
    try {
      const form = new FormData();
      form.append("body", photos.body, "body.jpg");
      form.append("face", photos.face, "face.jpg");
      const res = await fetch("/api/analyse", { method: "POST", body: form });
      const json = (await res.json().catch(() => ({}))) as { analysis?: unknown; error?: string };
      if (!res.ok) throw new Error(json.error || "Something went wrong. Please try again.");
      const analysis = StyleAnalysisSchema.parse(json.analysis);
      setAnalysis(analysis);
      router.push("/results");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setStatus("idle");
    } finally {
      clearInterval(ticker);
    }
  }

  return (
    <Shell step={{ current: 1, total: 2, label: "Photos" }}>
      <h1 className="font-serif text-3xl leading-tight text-ink">Two photos, that&apos;s all.</h1>
      <p className="mt-3 text-[15px] leading-relaxed text-ink/80">
        Natural daylight near a window is ideal. Stand relaxed with your arms by your sides,
        wearing something fairly fitted so your natural shape is easy to see. No need to pose or
        smile unless you want to.
      </p>

      <div className="mt-6 grid gap-4">
        <PhotoPicker
          label="Full-body photo"
          hint="Head to toe, facing the camera, phone held at about chest height."
          value={photos.body}
          onChange={(f) => pick("body", f)}
          disabled={busy}
        />
        <PhotoPicker
          label="Face and hair photo"
          hint="Shoulders up, hair as you usually wear it, face towards the light."
          value={photos.face}
          onChange={(f) => pick("face", f)}
          disabled={busy}
        />
      </div>

      {error && (
        <p role="alert" className="mt-4 rounded-2xl bg-white px-4 py-3 text-sm text-accent-deep ring-1 ring-line">
          {error}
        </p>
      )}

      <div className="mt-6">
        <Button
          className="w-full"
          disabled={!photos.body || !photos.face || busy}
          onClick={analyse}
        >
          {status === "analysing" ? "Analysing…" : "Show me what suits me"}
        </Button>
        <p className="mt-3 text-center text-xs leading-relaxed text-muted">
          Both photos are sent once for analysis and removed as soon as it&apos;s done. They are
          never saved.
        </p>
      </div>

      {status === "analysing" && (
        <div
          className="fixed inset-0 z-40 flex flex-col items-center justify-center bg-cream/95 px-8 text-center"
          aria-live="polite"
        >
          <div className="h-12 w-12 animate-spin rounded-full border-4 border-line border-t-accent" />
          <p className="mt-6 font-serif text-2xl text-ink">{ANALYSING_LINES[lineIndex]}</p>
          <p className="mt-2 text-sm text-muted">This usually takes under a minute.</p>
        </div>
      )}
    </Shell>
  );
}
