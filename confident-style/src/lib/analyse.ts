import "server-only";
import Anthropic from "@anthropic-ai/sdk";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import { StyleAnalysisSchema, type StyleAnalysis } from "./schema";
import {
  ANALYSIS_USER_PROMPT,
  STYLIST_SYSTEM_PROMPT,
  TONE_RETRY_NOTE,
} from "./stylist-prompt";
import { findDiscouragedLanguage } from "./language";

export type ImageMediaType = "image/jpeg" | "image/png" | "image/webp" | "image/gif";

export interface UploadedPhoto {
  /** Raw bytes, held in memory only. Wiped by the caller after use. */
  data: Buffer;
  mediaType: ImageMediaType;
}

export class AnalysisError extends Error {
  constructor(
    message: string,
    public readonly status: number = 500,
  ) {
    super(message);
    this.name = "AnalysisError";
  }
}

const MODEL = process.env.ANTHROPIC_MODEL || "claude-opus-5";

let client: Anthropic | null = null;
function getClient(): Anthropic {
  if (!process.env.ANTHROPIC_API_KEY) {
    throw new AnalysisError(
      "The styling service is not configured yet (missing ANTHROPIC_API_KEY).",
      503,
    );
  }
  client ??= new Anthropic();
  return client;
}

function imageBlock(photo: UploadedPhoto): Anthropic.Beta.BetaImageBlockParam {
  return {
    type: "image",
    source: {
      type: "base64",
      media_type: photo.mediaType,
      data: photo.data.toString("base64"),
    },
  };
}

/**
 * Runs the vision analysis. Photos are only ever passed through as base64 in the
 * request body; nothing is written to disk or kept after this function returns.
 */
export async function analysePhotos(
  body: UploadedPhoto,
  face: UploadedPhoto,
): Promise<StyleAnalysis> {
  const anthropic = getClient();

  const userContent: Anthropic.Beta.BetaContentBlockParam[] = [
    { type: "text", text: "Photo 1 (full body):" },
    imageBlock(body),
    { type: "text", text: "Photo 2 (face and hair):" },
    imageBlock(face),
    { type: "text", text: ANALYSIS_USER_PROMPT },
  ];

  const run = async (extraNote?: string): Promise<StyleAnalysis> => {
    const messages: Anthropic.Beta.BetaMessageParam[] = [
      { role: "user", content: userContent },
    ];
    if (extraNote) {
      messages.push({ role: "user", content: extraNote });
    }

    let response;
    try {
      response = await anthropic.beta.messages.parse({
        model: MODEL,
        max_tokens: 16000,
        system: STYLIST_SYSTEM_PROMPT,
        messages,
        output_config: { format: zodOutputFormat(StyleAnalysisSchema) },
        // Server-side refusal fallback: if the primary model declines, the API
        // re-runs the same request on a fallback model within the same call.
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
      });
    } catch (error) {
      if (error instanceof Anthropic.AuthenticationError) {
        throw new AnalysisError("The styling service key was not accepted.", 503);
      }
      if (error instanceof Anthropic.RateLimitError) {
        throw new AnalysisError(
          "Lots of people are getting styled right now. Please try again in a moment.",
          429,
        );
      }
      if (error instanceof Anthropic.BadRequestError) {
        throw new AnalysisError(
          "We couldn't read one of the photos. A clear, well-lit JPEG or PNG works best.",
          400,
        );
      }
      if (error instanceof Anthropic.APIError) {
        throw new AnalysisError("The styling service is briefly unavailable. Please try again.", 502);
      }
      throw error;
    }

    if (response.stop_reason === "refusal") {
      throw new AnalysisError(
        "We weren't able to analyse these photos. Please try a different pair of photos.",
        422,
      );
    }
    if (response.stop_reason === "max_tokens" || !response.parsed_output) {
      throw new AnalysisError("The analysis came back incomplete. Please try again.", 502);
    }

    const parsed = StyleAnalysisSchema.parse(response.parsed_output);
    if (parsed.clothing.length < 3 || parsed.hairstyles.length < 3) {
      throw new AnalysisError("The analysis came back incomplete. Please try again.", 502);
    }
    return {
      ...parsed,
      clothing: parsed.clothing.slice(0, 3),
      hairstyles: parsed.hairstyles.slice(0, 3),
    };
  };

  let result = await run();
  const hits = findDiscouragedLanguage(JSON.stringify(result));
  if (hits.length > 0) {
    console.warn("[analyse] retrying once for tone; found:", hits.join(", "));
    result = await run(TONE_RETRY_NOTE);
  }
  return result;
}
