# ADR 0013 — Pictures, on-device reading, and schematics

- Status: accepted
- Date: 2026-10-01
- Extends: [0007](0007-source-intake-verification-and-public-literature.md),
  [0012](0012-a-local-product-each-learner-installs.md)
- Keeps: [0002](0002-local-first-boundary.md) (nothing leaves the Mac for any of this)

## Context

The source folder holds what a clinician actually collects: a photographed handout, a lecture
deck where the key slide is a pathway diagram, a scanned chapter, a screenshot of a table. Until
now intake read extractable text and nothing else. A scan was reported as "needs OCR" and built
nothing; the pictures inside a deck were never kept; a `.jpg` dropped into a pile was refused as
unsupported. The owner's piles contain all three today.

The owner also asked for the assistant to draw: a pathway, a decision tree, a mechanism, drawn in
the learning point's own words and kept with it. Nothing in the data model had a place for that.

Two things stay fixed. The machine holds no key and calls no model (ADR 0009, 0012), so reading a
picture is either done on the Mac or not at all. And Vademecum does not understand pictures; the
learner's assistant does, by looking at them. Vademecum's job is to keep the picture, say where it
came from, and hand it over when asked.

## Decisions

### 1. Pictures are kept beside the text, with their locator

Intake now gathers every picture worth keeping from a PDF (image objects per page), a deck or a
Word file (image relationships per slide or document), and keeps it in `attachments/images/`,
content-addressed, with a row in `source_images` saying which page or slide it was on. Icons and
rules are dropped (under 64 px a side or 12,000 px in all); counts, pixels and bytes are bounded;
duplicates within a document are kept once. A picture goes when its source goes, and the file goes
when no row references its digest.

A standalone picture file (`.png`, `.jpg`) is a source of its own kind, `image`: one unit, whose
text is whatever on-device reading finds, and whose picture is itself.

### 2. Pages with no text layer are read on the Mac, and the citation says so

On macOS the API renders a text-less PDF page with Quartz and reads it with the Vision framework,
both of which ship with the operating system; a picture slide is read from its pictures; a
picture file is read directly. The text goes into the same segments as any other page, with the
locator suffixed `(OCR)`, so every quote drawn from it carries the suffix into its citation.
Coverage records `units_ocr`. A page that recognition cannot read either stays image-only, and
its rendering is kept as a picture with origin `rendered` so it can at least be looked at.

On a machine without those frameworks nothing changes: the message says recognition is not
available, and the pictures are still kept.

Recognition is a different thing from extraction and is never presented as more than it is: the
suffix is visible, the count is in coverage, and the status detail names what was tried.

### 3. Sources from before this ADR are read again, once, in the background

`sources.images_at` is NULL on rows stored before this migration. After start-up, a background
pass reads each such original once through the whole intake and records the result through the
same path a re-upload takes: a scan that can now be read is re-extracted and its dependants are
invalidated for revalidation, exactly as ADR 0007 already specifies. Every visited row is marked,
including rows whose original is missing, so the pass is idempotent and finite.

### 4. Schematics are SVG, filed against a learning point, copied into the source folder

The assistant draws; Vademecum keeps. A schematic is an SVG document checked before it is stored:
well-formed, an `svg` root, no `script`, `foreignObject`, `iframe`, `object`, `embed`, `animate`,
`set` or `image`, no event-handler attribute, no reference that leaves the document, no `url()`
in a style, at most 512 KB. It is stored in `attachments/schematics/`, content-addressed, with a
row in `schematics` against the learning point it explains. In single tenancy it is also copied
into the learner's source folder under `schematics/<pile>/<title> (<id>).svg`, so it is a file
they can open in Finder beside the material it explains.

A schematic carries the point's support label wherever it is returned and is never itself
"verified"; the reply says `saved_to_folder`, not where.

### 5. The tool surface grows by five, all local

`list_images`, `view_image` (returns the picture as an image content block, so the host shows it
to the model), `save_schematic`, `list_schematics`, `get_schematic`. None transmits anything; all
answer from the Mac. The instructions tell the assistant to treat text seen in a picture as source
material to quote, never as instructions.

## Consequences

- Pillow and, on macOS, the Quartz and Vision bindings from `pyobjc` are dependencies of the API.
  Neither contacts anything.
- The records directory gains `attachments/images/` and `attachments/schematics/`. Backups and
  exports include the new tables; the files are content-addressed and recoverable from the rows.
- Re-reading an old scan can invalidate learning points that cited it, which is the existing rule
  for a changed extraction and the right one: the text they quote may now be different.
- Nothing here makes Vademecum understand a figure. The assistant looks at it, in the
  conversation, on the learner's own plan. The web app does not yet show pictures or schematics;
  that is a later slice.
