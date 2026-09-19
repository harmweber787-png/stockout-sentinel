import {
  buildEditPrompt,
  ImageProviderError,
  type ImageProvider,
  type VisualiseRequest,
  type VisualiseResult,
} from "./types";

interface FalResponse {
  images?: Array<{ url: string; content_type?: string }>;
  detail?: unknown;
}

/**
 * FLUX.1 Kontext (image editing) via fal.ai's synchronous endpoint.
 * The photo is passed as a data URI; the returned image is fetched and handed
 * to the browser as a data URI so this app keeps no copy.
 */
export const falProvider: ImageProvider = {
  name: "fal",
  async visualise({ photo, kind, imagePrompt }: VisualiseRequest): Promise<VisualiseResult> {
    const key = process.env.FAL_KEY;
    if (!key) {
      throw new ImageProviderError("Image editing is not configured (missing FAL_KEY).", 503);
    }
    const model = process.env.FAL_KONTEXT_MODEL || "fal-ai/flux-pro/kontext";

    const response = await fetch(`https://fal.run/${model}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Key ${key}` },
      body: JSON.stringify({
        prompt: buildEditPrompt(kind, imagePrompt),
        image_url: `data:${photo.mediaType};base64,${photo.data.toString("base64")}`,
        output_format: "jpeg",
        safety_tolerance: "2",
      }),
    });
    const json = (await response.json().catch(() => ({}))) as FalResponse;
    if (!response.ok) {
      throw new ImageProviderError(
        `Image editing failed (${response.status}): ${JSON.stringify(json.detail ?? "unknown error")}`,
      );
    }
    const image = json.images?.[0];
    if (!image?.url) {
      throw new ImageProviderError("Image editing returned no image.");
    }

    const download = await fetch(image.url);
    if (!download.ok) {
      throw new ImageProviderError("The edited image could not be retrieved.");
    }
    const bytes = Buffer.from(await download.arrayBuffer());
    const mime = download.headers.get("content-type") || image.content_type || "image/jpeg";
    return {
      dataUri: `data:${mime};base64,${bytes.toString("base64")}`,
      provider: "fal",
      illustrative: false,
    };
  },
};
