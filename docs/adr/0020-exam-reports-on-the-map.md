# ADR 0020 — Exam reports on the Improvement Map

- Status: accepted
- Date: 2026-10-03
- Extends: [0007](0007-source-intake-verification-and-public-literature.md), [0018](0018-builds-on-a-timer.md)

## Context

The Improvement Map drew only what the learner had flagged and what their learning points
covered. The owner's correction: everything in the piles is an area to review, and the gaps
that matter should rest on a test, not only on what they happened to notice. An in-training
examination report, a licensing step report, a board feedback letter: these say, by content
area, where the learner stands.

## Decisions

### 1. A score report is taken in like a source and read like material

`POST /api/improvement-map/reports` takes a file (PDF, picture, Word, text), detects and
extracts it on this Mac as source intake does, keeps the original under `attachments/reports/`
and the text on the row. Uploading is the explicit act that sends: the dashboard states the
disclosure above the button, and the reply repeats it.

### 2. One model turn reads it into areas, and the server checks every one

The report's text goes to the Mac's own model connection (codex or claude mode; ADR 0006,
0019) inside the usual fence, with the list of subspecialties it may name. It answers with
areas: a topic, a subspecialty id or nothing, a standing of below, at or above, a verbatim
quote, a short note. The server keeps an area only when the quote is really in the report and
drops the subspecialty when the id is not one it listed. In host mode the report waits and
says so; a scheduled run reads waiting reports before it builds.

### 3. The map draws areas, and everything is shown by default

`report_areas` ride with the map. An area is a node; the newest report's standing wins for a
topic; an area below the mark is drawn with a dashed ring, in the open-flags view too. The map
starts with everything shown, since everything in the piles is an area to review.

## Consequences

- A third route may start a model turn, with a disclosure in front of it; the privacy test
  names it.
- Reports and areas sync (domi-owned) and export. A report uploaded on Foris reaches Domi,
  which reads it.
- The map says what a report said, located and quoted. It still judges nothing and assigns
  nothing.
