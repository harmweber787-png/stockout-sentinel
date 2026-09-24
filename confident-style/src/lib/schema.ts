import { z } from "zod";

/**
 * Structure returned by the styling analysis (mirrors the vision system prompt).
 * Kept flat and JSON-schema friendly so it can be used as a structured output format.
 */
export const RecommendationSchema = z.object({
  title: z.string().describe("Short name of the outfit or hairstyle"),
  description: z
    .string()
    .describe("Two or three sentences describing the look in concrete terms"),
  why: z
    .string()
    .describe(
      "One positive sentence explaining why this suits her, framed around what it highlights",
    ),
  image_prompt: z
    .string()
    .describe(
      "Detailed description of the outfit or hairstyle for an image-editing model. The person's face, body and identity stay unchanged.",
    ),
});

export const StyleAnalysisSchema = z.object({
  body_shape: z.string().describe("e.g. hourglass, pear, apple, rectangle, inverted triangle"),
  colour_type: z
    .string()
    .describe("warm/cool, light/deep and the seasonal palette, e.g. 'Warm Autumn'"),
  face_shape: z.string().describe("e.g. oval, round, heart, square, oblong"),
  hair: z.string().describe("Hair texture and length, e.g. 'fine, wavy, shoulder-length'"),
  clothing: z.array(RecommendationSchema).describe("Exactly three clothing recommendations"),
  hairstyles: z.array(RecommendationSchema).describe("Exactly three hairstyle recommendations"),
  confidence_note: z
    .string()
    .describe("One genuine, specific sentence about what already looks great about her"),
});

export type Recommendation = z.infer<typeof RecommendationSchema>;
export type StyleAnalysis = z.infer<typeof StyleAnalysisSchema>;

/** The profile we persist when the user saves it: text only, no image prompts, no photos. */
export const SavedProfileSchema = z.object({
  body_shape: z.string(),
  colour_type: z.string(),
  face_shape: z.string(),
  hair: z.string(),
  clothing: z.array(RecommendationSchema.omit({ image_prompt: true })).max(3),
  hairstyles: z.array(RecommendationSchema.omit({ image_prompt: true })).max(3),
  confidence_note: z.string(),
});

export type SavedProfile = z.infer<typeof SavedProfileSchema>;

export const SaveProfileRequestSchema = z.object({
  email: z.string().trim().toLowerCase().email().max(254),
  profile: SavedProfileSchema,
});

export const FeedbackRequestSchema = z.object({
  /** Random id generated in the browser; never linked to an email address. */
  session_id: z.string().uuid(),
  kind: z.enum(["clothing", "hairstyle"]),
  title: z.string().min(1).max(200),
  vote: z.enum(["up", "down"]),
});

export const RecommendationKindSchema = z.enum(["clothing", "hairstyle"]);
export type RecommendationKind = z.infer<typeof RecommendationKindSchema>;

export function toSavedProfile(analysis: StyleAnalysis): SavedProfile {
  const strip = ({ title, description, why }: Recommendation) => ({ title, description, why });
  return {
    body_shape: analysis.body_shape,
    colour_type: analysis.colour_type,
    face_shape: analysis.face_shape,
    hair: analysis.hair,
    clothing: analysis.clothing.map(strip),
    hairstyles: analysis.hairstyles.map(strip),
    confidence_note: analysis.confidence_note,
  };
}
