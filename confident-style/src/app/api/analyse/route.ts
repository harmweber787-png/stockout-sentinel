import { NextResponse } from "next/server";
import { analysePhotos, AnalysisError, type UploadedPhoto } from "@/lib/analyse";
import { PhotoError, readPhoto, wipePhoto } from "@/lib/photos";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

/**
 * POST multipart/form-data { body: File, face: File }
 * Photos live in memory for the duration of this request only.
 */
export async function POST(request: Request) {
  let body: UploadedPhoto | undefined;
  let face: UploadedPhoto | undefined;
  try {
    const form = await request.formData();
    body = await readPhoto(form, "body");
    face = await readPhoto(form, "face");
    const analysis = await analysePhotos(body, face);
    return NextResponse.json({ analysis });
  } catch (error) {
    if (error instanceof PhotoError) {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    if (error instanceof AnalysisError) {
      return NextResponse.json({ error: error.message }, { status: error.status });
    }
    console.error("[analyse] unexpected error", error);
    return NextResponse.json(
      { error: "Something went wrong on our side. Please try again." },
      { status: 500 },
    );
  } finally {
    wipePhoto(body);
    wipePhoto(face);
  }
}
