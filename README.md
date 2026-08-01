<p align="center">
  <img src="docs/media/wordmark.svg" alt="B-Side" width="360" />
</p>

<h3 align="center">You made the episode. B-Side makes everything else.</h3>

<p align="center">
  Drop in your audio → get back the entire release kit — transcript, chapters, show notes,
  episode art, quote cards, and word-synced audiogram clips — every asset reviewed by you,
  provenance-sealed, and archived to your show's permanent record on <b>Backblaze B2</b>,
  orchestrated end-to-end by <b>Genblaze</b>.
</p>

<p align="center">
  <a href="https://github.com/iamdflame/bside/actions/workflows/ci.yml"><img src="https://github.com/iamdflame/bside/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  · <b>Live:</b> see the Devpost submission for the judge URL (<code>/judge</code> — no sign-in)
</p>

---

## The problem

Every podcast episode and recorded talk carries an invisible second production: the *release kit*.
Transcript for accessibility and search. Chapters and show notes for the feed. Titles that don't
undersell the content. Cover art. Pull-quote cards and captioned audio clips for social — the
highest-leverage growth asset a show has. Independent creators burn hours per episode across four
or five disconnected tools, or skip the work and grow slower. B-Side turns that grind into one
reviewed, provenance-sealed pipeline run.

## How it works

```
                        ┌─────────────────────────  B-Side (one service)  ─────────────────────────┐
 episode audio ──────►  │  FastAPI + durable job queue (SQLite, resumable, idempotent stages)      │
                        │                                                                          │
                        │  ingest ─► transcribe ─► direct ─► art ─► cards ─► audiograms ─► seal    │
                        │  (Genblaze │ AssemblyAI │ Gemini→ │ FLUX→ │ custom │ custom     │ zip +  │
                        │   ingest)  │ word       │ NIM     │ Gemini│ SyncPr │ SyncProv   │ verify │
                        │            │ timings    │ chat    │ image │ ovider │ + ffmpeg   │        │
                        └───────┬────┴────────────┴─────────┴───────┴────────┴────────────┴────────┘
                                │  every step: manifest → ObjectStorageSink (HIERARCHICAL, tenant=show)
                                ▼
                   ┌──────────────  Backblaze B2 (private bucket — system of record)  ─────────────┐
                   │ shows/{show}/show.json                    show canon (style, palette)         │
                   │ shows/{show}/episodes/{ep}/episode.json   state document (restore source)     │
                   │ shows/{show}/episodes/{ep}/source/…       immutable ingested audio            │
                   │ shows/{show}/episodes/{ep}/transcript/…   words + timings                     │
                   │ shows/{show}/episodes/{ep}/release/…      sealed kit ZIPs (v1, v2, …)         │
                   │ genblaze/runs/{show}/{date}/{run}/…       SDK-native manifests + assets       │
                   │ scratch/…                                 lifecycle-expiring workspace        │
                   └───────────────────────────────────────────────────────────────────────────────┘
```

The web app (React + a hand-rolled design system) streams every stage over SSE, exposes a
human review gate (approve / reject-with-feedback → regeneration with manifest lineage), and a
provenance drawer that **fetches the object's bytes from B2 live and re-hashes them in front of
you**.

## How we use Genblaze (meaningfully, not decoratively)

The pipeline *is* Genblaze — including surface area far beyond `generate()`:

| SDK surface | Where B-Side uses it |
|---|---|
| `Pipeline.ingest(...)` | Real creator audio and the transcript artifact enter as first-class INGEST steps with their own manifests — the SDK's non-generative workflow support, exercised for its exact intended purpose ([stages.py](server/bside/stages.py)) |
| `AssemblyAIProvider` | The matrix-inverse connector: audio → hash-verified TEXT asset whose `word_timings` drive quote anchoring and caption sync ([stages.py](server/bside/stages.py)) |
| **Custom `SyncProvider` ×2** | `QuoteCardProvider` (deterministic typography over model art) and `AudiogramProvider` (ffmpeg composition with ASS karaoke captions) — first-party providers built on the documented extension contract, so composition carries manifests and lineage like any generation ([kit_providers.py](server/bside/kit_providers.py)) |
| `chat()` callables (google + nvidia) | Editorial direction with a real cross-vendor fallback chain (Gemini → NIM Llama), honestly labeled in the UI ([providers.py](server/bside/providers.py)) |
| `fallback_models=[...]` | STT model failover (`universal-3-5-pro` → `universal-2`) |
| `StepCache` (subclassed) | Deterministic step-level dedup so re-runs never re-pay; our `ValidatingStepCache` adds byte-existence validation ([stages.py](server/bside/stages.py)) |
| `ObjectStorageSink` + `KeyStrategy.HIERARCHICAL` | Tenant-partitioned (tenant = show) manifests + assets landing in B2 on every step |
| `Manifest.verify()` + parent-linked runs | Every stage note surfaces `manifest verified=True`; rejected assets regenerate with `parent_run_id` lineage the UI renders |
| Quality evaluation before acceptance | Deterministic image evaluator rejects blank/flat frames and retries across the provider plan — generate → evaluate → retry, visible in provider notes |

