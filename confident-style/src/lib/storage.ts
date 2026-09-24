import "server-only";
import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { SavedProfile } from "./schema";

/**
 * Minimal append-only storage (JSON lines). Swap this module for a database
 * when needed; nothing else in the app knows how records are stored.
 *
 * Only text is ever written here. Photos never reach this module.
 */
const DATA_DIR = path.resolve(/*turbopackIgnore: true*/ process.cwd(), process.env.DATA_DIR || "./data");

async function append(file: string, record: Record<string, unknown>): Promise<void> {
  await mkdir(DATA_DIR, { recursive: true });
  await appendFile(path.join(DATA_DIR, file), JSON.stringify(record) + "\n", "utf8");
}

export async function saveStyleProfile(email: string, profile: SavedProfile): Promise<string> {
  const id = randomUUID();
  await append("profiles.jsonl", {
    id,
    created_at: new Date().toISOString(),
    email,
    profile,
  });
  return id;
}

export interface FeedbackRecord {
  session_id: string;
  kind: "clothing" | "hairstyle";
  title: string;
  vote: "up" | "down";
}

/** Anonymous: no email, no IP address, no user agent. */
export async function saveFeedback(feedback: FeedbackRecord): Promise<void> {
  await append("feedback.jsonl", {
    created_at: new Date().toISOString(),
    ...feedback,
  });
}
