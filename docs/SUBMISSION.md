# Devpost Submission — B-Side

> Copy-paste source for the Devpost form. Every claim below is verifiable at the live URL, in the repo, or in CI.

**Live app (judges):** https://bside-production.up.railway.app — no sign-in anywhere; start at **/judge**
**Repo (public):** https://github.com/iamdflame/bside
**Video:** https://youtu.be/_o45OfY73SE

---

## Tagline (60 chars)

You made the episode. B-Side makes everything else.

## Inspiration

Every podcast episode and recorded talk hides a second production: the *release kit*. Transcript, chapters, show notes, titles, cover art, quote cards, captioned audiogram clips. Independent creators burn hours per episode across five disconnected tools — then store the results in a folder called `final_final_v2` on a laptop that will eventually die. We built the missing machine: drop in the audio, review what comes back, and seal it all to a permanent, verifiable archive.

## What it does

B-Side ingests an episode's audio and returns the entire release kit:

- **Transcript** with word-level timings (AssemblyAI via Genblaze)
- **Direction** — 3 titles, summary, show notes, timestamped chapters, and 3 pull quotes *anchored verbatim to the spoken words*, plus an art brief (Gemini → NIM Llama fallback chain)
- **Episode art** — FLUX under a deterministic quality gate (blank/flat frames are rejected and retried across the provider plan)
- **Quote cards** — model art + deterministic typography (image models can't spell; our typography engine can)
- **Audiograms** — the creator's *real voice* under karaoke captions that light up word-by-word (ASS `\k` timing from the transcript), composed by ffmpeg as a first-class Genblaze provider step
- **Human review gate** — approve, or reject with feedback; rejects regenerate with `parent_run_id` lineage
- **Sealed release** — every asset's bytes are fetched back from B2 and re-hashed; any mismatch fails the seal; the kit ZIP is delivered by presigned URL

Everything streams live to the UI over SSE, and every asset has a provenance drawer with a **"fetch bytes & re-hash now"** button that does a live B2 round trip in front of you.

## Judging criterion 1 — Real-world utility

The audience is nameable and enormous (millions of podcasts; every conference speaker), the pain is weekly and recurring, and the output is the actual deliverable set creators publish today. This is not a prompt toy: input is *their* work, the review gate makes it *their* call, and the archive makes it *their* record. The demo episode is about this exact problem — listen to it inside the product.

## Judging criterion 2 — Production readiness

- Durable job queue (SQLite) with exponential backoff, per-provider circuit breakers, idempotent stages — **we killed the live service mid-run on production and it resumed and finished** (documented in README; reproducible).
- Honest failure UX: stage errors surface in the UI (sanitized of presigned credentials) with attempt counts and one-click retry; provider fallbacks are labeled per asset.
- Guardrails: upload validation, per-IP rate limiting, daily episode budget, concurrency gate; secrets in env only; private bucket; OWASP-clean boundaries.
- **26 tests** (unit / live-B2 integration / e2e) + lint on GitHub Actions CI; one-command local setup (`make setup && make dev`) verified from scratch.
- Live deployment on Railway with healthchecks and a persistent volume.

## Judging criterion 3 — B2 storage & data orchestration

B2 is the **system of record**, not a dump:

- Four planes in one private bucket: app documents (`shows/…episode.json` written after *every* stage), source & derived media, SDK-native manifests (`genblaze/runs/{show}/{date}/{run}/…`), and expiring `scratch/…`.
- **Restore-from-B2 is a button on /judge**: it wipes the local read-model and rebuilds all state — shows, episodes, review decisions, lineage — from the bucket alone.
- **Fetched-byte verification is a product feature**: the evidence drawer and the seal stage download objects and re-hash them; the seal fails on mismatch (tamper detection covered by an integration test against the real bucket).
- Private-bucket delivery via short-lived presigned GETs (credential-redacting `PresignedURL`); ranged reads for content-type truthing; multipart-capable uploads.

## Judging criterion 4 — Use of Genblaze

The pipeline *is* Genblaze — including surface area beyond simple generation:

- `Pipeline.ingest(...)` — real creator audio and the transcript artifact enter as first-class INGEST steps with manifests (the SDK's non-generative workflow support, used for its exact intended purpose).
- `AssemblyAIProvider` — the matrix-inverse connector; its `word_timings` drive verbatim quote anchoring and caption sync.
- **Two first-party custom `SyncProvider`s** built on the documented extension contract: `QuoteCardProvider` and `AudiogramProvider` (ffmpeg + ASS karaoke) — so composition carries manifests, hashes, and lineage like any generation step.
- `chat()` callables with a real cross-vendor fallback chain (Gemini → NIM Llama), `fallback_models` for STT, subclassed `StepCache` for never-pay-twice reruns, `ObjectStorageSink` + `KeyStrategy.HIERARCHICAL` tenant-partitioned by show, `Manifest.verify()` surfaced in stage notes, and parent-linked runs powering the human reject→regenerate loop.
- Generate → **evaluate** → retry: a deterministic image evaluator rejects blank/flat frames before a human ever sees them.

## AI providers & models

| Stage | Provider (via Genblaze) | Model |
|---|---|---|
| Transcription | AssemblyAI | universal-3-5-pro (fallback universal-2) |
| Direction | Google Gemini → NVIDIA NIM | gemini-2.5-flash → meta/llama-3.3-70b-instruct |
| Episode art | NVIDIA NIM → Google Gemini | black-forest-labs/flux.1-dev → gemini-2.5-flash-image |
| Quote cards | B-Side custom provider | bside-card-v1 |
| Audiograms | B-Side custom provider (ffmpeg) | bside-audiogram-v1 |
| Demo narration | ElevenLabs | eleven_multilingual_v2 |

## What we're proud of

The audiogram moment: the judge hears a real human voice while the exact words light up in sync — generated, composed, hash-verified, and archived by one pipeline. And the evidence drawer: every claim in this writeup has a button next to it.

## What we learned / limitations (honest)

FLUX black-frames on long prompts and empty payloads (we bisected it live and built the evaluator because of it). Deterministic quality gates catch failure, not taste — the human gate is the real bar. One deployment = one workspace by design (no accounts; judges walk straight in). Model slugs rot fast — our config makes every model swappable via env.

---

# 3-minute demo video — script

**0:00–0:15 — the hook (voice over the actual demo episode playing)**
"Every podcaster knows this: the episode is the easy part. The release — transcript, chapters, notes, art, clips — is three hours you don't have. This is B-Side. Watch it do the whole thing, live."

**0:15–0:45 — drop the audio (screen: /show page)**
Drag `demo-episode.mp3` in. Pipeline rail lights up: ingest ✓ manifest verified. Transcribe ✓ 194 words. "Every stage you're seeing writes its manifest and artifacts to Backblaze B2 before moving on — via Genblaze, not around it."

**0:45–1:30 — the kit arrives (screen: episode page, SSE live)**
Direction fills in: titles, chapters, three quotes with timestamps. Art appears (mention the quality gate: "a deterministic evaluator already rejected any blank frames"). Cards compose. Then the moment: **play an audiogram** — real voice, captions lighting word-by-word. Hold it for 6 full seconds. "Those captions come from the transcript's word-level timings — this is composed, not templated."

**1:30–2:05 — the human gate + lineage**
Reject the art with a note ("more luminous, coral signal path"). Watch it regenerate. Open the evidence drawer on the new art: parent run → rejected run. "Every regeneration is lineage-linked. Nothing ships without your approval."

**2:05–2:40 — the proof (screen: evidence drawer + /judge)**
Click **Fetch bytes & re-hash now** → ✓ BYTES MATCH, ~1s round trip. Then /judge → **Restore state from B2**: "the local database is disposable — the whole product rebuilds from the bucket. We also killed the server mid-run today; it resumed exactly where it stopped."

**2:40–3:00 — close (screen: sealed kit download)**
Download kit v1. Open the ZIP: notes, chapters, transcript, art, cards, audiograms, provenance manifest. "You made the episode. B-Side made everything else — and can prove every byte of it. Judge mode is open, no sign-in: bside-production.up.railway.app."
