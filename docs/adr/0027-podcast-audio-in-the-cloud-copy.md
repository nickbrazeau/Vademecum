# 0027. Podcast audio in the cloud copy, retired once heard

Status: accepted, 2026-10-05

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

## Consequences

The phone plays the Mac's rendered audio while the Mac is asleep. The cloud copy holds
at most five episodes' audio. A listened episode cannot be replayed without rendering it
again on the Mac. Nothing goes anywhere but the owner's own cloud copy.
