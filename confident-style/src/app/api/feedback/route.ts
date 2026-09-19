import { NextResponse } from "next/server";
import { FeedbackRequestSchema } from "@/lib/schema";
import { saveFeedback } from "@/lib/storage";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** POST JSON { session_id, kind, title, vote } - stored anonymously. */
export async function POST(request: Request) {
  const parsed = FeedbackRequestSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid feedback." }, { status: 400 });
  }
  try {
    await saveFeedback(parsed.data);
    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("[feedback] could not save", error);
    return NextResponse.json({ error: "Feedback could not be saved." }, { status: 500 });
  }
}
