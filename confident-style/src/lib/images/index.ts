import "server-only";
import { falProvider } from "./fal";
import { geminiProvider } from "./gemini";
import { placeholderProvider } from "./placeholder";
import type { ImageProvider } from "./types";

export * from "./types";

export function getImageProvider(): ImageProvider {
  switch ((process.env.IMAGE_PROVIDER || "placeholder").toLowerCase()) {
    case "gemini":
      return geminiProvider;
    case "fal":
      return falProvider;
    case "placeholder":
      return placeholderProvider;
    default:
      console.warn(`[images] Unknown IMAGE_PROVIDER "${process.env.IMAGE_PROVIDER}", using placeholder.`);
      return placeholderProvider;
  }
}
