# ADR 0037 — Socratic grading on a rubric, and sessions about a flagged gap

- Status: accepted
- Date: 2026-10-12
- Amends ADR 0025 (the Socratic tutor), which had "no score, no grade".
- Prompted by the owner's feedback of 12 October, after a session graded "6/10" with nothing
  behind the number.

## Decision

1. **A rubric, the same every time.** Differential, knowledge and treatment are each scored
   0–4:
   - 0 incorrect;
   - 1 major errors;
   - 2 partly right;
   - 3 mostly complete;
   - 4 complete and accurate.

   For each, the tutor gives:
   - the learner's own words it rests on;
   - why this score and not the next one up;
   - what a 4 needed.
2. **The server checks it.**
   - A score stands only if its quoted words are found in the learner's answers. A quote may
     be shortened with an ellipsis, and each part must be found.
   - An area that was not asked, or not answered, is not graded and not counted.
   - The total is added up from the graded areas: "3 of 4 across the graded areas". Any
     overall score the model offers is dropped.
   - The learner model uses this total where a session has one.
3. **A session can be about a gap.** At the start, the learner chooses a case from their pages
   or one of their open flags.
   - The flag's own words go to the tutor, who opens with one question that tests exactly
     that gap, grounded on the page for its topic when there is one.
   - The session records the flag (`focus_flag_id`, `focus`; migration 0022).
   - ChatGPT and Claude get the same choice through `socratic_start(flag_id=…)`.
