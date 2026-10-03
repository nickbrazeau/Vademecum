# To do

Kept here so nothing agreed in conversation is lost. Each item names what it is, what it
costs, and what it waits for. Done items move to the ADR that records them.

## After the system has been stress-tested

- **Rename the Foris address** from `vademecum-seat.brazeau.workers.dev` to a name without
  the old word (for example `vademecum-foris`). Means: a fresh Worker and container
  application, re-entering the three secrets, a first deploy and a second with `PUBLIC_URL`,
  `./scripts/mcp.sh setup sync` on the Mac with the new address, and re-pointing the ChatGPT
  plugin, the Claude connector and the phone's home-screen bookmark. About fifteen minutes of
  work plus three re-pointings by the owner. Records are unaffected: the R2 bucket stays.
- **Store originals as APFS clones** on the Mac, so a 500 GB library is not held twice
  (source folder and records directory). Mac-only; a change in how originals are written.
- **Foris's Sources page** should say plainly that material and full text live on the Mac and
  that only built material and cited passages are here, with no upload or build offered.

## Open product decisions

- **Tutor and source-supported questions.** The Tutor asks only evidence-supported, sound
  questions. With material that PubMed cannot support (cheat sheets, local protocols) most
  questions stay held. Option: let the Tutor also ask source-supported questions, labelled as
  such. One rule change; the owner decides.
- **Licensing and pricing** for other learners; the local install is the product.
- **Packaging**: a wheel with the web app built in, then a signed macOS app bundling Python.
