# Confident Style

A mobile-first web app that turns two photos into warm, practical styling advice.
A woman uploads one full-body photo and one face/hair photo and receives:

- a short read of her **body shape, colour season, face shape and hair texture**,
- **three clothing ideas** and **three hairstyle ideas**, each with a one-sentence
  "why this suits you",
- a **before/after preview** of each idea, generated from her own photo with an
  image-editing model (or an illustrative placeholder until one is configured),
- the option to **save a written style profile** by email, and
- **thumbs up/down** per recommendation, stored anonymously.

Make-up is out of scope for v1.

Built with Next.js (App Router), TypeScript and Tailwind CSS. The analysis runs on
Claude via the Anthropic SDK with structured (JSON schema) output.

## Privacy by design

- **Photos are never stored.** They are downscaled in the browser, sent once for
  analysis (and once per preview), processed in memory and overwritten as soon as
  the request finishes (`src/lib/photos.ts`, `wipePhoto`). Nothing is written to disk.
- The browser keeps the photos in memory only for the current visit so previews can
  be generated. A page refresh drops them; the written advice survives via
  `sessionStorage`.
- **Saving a style profile stores text only**: shape, palette, face shape, hair and
  the six recommendations (title, description, why). No photos, no image prompts,
  no generated images.
- **Feedback is anonymous**: a random per-visit id, the recommendation title, the
  kind and the vote. No email, no IP address, no user agent.
- No forms ask for weight, size or age.

## Tone

All copy is supportive and non-judgemental. Words such as "flaw", "problem area",
"slimming" or "hide" never appear in the interface:

- `npm run lint:copy` fails the build if any of them show up in user-facing copy.
- The same list is checked against the model's output; if the model slips, the
  request is retried once with a corrective note (`src/lib/analyse.ts`).

## Getting started

```bash
cd confident-style
npm install
cp .env.example .env.local   # then add your keys
npm run dev                  # http://localhost:3000
```

### Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Vision analysis (Claude). |
| `ANTHROPIC_MODEL` | no | Defaults to `claude-opus-5`. |
| `IMAGE_PROVIDER` | no | `placeholder` (default), `gemini` or `fal`. |
| `GEMINI_API_KEY`, `GEMINI_IMAGE_MODEL` | for `gemini` | Gemini image editing (default model `gemini-2.5-flash-image`). |
| `FAL_KEY`, `FAL_KONTEXT_MODEL` | for `fal` | FLUX.1 Kontext via fal.ai (default `fal-ai/flux-pro/kontext`). |
| `DATA_DIR` | no | Where profiles and feedback are appended as JSON lines (default `./data`). |

With `IMAGE_PROVIDER=placeholder` the whole flow works without any image-editing
key: each preview is an illustrative card carrying the recommendation title, and the
UI labels it "Illustration" rather than "With this look".

### Scripts

```bash
npm run dev         # local development
npm run build       # production build
npm run start       # serve the production build
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
npm run lint:copy   # discouraged-language check on user-facing copy
npm run check       # typecheck + lint + lint:copy
```

## How it works

```
/            Welcome: what happens, what is (not) stored
/upload      Two photos, lighting/posture guidance, client-side downscale to <=1280px JPEG
   -> POST /api/analyse   (multipart: body, face)  -> Claude vision, structured JSON
/results     Confidence note, four facts, 3 outfits + 3 hairstyles
   -> POST /api/visualise (multipart: photo, kind, title, image_prompt) x6, in parallel
   -> POST /api/profile   (json: email, profile)   "Save my style profile"
   -> POST /api/feedback  (json: session_id, kind, title, vote)
```

Key files:

| Path | What it does |
| --- | --- |
| `src/lib/stylist-prompt.ts` | The stylist system prompt (Prompt 2 of the brief). |
| `src/lib/schema.ts` | Zod schemas for the analysis JSON, saved profile and feedback. |
| `src/lib/analyse.ts` | Claude call with `output_config.format` (structured output), error mapping, tone retry. |
| `src/lib/photos.ts` | Reads photos from multipart bodies, validates type/size, wipes buffers. |
| `src/lib/images/` | Image providers: `placeholder`, `gemini`, `fal`; shared identity-preserving prompt. |
| `src/lib/storage.ts` | Append-only JSON-lines storage for profiles and feedback (swap for a DB here). |
| `src/lib/session-store.ts` | Browser-side store: analysis in `sessionStorage`, anonymous session id. |
| `src/app/api/*` | Route handlers. Photo routes run on the Node runtime, `maxDuration = 120`. |
| `scripts/check-copy.mjs` | Discouraged-language check. |

### Image generation

`src/lib/images/types.ts` builds one prompt for every provider that insists on keeping
the person's face, body, skin tone, pose and identity unchanged and changes only the
outfit (full-body photo) or the hair (face photo). Adding a provider means implementing
the `ImageProvider` interface and registering it in `src/lib/images/index.ts`.

The fal provider fetches the generated image and returns it to the browser as a data
URI so no URL to the user's likeness is kept or exposed by this app. Note that both
Gemini and fal receive the photo under their own terms of service; check them before
enabling either in production.

## Storage and deployment notes

The default storage is a `data/` directory with `profiles.jsonl` and `feedback.jsonl`.
That is fine for a single server or local development. On serverless platforms the
filesystem is ephemeral, so point `saveStyleProfile` / `saveFeedback` in
`src/lib/storage.ts` at a database before launch. Nothing else needs to change.

## Not in v1

- Make-up advice.
- Accounts or retrieval of a saved profile (profiles are stored, not yet displayed).
- Choosing an occasion (work, date, everyday). The analysis request is a single
  route, so an optional `occasion` field can be threaded through `/api/analyse`
  into the user prompt without touching the rest of the flow.
