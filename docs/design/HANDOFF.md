# Handoff — Vademecum design workstream

Written 2026-09-17 by a Claude cloud session for a Claude Cowork session running on Nick's Mac.
Read this first, then `DESIGN.md`. Everything you need is in this folder and the repo around it.

## Where things are

| Thing | Path |
| --- | --- |
| Repo (the rewrite) | `/Users/nbrazeau/AgentWorkspaces/Vademecum` |
| Product rules and boundaries | `AGENTS.md`, `README.md`, `docs/adr/0001`–`0007` |
| The app's actual stylesheet | `apps/web/src/styles.css` (506 lines; all tokens live in `:root`) |
| Pages designed so far | `apps/web/src/pages/Today.tsx`, `apps/web/src/pages/Tutor.tsx` |
| Pages not yet designed | `apps/web/src/pages/Sources.tsx`, `ImprovementMap.tsx`, `Model.tsx` |
| Design decisions and open questions | `docs/design/DESIGN.md` |
| Artboard sources (plain HTML + inline styles) | `docs/design/*.dc.html` — `Main.dc.html` is Today on iPhone |
| Generator | `docs/design/src/build.mjs` + `src/shared.css` → `node src/build.mjs` rewrites every `*.dc.html` |
| Canvas layout | `docs/design/canvas.json` |
| Readable previews | `docs/design/previews/*.png` and `vademecum-design-preview.pdf` |
| Hosted, editable canvas | https://claude.ai/artifact/MtjDbUfjuJQStooGzEgCeE (Claude Design; saving and PNG/PDF export enabled) |
| Read-only predecessor — never modify, never import from | `Vademecum_arxive/` |

## What was decided (do not re-ask)

- Scope of the first pass: **Today + Tutor**, iPhone 390 px and desktop 1440 px, **static mockups** (not a clickable prototype).
- Direction: **evolve** the existing paper/ink look. Every value in `styles.css` is reused unchanged. Five tint tokens are proposed (`--accent-tint`, `--accent-line`, `--fail-bg`, `--fail-line`, `--tag-bg`) with dark counterparts.
- Proposed changes are listed in the table in `DESIGN.md`. The one layout departure is a 360 px aside on Today at desktop widths.

## What is waiting on Nick

Four open questions at the end of `DESIGN.md`: the desktop aside, small-caps section labels, whether the graded Tutor state should collapse the question card, and how Sources should differ from the cover sheet. Ask him, or proceed on the recommended answer (keep the aside, keep the labels, don't collapse, denser rows for Sources) and say so.

## Suggested next steps, in order

1. Confirm the previews render as expected on the Mac (`open docs/design/previews/vademecum-design-preview.pdf`). The cloud renders used Linux fallback fonts; on macOS you'll get Georgia and SF.
2. Port `src/shared.css` into `apps/web/src/styles.css`. The nine classes the pages already emit but never styled (`badges`, `support-*`, `badge-held`/`-retracted`/`-corrected`, `tags`/`tag`, `provenance`, `quote`, `prompt`, `disclosure-panel`/`-headline`/`-list`, `attempt`, `point`, `update`) are the low-risk part; the `h2` small-caps change and the desktop grid are the two judgement calls.
3. Run the web suite after any CSS change: `cd apps/web && npm test && npm run typecheck`. `tests/sources.test.ts` fails the build on the forbidden vocabulary ("streak", "due count", "review queue", "daily review", "items due", "missed question", "review session", "flashcard") and on any external origin in `src/`. It scans `apps/web/src/` only, so `docs/design/` is safe.
4. Design **Sources, Improvement Map and Model** the same way: add pieces to `src/build.mjs`, add artboards to `canvas.json`, run `node src/build.mjs`, then re-seed the canvas (ask Claude to "update the Vademecum design canvas from docs/design" — it needs the `design` skill and the canvas URL above).
5. Do **not** touch `Vademecum_arxive/`, and do not read it at runtime; ADR 0001 is explicit.

## Rules that every screen must respect

- Nothing counts down, nothing is owed, no streaks — the vocabulary list above is enforced by a test.
- Every model transmission (Build, Grade) shows what it will send *above* the button that sends it.
- The PHI warning sits beside every free-text field.
- "Evidence-supported, machine reviewed" is the strongest thing the product ever says; never "verified".
- 320 px minimum viewport, 44 px targets, 16 px body, no CDN, no polyfills, iOS Safari 17+ (ADR 0004).
- No fake iOS status bar or keyboard in phone frames; icons are inline stroke SVG, never emoji.

## Kickoff prompt for the desktop session

> Read `docs/design/HANDOFF.md` in the Vademecum folder, then `docs/design/DESIGN.md`. Confirm the previews open, then ask me the four open questions before doing anything else.
