# ADR 0025 — The Socratic tutor, and podcasts spoken on the Mac

- Status: accepted
- Date: 2026-10-04
- Extends: [0006](0006-codex-app-server-bridge.md), [0009](0009-hosted-personal-workspaces-chatgpt-is-the-model.md), [0023](0023-encyclopedia-and-board-bank.md), [0024](0024-flashcards-and-chosen-tabs.md)

## Context

Two asks. A Socratic tutor that talks: open questions, in ChatGPT's or Claude's voice, that
work through a differential, the treatment options and the knowledge underneath, and assess
how the learner did. And a podcast generator: scripts written from the encyclopedia, spoken
either by OpenAI's text-to-speech or by free, open software.

The second ask runs into a boundary that does not move: Vademecum holds no API key (ADR 0002,
0012). OpenAI's speech service is reached only with one.

## Decisions

### 1. The voice is the host's; the tutor is whoever is talking

In a chat host the assistant is the Socratic tutor. `socratic_start` hands it one page, compiled
from the learner's own sources, with the rules: one open question at a time, never the answer
first, the differential then treatment then knowledge, soundness judged only against the page.
The assistant asks and listens in whatever channel the host offers, voice included, records
each exchange with `socratic_turn`, and closes with `socratic_finish` and the assessment. On the
Mac's own model connection the dashboard runs the same dialogue in text, each answer one turn
with the page and the transcript, and the browser's own dictation and speech stand in for a
voice where the browser offers them. The session is one record either way.

### 2. The assessment is words, and the gaps become flags

The assessment has five fields: the differential, the treatment, the strengths, up to five
gaps named as topics, and a summary. There is no score and no field for one. Each gap becomes
a flag on the page's topic, once, so the Improvement Map draws it and the flashcards weigh it.

### 3. Podcast scripts from pages; audio made on the Mac, by the Mac

A script is one model turn on the Mac's own connection from up to six pages: two hosts, plain
speech, saying only what the pages say, with take-homes. The audio is made on-device with the
Mac's own speech synthesiser, one voice per host, line by line, joined and encoded to a small
AAC file beside the other stored files. No service is called and nothing of the script leaves
the machine to be spoken; higher-quality voices are the ones macOS lets the owner download.
OpenAI's text-to-speech is not offered, because it needs a key. The script syncs; the audio
stays with the Mac that made it, and anywhere else the browser reads the script aloud itself.

## Consequences

- Two more routes may start a model turn, each with a disclosure; the privacy test names them.
  The podcast's render route starts none.
- Two tables: sessions are shared (either node may run one); episodes are Domi's.
- Six MCP tools; a Podcast Generator tab; a Socratic section on the Tutor tab.
