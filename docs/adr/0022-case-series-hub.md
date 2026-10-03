# ADR 0022 — The Case Series hub

- Status: accepted
- Date: 2026-10-03
- Extends: [0007](0007-source-intake-verification-and-public-literature.md), [0015](0015-two-vademecums-that-sync.md), [0018](0018-builds-on-a-timer.md)

## Context

The owner learns from other people's cases: the NEJM's Case Records of the Massachusetts
General Hospital and Clinical Problem-Solving, the Clinical Problem Solvers' episodes, The
Curbsiders' episodes. They are scattered across a journal and two podcast sites, each with
its own feed, and none of them is the owner's own material. The ask: gather them in one
place, link to the originals, write the high-yield teaching points beside each, and credit
whose work it is. Keep the hub updated on its own.

Two boundaries press on this. ADR 0007 made Vademecum's egress one allowlisted host with
one short public phrase; a hub that reads three publishers widens that. And nothing here is
the owner's: a copy of a publisher's article would be theirs, not ours.

## Decisions

### 1. Fixed requests, three hosts, nothing of the owner's

The allowlist in `literature/http.py` grows from one host to three: PubMed, and the two
podcast sites' own domains. Every request the hub sends is the same every time: one PubMed
query naming the journal and the publication type, and one request each to the sites'
public WordPress JSON endpoint naming a category, a page size and a field list. No topic,
flag, passage or answer has a route into any of them. The NEJM's own site is not reached:
PubMed indexes both series, and the DOI prefix (`NEJMcpc`, `NEJMcps`) tells them apart.
Redirects are still refused, so each site's canonical host is the one named.

### 2. A title, a link, a credit, and a bounded public text

An entry is a case's title, its link at the publisher, who made it, when, and the strand of
the series it belongs to. The publisher's public text -- show notes, or nothing for a journal
article PubMed carries no abstract for -- is kept bounded so teaching points can be checked
against it; it is never returned by the API, which shows a short snippet. The entry is
unique by series and publisher id, so a refresh keeps only what is new.

### 3. Teaching points rest on quotes; a title alone yields prompts

Each entry's title and public text go once to the Mac's own model connection (codex or
claude mode; ADR 0006, 0019). The reply is a one-line summary, teaching points each with a
verbatim quote, think-first prompts (the questions the presentation invites before the
answer), a subspecialty from the map's list, and the people the notes name. The server
keeps a teaching point only when its quote is in the text, a specialty only when it is
listed, a name only when the notes contain it. For an NEJM entry there is no text, so there
are no points: the think-first prompts are the study note, and the original is one click
away. There is no field for a diagnosis.

### 4. Off until chosen; Domi fetches, Foris shows

Like the literature sweep (ADR 0007), the hub runs on a timer only once the owner switches
it on, having read the disclosure beside the switch; "Refresh now" is the same consent given
once. It runs only where the sync role is Domi, so the two nodes never gather the same case
twice; the table is Domi-owned and sync carries it to Foris, which shows it and refuses to
fetch. In host mode the fetch still runs and the notes wait, as reports do.

### 5. Credit is part of the page

Every entry names its authors or hosts and its publisher, links to the original, and the
hub says, above the list, that these are the work of others and Vademecum writes its study
notes beside them.

## Consequences

- Three allowlisted hosts instead of one; the provider test pins all three, and the README's
  boundary section and transmission table name what goes where.
- A sixth view in the web app and in `open_vademecum`; the in-chat app's request allowlist
  gains the hub's four routes.
- Teaching points for the NEJM series depend on text the publisher does not offer through
  PubMed. The hub says so by showing prompts rather than points, instead of inventing a
  summary from a title.