## How we use B2 (structurally essential)

- **System of record:** the `Episode` document persisted to B2 after *every* stage is the source
  of truth. The local DB is a disposable read-model — `/judge` has a **Restore from B2** button
  that rebuilds all state by walking the bucket.
- **Four planes, one bucket:** app documents, source/derived media, SDK-native manifests
  (`genblaze/…`), and lifecycle-expiring `scratch/…`.
- **Private bucket + presigned delivery:** nothing public; the UI and kit downloads use
  short-lived presigned GETs (credential-redacting `PresignedURL`s from `genblaze-s3`).
- **Fetched-byte verification as a feature:** the evidence drawer and the seal stage download
  objects and re-hash them — the seal *fails* on any mismatch (tamper detection is tested).
- **Multipart-capable uploads, ranged reads** (magic-byte sniffing uses a 16-byte ranged GET).

Delete B2 and there is no archive, no restore, no delivery, no verification — no product.
Delete Genblaze and there is no orchestration, no manifests, no lineage, no caching — no pipeline.

## Providers & models

| Stage | Provider (via Genblaze) | Model |
|---|---|---|
| Transcription | AssemblyAI | `universal-3-5-pro` (fallback `universal-2`) |
| Direction | Google Gemini → NVIDIA NIM (fallback chain) | `gemini-2.5-flash` → `meta/llama-3.3-70b-instruct` |
| Episode art | NVIDIA NIM → Gemini (fallback chain) | `black-forest-labs/flux.1-dev` → `gemini-2.5-flash-image` |
| Quote cards | B-Side `QuoteCardProvider` (custom) | `bside-card-v1` |
| Audiograms | B-Side `AudiogramProvider` (custom, ffmpeg) | `bside-audiogram-v1` |
| Demo narration | ElevenLabs (fixture generation) | `eleven_multilingual_v2` |

## Run it locally

```bash
git clone https://github.com/iamdflame/bside && cd bside
cp .env.example .env        # fill in B2 + provider keys (see the file for links)
make setup                  # venv + server deps + web build   (or: see Makefile targets)
make dev                    # serves API + built SPA on :8000
```

Tests:

```bash
make test                   # unit + e2e (no credentials, no network costs)
BSIDE_LIVE_B2=1 make test-live   # + real B2 round-trip/restore/tamper suite
```

## Production notes (what's real)

- Durable jobs: SQLite queue, exponential backoff, per-provider circuit breakers; orphaned jobs
  recover on boot — kill the process mid-run and it resumes at the incomplete stage.
- Honest failure UX: stage errors (sanitized of presigned credentials) surface in the UI with
  attempt counts and a retry button; fallback usage is labeled per asset.
- Guardrails: upload type/size validation, per-IP rate limiting, daily episode budget,
  concurrency gate. Secrets live in env only; the bucket stays private.

## Limitations (stated, not hidden)

- Judged image quality is a deterministic gate (blank/flat detection), not an aesthetic model;
  the human review gate is the real quality bar.
- One deployment = one workspace (no user accounts by design — judges walk straight in;
  a real multi-tenant build would add auth in front of the same show-scoped storage).
- The bundled demo voice is synthesized; upload your own audio for the real experience.
- `parent_run_id` on regenerations is set via the same mechanism as `Pipeline.from_result`
  (we hold only the run id after a restart, not the full result object).

## Repo tour

```
server/bside/            the product
  api/app.py             routes, SSE, judge mode, restore
  worker.py              durable queue, crash recovery, regeneration
  stages.py              the seven stages + image evaluator
  kit_providers.py       custom Genblaze providers (cards, audiograms)
  stages_direction.py    LLM direction + verbatim quote anchoring
  design.py              deterministic typography engine (Pillow)
  storage.py / keys.py   B2 planes, fetched-byte verification
web/                     React SPA, hand-rolled design system
server/tests/            unit · integration (live B2, opt-in) · e2e
docs/                    competitive intelligence, ideation, thesis
```
