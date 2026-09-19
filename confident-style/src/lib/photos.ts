import "server-only";
import type { ImageMediaType, UploadedPhoto } from "./analyse";

const ALLOWED: ReadonlySet<string> = new Set(["image/jpeg", "image/png", "image/webp"]);
/** Client resizes before upload; this is a generous safety cap (the API limit is 5 MB). */
export const MAX_PHOTO_BYTES = 4.5 * 1024 * 1024;

export class PhotoError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PhotoError";
  }
}

const FIELD_LABELS: Record<string, string> = {
  body: "full-body photo",
  face: "face photo",
  photo: "photo",
};

export async function readPhoto(form: FormData, field: string): Promise<UploadedPhoto> {
  const value = form.get(field);
  if (!(value instanceof Blob) || value.size === 0) {
    throw new PhotoError(`Please add the ${FIELD_LABELS[field] ?? "photo"}.`);
  }
  if (!ALLOWED.has(value.type)) {
    throw new PhotoError("Photos need to be JPEG, PNG or WebP.");
  }
  if (value.size > MAX_PHOTO_BYTES) {
    throw new PhotoError("That photo is very large. Please pick a smaller one.");
  }
  const data = Buffer.from(await value.arrayBuffer());
  return { data, mediaType: value.type as ImageMediaType };
}

/**
 * Overwrites the in-memory photo bytes. Node will free the buffer anyway, but
 * zeroing makes the "deleted straight after use" promise explicit and auditable.
 */
export function wipePhoto(photo: UploadedPhoto | undefined): void {
  photo?.data.fill(0);
}
