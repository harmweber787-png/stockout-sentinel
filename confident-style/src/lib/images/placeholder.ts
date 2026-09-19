import type { ImageProvider, VisualiseRequest, VisualiseResult } from "./types";

function escapeXml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function wrap(text: string, max = 22): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    if ((current + " " + word).trim().length > max && current) {
      lines.push(current);
      current = word;
    } else {
      current = (current + " " + word).trim();
    }
  }
  if (current) lines.push(current);
  return lines.slice(0, 4);
}

/**
 * No external calls. Produces a soft illustrative card carrying the recommendation
 * title, so the whole flow works before an image-editing key is configured.
 */
export const placeholderProvider: ImageProvider = {
  name: "placeholder",
  async visualise({ kind, title }: VisualiseRequest): Promise<VisualiseResult> {
    const lines = wrap(title);
    const startY = 300 - ((lines.length - 1) * 40) / 2;
    const tspans = lines
      .map(
        (line, i) =>
          `<tspan x="240" y="${startY + i * 40}">${escapeXml(line)}</tspan>`,
      )
      .join("");
    const label = kind === "clothing" ? "Outfit preview" : "Hairstyle preview";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="600" viewBox="0 0 480 600">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#F6E7DD"/>
      <stop offset="1" stop-color="#E9C4B4"/>
    </linearGradient>
  </defs>
  <rect width="480" height="600" rx="28" fill="url(#g)"/>
  <circle cx="240" cy="170" r="46" fill="#FFFFFF" fill-opacity="0.55"/>
  <text x="240" y="118" text-anchor="middle" font-family="Georgia, serif" font-size="18" fill="#7A3B2E" letter-spacing="2">${escapeXml(label.toUpperCase())}</text>
  <text text-anchor="middle" font-family="Georgia, serif" font-size="28" fill="#3E2A2A">${tspans}</text>
  <text x="240" y="520" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="15" fill="#7A3B2E">Illustrative preview</text>
  <text x="240" y="545" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="13" fill="#7A3B2E" fill-opacity="0.8">A photo of you in this look arrives once image editing is switched on.</text>
</svg>`;
    return {
      dataUri: `data:image/svg+xml;base64,${Buffer.from(svg, "utf8").toString("base64")}`,
      provider: "placeholder",
      illustrative: true,
    };
  },
};
