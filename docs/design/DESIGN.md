# Vademecum — design

Design work for the rewrite lives here. The visual source of truth is a Claude Design canvas;
this folder holds the files that regenerate it, the decisions the canvas encodes, and rendered
previews for reading without a browser.

- **Canvas (editable, exportable to PNG/PDF):** https://claude.ai/artifact/MtjDbUfjuJQStooGzEgCeE
- **Previews:** `previews/*.png` (one per artboard) and `previews/vademecum-design-preview.pdf`.
  Rendered on Linux, so the serif is a Georgia stand-in; proportions and colour are exact.
- **Scope:** all five screens — Today, Tutor, Sources, Improvement Map, Model — plus the ⌘K flag
  dialog and a token sheet, at iPhone (390 px) and desktop (1440 px) widths. Static mockups.
- **Direction:** evolve the existing paper/ink look. Every token value in
  `apps/web/src/styles.css` is used unchanged; the five tints below are the only additions.

## Decisions (2026-09-17)

| Question | Decision | Consequence |
| --- | --- | --- |
| Desktop layout | **One reading column at every width (max 46 rem); accept the scroll.** | Desktop differs only in the header, where the nav sits beside the wordmark. No aside. |
| Section labels | **Small caps** — 12 px sans 600, uppercase, 0.08 em tracking, muted. | Landed in `styles.css` (`h2`). `h3` 17 px, `h4` 12 px small caps. |
| Graded Tutor state | **Collapse the question card** to prompt + support + a read-only copy of the answer. | Landed in `Tutor.tsx` (`collapsed = attempt !== null`). Next question moves below the reference. Refusals and a model outage keep the full card. |
| Sources | **Denser rows** than the cover sheet. | Landed in `Sources.tsx` / `SourceList.tsx`: 44 px pile rows with a one-line metadata strip, a 56 px coverage mini-bar and a chevron; files as compact rows with a type chip. Coverage stays in words ("0 of 25 extracted-text characters processed") — a percentage reads as progress. |
| Improvement Map | **A manipulable graph, Obsidian-style.** | Landed — see "Improvement Map as a graph" below. |
| Footer | "A personal learning workspace, **augmented by AI**. Educational only — …" | Landed in `App.tsx`. |
| PHI warning | "**No patient identifiers or HIPAA material.**" | Landed in `PhiWarning.tsx`. Two test assertions that required the old list of examples (`names, dates of birth, record numbers`) were updated to match; see "Tests" below. |

## What landed in the app

`apps/web/src/styles.css` (506 → 665 lines): five tokens in `:root` and the dark block; `h2`
small caps, `h3`/`h4` sizes; and rules for the classes the pages already emitted but nothing
styled — `prompt`, `prompt-compact`, `answer-given`, `badges`, `support-*`, `badge-held`,
`badge-corrected`, `badge-retracted`, `tags`/`tag`, `provenance`, `quote`, `disclosure-panel`,
`disclosure-headline`, `disclosure-list`, `attempt-self`.

`apps/web/src/pages/Tutor.tsx`: the collapsed question card after an attempt.
`apps/web/src/App.tsx`, `apps/web/src/components/PhiWarning.tsx`: copy.

`apps/web/src/components/TopicGraph.tsx` (new), `pages/ImprovementMap.tsx`, `pages/Sources.tsx`,
`components/SourceList.tsx`, `lib/types.ts`, `lib/normalize.ts`: the graph and the dense rows.
`d3-force` was added to `package.json` — run `npm install` in `apps/web` after pulling.

Suites after the change: web 158 pass, `tsc --noEmit` clean, `vite build` clean (run in a clean
container from the same `package-lock.json`; the Mac's `node_modules` are macOS binaries and
cannot be run from Cowork's Linux shell); backend 878 pass, 1 skipped (run from a throwaway
Python 3.10 venv in the Cowork shell with `--ignore=tests/test_browser_flows.py`). The next
`./scripts/dev.sh` applies migration 0004 on startup.

## Token additions

| Token | Light | Dark | Used for |
| --- | --- | --- | --- |
| `--accent-tint` | `#e6efec` | `#1d2c28` | evidence-supported badge background |
| `--accent-line` | `#b8d3ca` | `#35594f` | evidence-supported badge border; rule beside a graded answer |
| `--fail-bg` | `#f6e3e3` | `#2c1b1b` | retracted badge background |
| `--fail-line` | `#d9a3a3` | `#6b3535` | retracted badge border |
| `--tag-bg` | `#edeae2` | `#23272e` | topic tags |

Contrast (WCAG 2.1, light): ink/paper 15.3, muted/card 5.9, accent/accent-tint 6.4,
warn/warn-bg 6.8, fail/fail-bg 7.4, muted/tag-bg 5.0 — all AA. The existing white-on-`tier-mid`
(3.6) and white-on-`tier-low` (3.7) confidence pills pass only at large-text size and are 12 px.
Still worth a look.

