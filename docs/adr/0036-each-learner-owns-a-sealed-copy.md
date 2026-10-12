# ADR 0036 — Each learner owns their copy: Face ID and sealed backups

- Status: accepted
- Date: 2026-10-11
- Builds on ADR 0012 (a local install each learner owns) and ADR 0017 (the cloud copy).

## Context

The cloud copy's address is public. Its data routes already refuse anyone without a sign-in,
but:

- the only key was one passphrase;
- the backups in R2 were readable by anyone with the Cloudflare account;
- each new learner had a page of manual steps to follow.

The owner asked how this stays private for each learner, and at scale.

## Decision

1. **Everyone runs their own copy.** There is no shared service. Each learner has their own
   computer install and, if they want their phone, their own cloud copy on their own
   Cloudflare account. No machine, bucket or database ever holds two learners' material, so
   keeping learners apart needs no code at all. It scales because nothing central grows.
2. **Face ID on the phone.** The phone signs in with a passkey (WebAuthn).
   - **Adding one:** once, from a session signed in with the passphrase, at `/passkeys`
     (Settings → Face ID and sessions).
   - **The checks:** every ceremony takes a challenge that is used once, lasts five minutes,
     and is checked against this copy's own origin and relying party. A sign count going
     backwards is refused. Failures count towards the passphrase's lockout (5 in 15 minutes).
   - **What is kept:** only the public key.
   - **The passphrase** remains as the way back in. Approving ChatGPT or Claude offers Face
     ID first and keeps the passphrase.
   - **Sessions:** a web session lasts a week, and **Sign out everywhere** ends all of them.
   - **Scripts:** the sign-in pages allow a script only from this address, and only on the
     three pages that ask for a passkey.
3. **Backups sealed with the learner's own key.**
   - **The key:** 32 random bytes, made on the learner's computer, kept in its Keychain, and
     given to their cloud copy as a Worker secret. Nobody types it or sees it.
   - **The format** (`deploy/cloudflare/sealing.py`): AES-256-GCM in 4 MiB chunks. Each
     chunk is bound to its object's name, its position, and whether it is the last.
   - **Unsealed copies:** backups from before sealing are read once, sealed on the next
     round, and refused after a marker records that everything is sealed.
   - **A wrong key, an altered object or a renamed object** makes the copy refuse to start,
     never start empty.
   - **The check:** `GET /api/sync/seat` (sync token) reports how many objects are sealed,
     and the key's fingerprint.
4. **One command.** `./scripts/mcp.sh setup cloud` makes a learner's copy:
   - it signs in, makes a bucket, deploys, and makes every secret;
   - secrets go to Cloudflare on standard input only;
   - the backup key and sync token are kept in the Keychain;
   - it ends by running `scripts/check_cloud.py`.
   - `--adopt` seals an existing copy, and `--rotate` replaces the store key and sync token.
5. **Less on show:**
   - the public `/health` says only "ok";
   - the settings file is readable by its owner only, and the sync token moves to the
     Keychain;
   - a test pins the paths that answer without a session;
   - `scripts/check_cloud.py` probes every API path of a deployed copy from outside.

## What remains (stated, not solved)

- The backup key also sits in Cloudflare's secret store, and a running container holds it
  in memory. Someone with the Cloudflare account and the skill to read a running container
  could read it.
- If the Keychain item and the secret are both lost, the backups cannot be opened. The
  computer still has everything, and a new cloud copy is refilled by sync.
- The backup key is not rotated: every backup is sealed with it. Rotating it would mean
  reading with two keys for a while, and is left for later.
- Wrong guesses lock sign-in for everyone for 15 minutes. That is a nuisance an attacker
  can cause, not a way in.
