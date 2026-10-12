# 6. Your data: where it lives and who can read it

Vademecum is software you run for yourself. Nobody else's study material is ever in your copy,
and yours is in nobody else's.

| Where | What is there | Who can read it |
|---|---|---|
| Your computer | Everything: sources, pages, questions, notes, answers | You |
| Your cloud copy (optional, on your Cloudflare account) | What the phone needs: pages, questions, cards, notes, podcast audio | You, signed in with Face ID or your passphrase |
| Its backups (in your Cloudflare bucket) | The cloud copy's records, sealed | Only with the key your computer keeps |
| ChatGPT or Claude | What you or the Tutor send in a conversation | You, under that service's own terms |

## Signing in on the phone

The first time, sign in with your passphrase. Then go to **Settings → Face ID and sessions →
Add Face ID on this device**. After that, the sign-in page leads with **Sign in with Face ID**,
and the passphrase stays underneath as the way back in. **Sign out everywhere**, on the same
page, ends every session at once.

## The backup key

Your computer makes the key that seals your cloud copy's backups, keeps it in the Keychain, and
hands it to your cloud copy. You never see or type it. Your Cloudflare bucket holds only sealed
backups, which cannot be read without the key. If the key were ever lost, your computer would
still have everything, and a new cloud copy is filled again by syncing.

## Never put patient information in Vademecum

Vademecum is for learning. Do not put patient identifiers or anything covered by HIPAA in a
source, a note or a flag.
