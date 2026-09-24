import type { UploadedPhoto } from "../analyse";
import type { RecommendationKind } from "../schema";

export interface VisualiseRequest {
  photo: UploadedPhoto;
  kind: RecommendationKind;
  title: string;
  imagePrompt: string;
}

export interface VisualiseResult {
  /** data: URI (image/*) ready for an <img src>. Never stored server side. */
  dataUri: string;
  provider: "placeholder" | "gemini" | "fal";
  /** True when the image is illustrative rather than an edit of the user's photo. */
  illustrative: boolean;
}

export interface ImageProvider {
  readonly name: VisualiseResult["provider"];
  visualise(request: VisualiseRequest): Promise<VisualiseResult>;
}

export class ImageProviderError extends Error {
  constructor(
    message: string,
    public readonly status: number = 502,
  ) {
    super(message);
    this.name = "ImageProviderError";
  }
}

/** Shared prefix so every provider keeps the person recognisably herself. */
export function buildEditPrompt(kind: RecommendationKind, imagePrompt: string): string {
  const subject = kind === "clothing" ? "outfit" : "hairstyle";
  return [
    `Edit this photo of the person so she is wearing a new ${subject}.`,
    "Keep her face, facial features, skin tone, body, pose, expression and identity exactly the same.",
    kind === "clothing"
      ? "Keep her hair as it is. Change only the clothing and accessories."
      : "Keep her clothing as it is. Change only the hair.",
    `New ${subject}: ${imagePrompt}`,
    "Photorealistic, natural lighting, same background, flattering but true to life.",
  ].join(" ");
}
