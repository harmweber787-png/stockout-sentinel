"use client";

import { useObjectUrl } from "@/lib/object-url";
import type { Recommendation, RecommendationKind } from "@/lib/schema";
import { FeedbackButtons } from "./FeedbackButtons";

export type PreviewState =
  | { status: "loading" }
  | { status: "ready"; image: string; illustrative: boolean }
  | { status: "unavailable"; note: string };

interface Props {
  index: number;
  kind: RecommendationKind;
  recommendation: Recommendation;
  beforePhoto: Blob | null;
  preview: PreviewState;
  sessionId: string;
}

export function RecommendationCard({
  index,
  kind,
  recommendation,
  beforePhoto,
  preview,
  sessionId,
}: Props) {
  const beforeUrl = useObjectUrl(beforePhoto);

  return (
    <article className="rounded-3xl bg-white p-5 shadow-soft ring-1 ring-line">
      <div className="mb-1 text-xs uppercase tracking-widest text-accent-deep">
        {kind === "clothing" ? "Outfit" : "Hairstyle"} {index + 1}
      </div>
      <h3 className="font-serif text-2xl text-ink">{recommendation.title}</h3>
      <p className="mt-2 text-[15px] leading-relaxed text-ink/80">{recommendation.description}</p>
      <p className="mt-3 rounded-2xl bg-blush px-4 py-3 text-[15px] leading-relaxed text-ink">
        <span className="font-medium text-accent-deep">Why this suits you: </span>
        {recommendation.why}
      </p>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <figure>
          <div className="aspect-[3/4] overflow-hidden rounded-2xl bg-blush/60">
            {beforeUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={beforeUrl} alt="Your photo" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted">
                Your photo isn&apos;t kept after the analysis, so it can&apos;t be shown after a
                refresh.
              </div>
            )}
          </div>
          <figcaption className="mt-1.5 text-center text-xs text-muted">Now</figcaption>
        </figure>
        <figure>
          <div className="relative aspect-[3/4] overflow-hidden rounded-2xl bg-blush/60">
            {preview.status === "loading" && (
              <div className="flex h-full animate-pulse items-center justify-center p-4 text-center text-xs text-muted">
                Creating your preview…
              </div>
            )}
            {preview.status === "ready" && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={preview.image}
                alt={`${recommendation.title} preview`}
                className="h-full w-full object-cover"
              />
            )}
            {preview.status === "unavailable" && (
              <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted">
                {preview.note}
              </div>
            )}
          </div>
          <figcaption className="mt-1.5 text-center text-xs text-muted">
            {preview.status === "ready" && preview.illustrative ? "Illustration" : "With this look"}
          </figcaption>
        </figure>
      </div>

      <div className="mt-4 flex justify-end">
        <FeedbackButtons sessionId={sessionId} kind={kind} title={recommendation.title} />
      </div>
    </article>
  );
}
