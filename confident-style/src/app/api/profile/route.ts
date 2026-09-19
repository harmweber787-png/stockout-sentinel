import { NextResponse } from "next/server";
import { SaveProfileRequestSchema } from "@/lib/schema";
import { saveStyleProfile } from "@/lib/storage";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** POST JSON { email, profile } - stores the text profile only. */
export async function POST(request: Request) {
  const parsed = SaveProfileRequestSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Please check the email address and try again." },
      { status: 400 },
    );
  }
  try {
    const id = await saveStyleProfile(parsed.data.email, parsed.data.profile);
    return NextResponse.json({ ok: true, id });
  } catch (error) {
    console.error("[profile] could not save", error);
    return NextResponse.json(
      { error: "We couldn't save your profile just now. Please try again." },
      { status: 500 },
    );
  }
}
