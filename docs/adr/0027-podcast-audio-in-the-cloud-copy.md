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

- After each sync round the Mac sends the audio of the **ten newest unheard** episodes
  that the cloud copy lacks, over the existing sync token, checked against its SHA-256.
- The cloud copy keeps the file under `attachments/podcasts/`, which the seat already
  mirrors to R2 and restores at boot. Ten episodes are about 60 MB; R2's free tier is
  10 GB and downloads are free.
- **Listening retires the audio everywhere.** An episode marked listened, by playing or
  reading it to the end or by hand, has its audio deleted on the copy where that happened.
  The listen syncs, and the other copy deletes its file too. On the Mac the episode goes
  back to its script and can be rendered again. The script, take-homes and sources stay.
- The cloud copy deletes any audio beyond the ten newest unheard. The seat deletes from
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

### Asking for an episode

- On the Podcast tab, or through `podcast_create` with `pick=request`, the owner says in
  their own words what an episode should be about. The pages sharing the most words with
  the request ground it; when none does, the hosts work from their own knowledge and say
  so once. The script is voiced as soon as it is written, in the default voices.
- **At most ten episodes wait to be heard** at once, on the Mac and in the cloud copy alike.
  An eleventh is refused until one is heard or removed.
- **Progress** is kept in a small file beside the audio, so a render in its own process can
  report it: writing the script fills the first 30% on an estimate (the model reports
  none), voicing fills the rest line by line. One episode is voiced at a time; the next
  waits its turn and says so.

## Consequences

The phone plays the Mac's rendered audio while the Mac is asleep. The cloud copy holds
at most ten episodes' audio. A listened episode cannot be replayed without rendering it
again on the Mac. Nothing goes anywhere but the owner's own cloud copy.
