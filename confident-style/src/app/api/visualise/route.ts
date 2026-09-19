import { NextResponse } from "next/server";
import { z } from "zod";
import type { UploadedPhoto } from "@/lib/analyse";
import { getImageProvider, ImageProviderError } from "@/lib/images";
import { PhotoError, readPhoto, wipePhoto } from "@/lib/photos";
import { RecommendationKindSchema } from "@/lib/schema";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

const FieldsSchema = z.object({
  kind: RecommendationKindSchema,
  title: z.string().min(1).max(200),
  image_prompt: z.string().min(1).max(4000),
});

/**
 * POST multipart/form-data { photo: File, kind, title, image_prompt }
 * Returns { image: dataUri, provider, illustrative }. The photo is wiped after use.
 */
export async function POST(request: Request) {
  let photo: UploadedPhoto | undefined;
  try {
    const form = await request.formData();
    const fields = FieldsSchema.safeParse({
      kind: form.get("kind"),
      title: form.get("title"),
      image_prompt: form.get("image_prompt"),
    });
    if (!fields.success) {
      return NextResponse.json({ error: "Missing recommendation details." }, { status: 400 });
    }
    photo = await readPhoto(form, "photo");

    const result = await getImageProvider().visualise({
      photo,
      kind: fields.data.kind,
      title: fields.data.title,
      imagePrompt: fields.data.image_prompt,
    });
    return NextResponse.json({
      image: result.dataUri,
      provider: result.provider,
      illustrative: result.illustrative,
    });
  } catch (error) {
    if (error instanceof PhotoError) {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    if (error instanceof ImageProviderError) {
      console.warn("[visualise]", error.message);
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    console.error("[visualise] unexpected error", error);
    return NextResponse.json({ error: "The preview could not be created." }, { status: 500 });
  } finally {
    wipePhoto(photo);
  }
}
