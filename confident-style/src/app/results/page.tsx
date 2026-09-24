"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useHydrated } from "@/lib/session-store";
import { Button, LinkButton } from "@/components/Button";
import { RecommendationCard, type PreviewState } from "@/components/RecommendationCard";
import { SaveProfileDialog } from "@/components/SaveProfileDialog";
import { Shell } from "@/components/Shell";
import { toSavedProfile, type Recommendation, type RecommendationKind } from "@/lib/schema";
import { useStyleSession } from "@/lib/session-context";

const NO_PHOTO_NOTE =
  "Previews are made from your photo, which isn't kept. Start again to see them.";

function previewKey(kind: RecommendationKind, index: number) {
  return `${kind}-${index}`;
}

export default function ResultsPage() {
  const router = useRouter();
  const { analysis, photos, sessionId, reset } = useStyleSession();
  const [previews, setPreviews] = useState<Record<string, PreviewState>>({});
  const [showSave, setShowSave] = useState(false);
  const hydrated = useHydrated();
  const started = useRef(false);

  // Send the user home if there is nothing to show (e.g. deep link with no analysis).
  useEffect(() => {
    if (hydrated && !analysis) {
      const timer = setTimeout(() => {
        if (!analysis) router.replace("/");
      }, 400);
      return () => clearTimeout(timer);
    }
  }, [hydrated, analysis, router]);

  // Generate the six previews in parallel, once.
  useEffect(() => {
    if (!analysis || started.current) return;
    started.current = true;

    const jobs: Array<{ kind: RecommendationKind; index: number; rec: Recommendation; photo: Blob | null }> = [
      ...analysis.clothing.map((rec, index) => ({ kind: "clothing" as const, index, rec, photo: photos.body })),
      ...analysis.hairstyles.map((rec, index) => ({ kind: "hairstyle" as const, index, rec, photo: photos.face })),
    ];

    for (const job of jobs) {
      if (!job.photo) continue;
      const key = previewKey(job.kind, job.index);
      const form = new FormData();
      form.append("photo", job.photo, "photo.jpg");
      form.append("kind", job.kind);
      form.append("title", job.rec.title);
      form.append("image_prompt", job.rec.image_prompt);
      fetch("/api/visualise", { method: "POST", body: form })
        .then(async (res) => {
          const json = (await res.json().catch(() => ({}))) as {
            image?: string;
            illustrative?: boolean;
            error?: string;
          };
          if (!res.ok || !json.image) {
            throw new Error(json.error || "The preview could not be created.");
          }
          setPreviews((p) => ({
            ...p,
            [key]: { status: "ready", image: json.image!, illustrative: Boolean(json.illustrative) },
          }));
        })
        .catch(() => {
          setPreviews((p) => ({
            ...p,
            [key]: {
              status: "unavailable",
              note: "This preview didn't come through, but the advice above still stands.",
            },
          }));
        });
    }
  }, [analysis, photos.body, photos.face]);

  if (!analysis) {
    return (
      <Shell>
        <p className="text-center text-muted">Loading your results…</p>
      </Shell>
    );
  }

  // Until a preview arrives, a card is "loading" if we hold the photo and "unavailable" if not.
  const previewFor = (kind: RecommendationKind, index: number, photo: Blob | null): PreviewState =>
    previews[previewKey(kind, index)] ??
    (photo ? { status: "loading" } : { status: "unavailable", note: NO_PHOTO_NOTE });

  const facts: Array<[string, string]> = [
    ["Body shape", analysis.body_shape],
    ["Colour palette", analysis.colour_type],
    ["Face shape", analysis.face_shape],
    ["Hair", analysis.hair],
  ];

  return (
    <Shell step={{ current: 2, total: 2, label: "Your style" }}>
      <p className="text-xs uppercase tracking-widest text-accent-deep">Your results</p>
      <h1 className="mt-2 font-serif text-3xl leading-tight text-ink">Here&apos;s what suits you.</h1>
      <blockquote className="mt-4 rounded-3xl bg-blush p-5 font-serif text-lg leading-relaxed text-ink">
        “{analysis.confidence_note}”
      </blockquote>

      <dl className="mt-5 grid grid-cols-2 gap-3">
        {facts.map(([label, value]) => (
          <div key={label} className="rounded-2xl bg-white p-4 ring-1 ring-line">
            <dt className="text-xs uppercase tracking-wider text-muted">{label}</dt>
            <dd className="mt-1 text-[15px] font-medium leading-snug text-ink">{value}</dd>
          </div>
        ))}
      </dl>

      <section className="mt-10">
        <h2 className="font-serif text-2xl text-ink">Clothing</h2>
        <p className="mt-1 text-sm text-muted">Three outfits built around your shape and colouring.</p>
        <div className="mt-4 space-y-5">
          {analysis.clothing.map((rec, index) => (
            <RecommendationCard
              key={previewKey("clothing", index)}
              index={index}
              kind="clothing"
              recommendation={rec}
              beforePhoto={photos.body}
              preview={previewFor("clothing", index, photos.body)}
              sessionId={sessionId}
            />
          ))}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="font-serif text-2xl text-ink">Hair</h2>
        <p className="mt-1 text-sm text-muted">Three styles chosen for your face shape and hair texture.</p>
        <div className="mt-4 space-y-5">
          {analysis.hairstyles.map((rec, index) => (
            <RecommendationCard
              key={previewKey("hairstyle", index)}
              index={index}
              kind="hairstyle"
              recommendation={rec}
              beforePhoto={photos.face}
              preview={previewFor("hairstyle", index, photos.face)}
              sessionId={sessionId}
            />
          ))}
        </div>
      </section>

      <div className="mt-10 space-y-3">
        <Button className="w-full" onClick={() => setShowSave(true)}>
          Save my style profile
        </Button>
        <LinkButton href="/upload" variant="secondary" className="w-full">
          Try different photos
        </LinkButton>
        <button
          type="button"
          className="w-full py-2 text-sm text-muted underline-offset-2 hover:underline"
          onClick={() => {
            reset();
            router.push("/");
          }}
        >
          Start over and clear my results
        </button>
      </div>

      {showSave && (
        <SaveProfileDialog profile={toSavedProfile(analysis)} onClose={() => setShowSave(false)} />
      )}
    </Shell>
  );
}
