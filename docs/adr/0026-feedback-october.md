# ADR 0026 — The owner's feedback of 4 October

- Status: accepted
- Date: 2026-10-04
- Extends: [0001](0001-rewrite-preserving-product-patterns.md), [0013](0013-pictures-on-device-reading-and-schematics.md), [0022](0022-case-series-hub.md), [0023](0023-encyclopedia-and-board-bank.md), [0025](0025-socratic-tutor-and-podcasts.md)
- Supersedes: [0016](0016-the-phone-pack.md)

## Decisions

### Tabs and where things live
Today, Tutor, Flashcards, Encyclopedia, Improvement Map, Podcast Generator, Construction,
Sources, Settings — Sources second to last. The Model page, the literature settings, the Case
Series hub and the note on where data lives move into Settings. Construction holds what is
being built (the dissection agent, compiling) and what is held for review. A tab added after
the owner chose their tabs is shown until they choose again.

### Today
Opens on a review dashboard: reviewed today and this week, days in a row, the longest run, the
last two weeks. ADR 0001 refused streaks; the owner asked for one to make reviewing a habit, so
the count is shown, and nothing else of that vocabulary comes with it: no due count, no target,
nothing owed. The page to review can be marked reviewed, which counts. New literature, new cases
and worth-a-look are sections that open and close. Held for review moves to Construction.

### Case Series reach Today
The hub is no longer a tab. A case published in the last three weeks, with its teaching points,
shows on Today until acknowledged; every case can still be browsed in Settings.

### The Tutor
Opens on a home with the two modes and a scorecard (board questions right, the last week,
flashcards, Socratic sessions, weakest and strongest topics), and every mode has Close. The
Socratic tutor gains a hands-free voice mode on the Mac (the browser speaks each question and
listens for the answer) and says how to use ChatGPT's or Claude's own voice.

### Encyclopedia pages are editable, in Markdown, as files
Each page is mirrored to `<source folder>/encyclopedia/<subject>/<title>.md`. An edit in the web
app (on the Mac) or in any editor becomes the page's own Markdown, kept beside the compiled text,
which still feeds questions and cards; when the compiled page changes later the edit stays and
says so. When both changed, the file wins. Subjects open and close.

### Figures come from the owner's material, placed by provenance
A picture kept from a source names its page or slide; a learning point cites the same. Each
paragraph carries the pictures from the pages its points come from, largest first, icons and
banners left out by size, at most two per paragraph and six per page, each with its source
beneath. No model chooses or draws them; schematics are drawn only when the owner asks. Files
carry the figures too, copied into `encyclopedia/_figures/`.

### Strong and weak, and why
The Improvement Map gains a drill-down: specialty, then topic, then the reasons (board questions
right and wrong, flashcards asked for again, open flags including Socratic gaps, exam-report
areas) and the evidence itself, down to the stem of a missed question. Flags group by topic and
open and close. On the cloud copy, the map says that the Mac files flags, instead of offering a
button that cannot work there.

### Podcasts expand from the pages
Episodes are grounded in the pages and expand with the literature reviewed for them, related
pages, case-series points and the hosts' own knowledge, citing sources aloud and saying when they
go beyond the pages. The episode lists its sources, recorded by the server, not by the model.

### The phone pack and the inbox are removed
The phone uses the Cloudflare copy (ADR 0017). The pack, its route and tool, and the `phone/` and
`inbox/` folders are gone.
