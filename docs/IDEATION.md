# Ideation Protocol — candidates, scoring, adversarial elimination

Scored 0–25 per axis against the same evidence standards as the field audit. **Gates:** Differentiation (distance from all 77) and Demo gravity (a moment a judge remembers after 80 videos).

## Candidates

| # | Candidate (user → pain → why B2+Genblaze irreplaceable) | Util | Prod | B2 | Gz | Diff | Demo | Σ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **B-Side — podcast/talk release-kit engine.** Podcasters spend hours per episode producing transcript, chapters, notes, art, quote cards, captioned clips. Delete B2 → the show archive/system-of-record and restore path collapse; delete Genblaze → the ingest→STT→chat→image→TTS→composite chain, lineage, and step-cache collapse. | 24 | 23 | 24 | 25 | 24 | 24 | **144** |
| 2 | **Campaign canon keeper.** Brand teams lose visual continuity across generated assets; B2 holds canon, Genblaze regenerates drifted assets. | 21 | 21 | 22 | 22 | 6 | 15 | 107 |
| 3 | **TTRPG session chronicler.** Game masters upload session audio; get recap, NPC portraits, campaign codex on B2. | 20 | 21 | 23 | 24 | 24 | 21 | 133 |
| 4 | **Real-estate listing kit.** Agents turn listing photos/facts into flyers, staged variants, narrated walkthroughs archived per-property on B2. | 22 | 20 | 21 | 21 | 14 | 18 | 116 |
| 5 | **Read-along storybooks.** Parents generate personalized children's books with narration + word-sync highlighting, library on B2. | 21 | 18 | 20 | 23 | 22 | 23 | 127 |
| 6 | **Community sports media desk.** Clubs turn match data/commentary into recap graphics + audio bulletins, season archive on B2. | 19 | 19 | 21 | 22 | 23 | 17 | 121 |
| 7 | **Recipe creator kit.** Food bloggers turn a recipe into step cards, plated hero shots, narrated short. | 20 | 20 | 19 | 21 | 20 | 19 | 119 |
| 8 | **Field-notes digitizer for researchers.** Voice memos → structured, illustrated, citable research notes sealed on B2. | 20 | 19 | 21 | 23 | 21 | 16 | 120 |
| 9 | **Local radio/news bulletin factory.** Community stations turn scripts into produced bulletins with music beds/stingers. | 19 | 18 | 20 | 22 | 22 | 17 | 118 |

Kill list: #2 (a Beavous/CANONLOOP reskin — differentiation fails), #4 (Beavous shadow + identity-preservation trap that sank BrandBlaze-class projects), #7/#9 (weaker system-of-record story). Finalists: **#1, #3, #5**.

## Adversarial elimination (arguing as a hostile judge)

**vs #5 Read-along storybooks** — *"Who needs this repeatedly?"* One-off gift usage, not a recurring workflow — utility caps ~21. *"What breaks?"* Child-safety moderation of generated images is a demo-day liability; word-sync narration without paid TTS timing data is fragile. *"Which of the 77 is this?"* None exactly — but 'Un Livre Audio-Visuel' makes AI storybooks feel familiar. **Three unanswered attacks. Eliminated.**

**vs #3 TTRPG chronicler** — *"Who actually needs this?"* Passionate but narrow niche; judges may read it as a toy — utility risk. *"What breaks?"* 3–4 hour session audio = long, costly STT + summarization runs that can't complete live in a demo. *"Reskin?"* No — clean whitespace. **Two unanswered attacks (audience gravity, demo-length physics). Eliminated — but its mechanics (audio → canon archive) fold into #1.**

**vs #1 B-Side** — *"Who needs this?"* Answered: millions of podcasters + every conference speaker; the release grind is named, recurring, and universally recognized; today it takes 4–5 separate tools. *"What breaks?"* Long audio → bounded: chunk-friendly STT, per-step durable jobs, judge demo uses a minutes-long clip while architecture handles hour-scale; provider failure → fallback chains + honest failure UX. *"Reskin of which?"* Closest neighbors are Alias TTS (a TTS **API server** — opposite direction), Cast (voice **localization**), LectureSnap (lectures → study aids). No project ingests creator audio and produces a multi-modal release kit. *"Isn't 'generate images for audio' shallow?"* No — word-level timings drive quote extraction and caption-synced audiograms; the composition is timing-native, not decorative. **Zero unanswered attacks. Selected.**

## Verdict

**Build #1: B-Side.** One sentence: *drop in your episode, get back everything the release needs — transcribed, illustrated, clipped, and sealed to your show's permanent archive on B2.*
