/**
 * Browser-side helpers. Photos are downscaled before upload so requests stay small
 * and well under the vision API's per-image limit.
 */
const MAX_EDGE = 1280;
const JPEG_QUALITY = 0.86;

export async function prepareForUpload(file: File): Promise<Blob> {
  if (!file.type.startsWith("image/")) {
    throw new Error("Please choose an image file.");
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    // Formats the browser can't decode (some HEIC files): send as-is if small enough.
    if (file.size <= 4 * 1024 * 1024) return file;
    throw new Error("We couldn't open that photo. A JPEG or PNG works best.");
  }
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return file;
  ctx.drawImage(bitmap, 0, 0, width, height);
  bitmap.close();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
  );
  return blob ?? file;
}
