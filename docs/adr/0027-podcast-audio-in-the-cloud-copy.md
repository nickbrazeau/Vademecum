# ADR 0027 — Podcast audio: natural voices, in the cloud copy, retired once heard

- Status: accepted
- Date: 2026-10-05
- Extends: [0017](0017-the-seat-on-cloudflare.md), [0025](0025-socratic-tutor-and-podcasts.md), [0026](0026-feedback-october.md)

## Context

Podcast audio is rendered on the Mac with on-device voices (ADR 0025). The cloud copy
(Foris, ADR 0017) synced the episode rows but not the files, so on the phone an episode
showed a player that could not load, and the only way to listen was the browser reading
the script aloud. The owner asked for the audio on the phone, with episodes retired and
deleted for space once listened to.

## Decision

- After each sync round the Mac sends the audio of the **five newest unheard** episodes
  that the cloud copy lacks, over the existing sync token, checked against its SHA-256.
- The cloud copy keeps the file under `attachments/podcasts/`, which the seat already
  mirrors to R2 and restores at boot. Five episodes are about 28 MB; R2's free tier is
  10 GB and downloads are free.
- **Listening retires the audio everywhere.** An episode marked listened, by playing or
  reading it to the end or by hand, has its audio deleted on the copy where that happened.
  The listen syncs, and the other copy deletes its file too. On the Mac the episode goes
  back to its script and can be rendered again. The script, take-homes and sources stay.
- The cloud copy deletes any audio beyond the five newest unheard. The seat deletes from
  R2 what left the disk under `attachments/podcasts/` only, and only after its boot restore
  has finished. The Worker refuses deletion anywhere else.
- A copy without the file never shows a player. It says the audio arrives with the
  Mac's next sync, or that it was deleted after listening.

### Voices and text for the ear

- **Kokoro** (an open speech model, Apache 2.0) renders on the Mac through ONNX Runtime,
  with no account, key or service. `scripts/voices.sh` installs the package and fetches
  the model (325 MB) and voice pack (28 MB) once from the project's GitHub release,
  checked against pinned SHA-256 digests. Once present, new episodes default to two of
  its voices, Heart and Michael. The Mac's own `say` voices remain listed and are the
  fallback. Each line is levelled to the same loudness so neither host is quieter.
- **Speakable text.** Before speaking, symbols, units, ranges, dosing shorthand,
  Latin abbreviations and genus initials are written out ("2-4 mg/kg q8h" becomes "2 to 4
  milligrams per kilogram every 8 hours"). This and the next point are lessons from the
  owner's earlier podcast project. There, respelling hundreds of medical words made speech
  choppier, so no respelling list is applied.
- **The script prompt writes for the ear:** acronyms spelled out unless said as letters
  by everyone, no references to figures, short turns with brief reactions.

## Consequences

The phone plays the Mac's rendered audio while the Mac is asleep. The cloud copy holds
at most five episodes' audio. A listened episode cannot be replayed without rendering it
again on the Mac. Nothing goes anywhere but the owner's own cloud copy.
