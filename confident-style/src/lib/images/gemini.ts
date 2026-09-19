import {
  buildEditPrompt,
  ImageProviderError,
  type ImageProvider,
  type VisualiseRequest,
  type VisualiseResult,
} from "./types";

interface GeminiPart {
  text?: string;
  inlineData?: { mimeType: string; data: string };
}
interface GeminiResponse {
  candidates?: Array<{ content?: { parts?: GeminiPart[] } }>;
  promptFeedback?: { blockReason?: string };
  error?: { message?: string };
}

/**
 * Gemini image editing via the Generative Language REST API.
 * The photo is sent inline in the request and never stored by this app.
 */
export const geminiProvider: ImageProvider = {
  name: "gemini",
  async visualise({ photo, kind, imagePrompt }: VisualiseRequest): Promise<VisualiseResult> {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      throw new ImageProviderError("Image editing is not configured (missing GEMINI_API_KEY).", 503);
    }
    const model = process.env.GEMINI_IMAGE_MODEL || "gemini-2.5-flash-image";
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`;

    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-goog-api-key": apiKey },
      body: JSON.stringify({
        contents: [
          {
            parts: [
              { text: buildEditPrompt(kind, imagePrompt) },
              { inline_data: { mime_type: photo.mediaType, data: photo.data.toString("base64") } },
            ],
          },
        ],
        generationConfig: { responseModalities: ["IMAGE"] },
      }),
    });

    const json = (await response.json().catch(() => ({}))) as GeminiResponse;
    if (!response.ok) {
      throw new ImageProviderError(
        `Image editing failed (${response.status}): ${json.error?.message ?? "unknown error"}`,
      );
    }
    const part = json.candidates?.[0]?.content?.parts?.find((p) => p.inlineData?.data);
    if (!part?.inlineData) {
      const reason = json.promptFeedback?.blockReason;
      throw new ImageProviderError(
        reason ? `Image editing declined this request (${reason}).` : "Image editing returned no image.",
      );
    }
    return {
      dataUri: `data:${part.inlineData.mimeType};base64,${part.inlineData.data}`,
      provider: "gemini",
      illustrative: false,
    };
  },
};
