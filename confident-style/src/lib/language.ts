/**
 * Words we never show to the user. Checked against the model's output (server side)
 * and against the app's own copy (scripts/check-copy.mjs).
 */
export const DISCOURAGED_PATTERNS: RegExp[] = [
  /\bflaws?\b/i,
  /\bproblem areas?\b/i,
  /\bslimming\b/i,
  /\bhides?\b/i,
  /\bhiding\b/i,
  /\bconceals?\b/i,
  /\bcamouflages?\b/i,
  /\bdisguises?\b/i,
  /\bminimi[sz]es?\b/i,
  /\bimperfections?\b/i,
  /\bunflattering\b/i,
];

export function findDiscouragedLanguage(text: string): string[] {
  const hits: string[] = [];
  for (const pattern of DISCOURAGED_PATTERNS) {
    const match = text.match(pattern);
    if (match) hits.push(match[0]);
  }
  return hits;
}
