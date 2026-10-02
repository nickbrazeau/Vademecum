/**
 * Where the owner's writing actually lives, and what leaves this Mac.
 *
 * This copy used to say no learning content was ever sent. That stopped being
 * true when building material and grading answers landed, and a privacy note
 * that is out of date is worse than none. The claim is now the narrower, true
 * one: transmission happens only on a button press, and each button names
 * exactly what it will send before it sends it.
 */
export function PrivacyNote() {
  return (
    <section className="privacy" aria-labelledby="privacy-heading">
      <h2 id="privacy-heading">Where this is stored</h2>
      <dl>
        <dt>On this Mac</dt>
        <dd>
          Your piles, uploaded sources, notes, generated points and questions, answers and flags are
          saved to a SQLite database and a data directory of your own. You can export it as readable
          JSON or copy it as a backup at any time.
        </dd>

        <dt>Sent to a model</dt>
        <dd>
          <strong>Build and Grade require an explicit action.</strong> Each shows what it will send first.
          <ul>
            <li>
              <strong>Build learning material</strong> sends the selected source excerpts you are
              shown first, with filenames, confidence labels and locations, to OpenAI through Codex.
              Follow-up evidence checks send generated claims and context with retrieved abstracts;
              question checks send the question, reference answer and rubric with selected passages
              and abstracts.
            </li>
            <li>
              <strong>Grade</strong> sends only the question on screen, its reference answer and
              rubric, and the answer you typed.
            </li>
          </ul>
          Codex holds your ChatGPT sign-in and makes the request, so <strong>no API key is used</strong>.
          Checking sign-in and plan usage also goes through Codex, which contacts OpenAI to answer
          it.
        </dd>

        <dt>Sent to PubMed</dt>
        <dd>
          Build derives short public topic searches for PubMed (NCBI). Manual checks and weekly
          checks send the public search words in your watched topics and PubMed record identifiers,
          never source excerpts,
          filenames or learner answers. Weekly checks are off until you opt in and run only while
          Vademecum is running. Reading local pages does not itself request a content model turn.
        </dd>

        <dt>Kept in this browser</dt>
        <dd>
          Only what you have typed and not yet saved. Browser storage is treated as disposable; the
          database on the Mac is the real copy.
        </dd>
      </dl>
    </section>
  )
}
