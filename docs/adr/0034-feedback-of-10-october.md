# ADR 0034 — The owner's feedback of 10 October

- Status: accepted
- Date: 2026-10-10
- Notes and building in the background have their own ADRs: 0032 and 0033.

## Decisions

**Wording**
- "Reading from source…" is the one loading line.
- Case series: "Reviewed it".

**Recall one thing**
- No badge for a topic not yet tried.
- A card answered in the last 12 hours waits until it is due, and coming back to the app brings a
  fresh one.

**Flashcards**
- "Where this comes from" is gone; the link to the card's page stays.

**Literature on Today**
- "Reviewed it" and "Next article". When few papers are left, a check runs and reads further back
  in PubMed once the newest results are all known.
- Thumbs up and down are shared between Mac and phone (`literature_ratings`, migration 0019). They
  rank what Today shows by journal, topic and kind of paper.
- A paper turned down is not shown.
- Journals liked twice are offered as preferred journals.

**Encyclopedia**
- Edit and delete work wherever a page is shown, Today included.
- On the cloud copy a change shows at once and is recorded (`page_changes`, migration 0018). The
  Mac, whose pages they are, applies it between pulling and pushing.

**Improvement Map**
- "Where to go next" starts closed. Each topic offers Board questions, Talk it through and its
  Encyclopedia page.
- Each specialty has its own shape.
- A specialty with three or more topics is folded into one node until tapped.

**Settings**
- Today and Settings read "Always shown", with no switch.
- Case Series lists where cases come from rather than every case.
- A feed of one's own (RSS, podcast or Atom) is added after its one host is named and confirmed.
  Only that host is ever contacted for it.

**Podcast**
- Medical words espeak mispronounced are said from a lexicon of their sounds, spliced into the line:
  - eponyms (Sjögren, Chvostek, Virchow, Behçet);
  - organisms (Coxsackie, jirovecii, Strongyloides);
  - drugs (furosemide, ceftriaxone, fomepizole);
  - terms (syncope, ascites, pruritus);
  - abbreviations said as words (MRSA, HFrEF, CABG).
- The owner adds words of their own, with a sound-alike spelling heard before it is kept.

**Foundation**
- Sources and Construction become one page (ADR 0033).
