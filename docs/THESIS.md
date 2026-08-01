# Product Thesis — B-Side

**One sentence:** Drop in your episode; B-Side gives you back everything the release needs — transcript, chapters, show notes, episode art, quote cards, and caption-synced audiogram clips — reviewed by you, sealed to your show's permanent archive on Backblaze B2.

## Problem

Every podcast episode and recorded talk carries an invisible second production: the *release kit*. Transcript for accessibility and SEO. Chapters and show notes for the feed. Titles that don't undersell the content. Episode art. Pull-quote cards and captioned audio clips for social — the single highest-leverage growth asset a show has. Independent creators do this with four or five disconnected tools and hours of manual grind per episode, or skip it and grow slower. The work is repetitive, structured, multi-modal — and perfectly suited to an orchestrated generative pipeline with a human veto.

## Audience

The ~4M active podcasts' long tail of independent creators and small studios, plus every conference speaker, lecturer, and interview-show host with a backlog of recorded audio. A nameable user with a *recurring* pain: this workflow re-runs for every single episode, forever.

## Solution

B-Side is a production pipeline, not a prompt box:

1. **Ingest** — episode audio enters as a first-class provenance event (`Pipeline.ingest`): hashed, manifested, archived to B2 before anything generates.
2. **Understand** — AssemblyAI transcribes with word-level timings; Gemini reads the transcript and produces chapters, show notes, titles, pull-quote candidates *with timestamp anchors*, and an art brief derived from what was actually said.
3. **Design** — image models (NVIDIA NIM FLUX primary, Gemini fallback chain) render episode art and quote cards under the show's persistent style canon.
4. **Compose** — a custom Genblaze provider cuts the quoted audio segment and composites caption-synced audiogram video (art + waveform + word-timed captions + the creator's real voice) via the SDK's compositor lifecycle.
5. **Review** — every asset lands in a review queue: approve, or reject with feedback that drives an evaluator-linked regeneration (parent-linked lineage). Nothing ships unapproved.
6. **Seal** — approved kits are sealed to the show archive on B2 with verified manifests; the release bundle is delivered by presigned URL.

## Why B2 (delete test: product collapses)

B2 is the *system of record*, not a dump: structured show/episode/run key hierarchy holds source audio, every artifact, every manifest, the show style canon, and review decisions. The app can rebuild its entire state from the bucket alone — demonstrated live by a restore-from-B2 flow. Scratch prefixes carry lifecycle expiry; archive prefixes persist; delivery is presigned from a private bucket; every asset is fetched-byte re-hash verifiable in the UI. Remove B2 and there is no archive, no restore, no delivery, no verification — no product.

## Why Genblaze (delete test: product collapses)

The pipeline *is* Genblaze: ingest, STT, chat-driven direction, multi-provider image generation with declarative fallback chains, custom-provider composition, step-level caching (an episode re-run never re-pays for unchanged steps), streaming events driving the live UI, evaluator-gated retries with run lineage, and canonical manifests verifying every byte. It exercises the exact SDK surface — `Pipeline.ingest`, `AssemblyAIProvider`, custom providers, `StepCache`, `astream`, `AgentLoop`/`Evaluator`, `KeyStrategy.HIERARCHICAL`, `Manifest.verify` — that the audited field of 77 left untouched. Remove Genblaze and the orchestration, provenance, caching, and failover all vanish — no pipeline.

## Why nothing in the field of 77 comes close

The most crowded clusters are provenance vaults and brand-campaign generators. Zero of 77 serve podcasters; zero ingest real creator audio as the pipeline's raw material; zero use word-level timing to make generation *timing-native* (quotes anchored to the second; captions synced to the spoken word). B-Side's inputs are real human work — which also makes the demo unforgettable: the judge hears the creator's actual voice inside a generated, captioned clip.

## Why now

Genblaze 0.4.5 just made non-generative ingest and transcription first-class citizens. The exact moment the SDK can treat *existing human media* as pipeline input is the moment this product becomes buildable in a weekend and defensible in production.
