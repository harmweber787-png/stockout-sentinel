#!/usr/bin/env node
/**
 * Fails if user-facing copy contains language the product never shows.
 * Scans screens and components (not the model prompt, which legitimately lists these words
 * as things to avoid, and not the tone guard itself).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const ROOTS = ["src/app", "src/components"];
const PATTERNS = [
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

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.(tsx?|mdx?)$/.test(entry)) yield full;
  }
}

let failures = 0;
for (const root of ROOTS) {
  for (const file of walk(root)) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      for (const pattern of PATTERNS) {
        const match = line.match(pattern);
        if (match) {
          failures++;
          console.error(`${file}:${i + 1}: "${match[0]}" - ${line.trim()}`);
        }
      }
    });
  }
}

if (failures) {
  console.error(`\n${failures} discouraged word(s) found in user-facing copy.`);
  process.exit(1);
}
console.log("Copy check passed: no discouraged language in user-facing text.");