## Improvement Map as a graph — implemented 2026-09-17

`apps/web/src/components/TopicGraph.tsx` draws the map the mock proposed, from real data:

| Encoding | Source of truth |
| --- | --- |
| Node | a flagged topic (default) or any covered topic ("Everything covered") |
| Size | open flags first; addressed flags and learning points add a little |
| Colour | the topic's **specialty**: the owner's assignment (stored), else a whole-string match on the topic's name or a shorthand ("renal" → Nephrology; offered on read, marked `name`, never written), else a dashed grey "no specialty yet". Unfiled flags are muted and never linked |
| Edge | one generated learning point filed under both topics; width = how many (`links[].weight`) |
| Selection | tap or Enter; a panel shows that topic's flags, its links, where its points came from, and a **specialty picker** that records the owner's call |
| Layout | `d3-force`. Topics with a remembered position are pinned there while newcomers settle, then everything is released on a short leash for a few low-energy ticks. Drift is a few percent of the frame, never a re-layout. With no memory, a deterministic ring start |
| Memory | every settled layout and every drag is saved (debounced, one write) to `map_positions`; a topic that leaves the map is dropped from the table |
| Interaction | drag nodes, drag background to pan, wheel or pinch to zoom, − / + / Fit; 44 px hit radius per node |
| Filter | each legend entry is a toggle (`aria-pressed`); switching a specialty off hides its nodes and edges for this view only. Unfiled flags are never hidden. A selected topic that gets filtered out loses its panel |

**Taxonomy.** Migration `0004_map_specialties_and_positions.sql` seeds fourteen specialties — the
set the predecessor settled on (its ADR 0007: the Infographic Atlas's eleven plus Hematology,
General Internal Medicine and Psychiatry), re-entered as seed data, nothing read from the archive
at runtime. Tables: `specialties`, `topic_specialties` (owner assignments only), `map_positions`.
All three are exported. Storage: `storage/map.py`. Routes: `PUT /api/improvement-map/topics/specialty`,
`PUT /api/improvement-map/positions`. The list is a starting taxonomy, not a frozen enum; Spectrum
and Etiology levels are not modelled yet.

**Palette.** Fourteen hues at a similar weight (`--spec-*` in `styles.css`, light and dark), fills
mixed at 16 % into the card colour with `color-mix` (Safari 16.2+, inside ADR 0004). The legend
names only the specialties actually drawn.

Backend: `storage/overview.py` also gained `topic_links()` (topic pairs on the same learning point,
capped at 200) and `topic_clusters()` (dominant pile per topic, kept in the response as `cluster`
for the selected-topic panel's "mostly from …").

Checked in dark mode (`previews/18-ImprovementMap-implemented-dark.png`).

Still open: Spectrum/Etiology beneath specialty, and choosing a specialty at ⌘K capture time.

## Rules the canvas respects

- The forbidden vocabulary in `apps/web/tests/sources.test.ts` appears nowhere in the artboards.
- Nothing counts down or is owed; the cycle position is stated as a fact.
- Every transmission (Grade, Build) is disclosed above the button that causes it; the PHI
  warning sits beside every free-text field.
- No fake iOS status bar or keyboard in the phone frames; no emoji; icons are inline stroke SVG.
- Sample clinical content is illustrative. PMIDs and DOIs are `[sample]` placeholders; paper
  titles are plausible, not citations.

## Tests

`tests/privacy.test.tsx` ("names the identifiers it means") and `tests/quickFlag.test.tsx`
both asserted the warning listed `names, dates of birth, or record numbers`. The new copy
does not, by decision, so both now assert `/hipaa material/i` instead. AGENTS.md boundary 7
still says the product must not solicit those identifiers; the warning simply no longer
enumerates them. If that enumeration was load-bearing for you, revert the copy and the two
assertions together.

## Regenerating the canvas

```sh
node src/build.mjs        # writes every *.dc.html from src/shared.css + src/build.mjs + src/more.mjs
```

The `.dc.html` files plus `canvas.json` are what Claude Design reads. To update the published
canvas, ask Claude in this project to re-seed from this folder. Frame sizes in `canvas.json` were
measured against a headless render and carry ~5 % slack; if an artboard grows, raise its `h`.

## Next

1. `cd apps/web && npm install && npm test` on the Mac, then a real look at the graph with your
   own flags — the sample layouts were checked headlessly at 390 px and 1280 px.
2. Assign specialties as you go — the picker is in the selected-topic panel; name matches are a
   starting point, not a decision.
3. Check on an actual iPhone and desktop Safari — ADR 0004 treats that as a human step. Pinch on
   the graph is the one gesture that only a real device can confirm.
