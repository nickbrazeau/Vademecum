# Vademecum

*Vade mecum*: "go with me". A personal tutor and learning workspace for clinicians, built
from the material you trust, that fits into the few minutes a day you actually have.

## Why this exists

Vademecum started while I was studying for my medicine boards.

On the wards I was constantly wanting to look something up, and there was rarely time to do
it properly. A question would come up on rounds, I would open a tab on my phone, then another,
and by the end of the week there were dozens of them. They sat there until they were skimmed
or closed. Whatever I meant to learn from them mostly went with them.

At the same time I noticed when I really had room to learn in residency. It wasn't long study
blocks. It was the drive home, a gap between admissions, ten minutes before sign-out. Those
minutes were real, but nothing I had was built for them. A textbook needs a desk. A question
bank needs a session. A tab I opened on Tuesday has no idea what I got wrong on Monday.

Vademecum is meant to fill that gap:

- **Catch the question when it comes up.** A thought on rounds becomes a *knowledge gap flag*
  in two taps (`⌘K` on the Mac), instead of another tab.
- **Learn from what you trust.** Guidelines, lecture slides, review articles and your own notes
  go into a source folder. Each file sits in a pile you rate by how much you trust it.
  Vademecum turns them into an encyclopedia of pages. Every paragraph names the passage it
  rests on.
- **Fit the minutes you have.** Flashcards on a spacing schedule, board-style questions, a
  Socratic tutor you can talk to by voice, and a two-host podcast written from your own pages
  for the drive home.
- **Think, not just recall.** The Socratic tutor asks open questions: the differential, the
  treatment options, the knowledge underneath. Then it names the gaps it heard, and each gap
  becomes a flag.
- **Know where the gaps are.** The Improvement Map models what you know and what is fading,
  topic by topic. It points you to what to study next.
- **Stay current.** Vademecum watches PubMed for the topics and subspecialties you choose, and
  shows new literature on Today with why it matters to you.

It is a learning tool. It is not clinical decision support, and nothing in it replaces
clinical judgement or your institution's guidance.

## How it works

Vademecum is installed on your own Mac. Your records stay there, in
`~/Library/Application Support/Vademecum`, with export and backup built in.

- **No API key, ever.** The model is the assistant you already have: Codex (the ChatGPT app)
  or Claude, on your own plan. On the Mac, Vademecum asks your signed-in assistant to do the
  model work. In ChatGPT or Claude, the assistant in the conversation does it, through
  Vademecum's MCP tools. Vademecum never holds a key and never calls a model provider itself.
- **Checked, not trusted.** Everything a model writes is checked on your Mac before you see it:
  quotes against the source, identifiers, schemas, and how well each claim is supported.
  Anything that fails is held back.
- **On the phone, too.** An optional always-awake copy on Cloudflare syncs with the Mac. You can
  open the web app on your phone, use it from ChatGPT or Claude (voice included), and listen to
  podcast episodes while the Mac is asleep.

## Get started

```sh
git clone https://github.com/nickbrazeau/Vademecum.git
cd Vademecum
./scripts/install.sh
```

The installer sets up Python and the web app, asks where your source folder should be, and
registers Vademecum with Codex and Claude Desktop, whichever you have. It needs macOS 14 or
later and Python 3.12 or later; Node 20 or later is optional.

Then follow the tutorials, also on the [project website](https://nicholasbrazeau.com/Vademecum/):

1. [Install and connect your assistant](docs/tutorials/01-install.md)
2. [Add your first sources](docs/tutorials/02-first-sources.md)
3. [A day with Vademecum: ten minutes at a time](docs/tutorials/03-a-day-with-vademecum.md)
4. [On your phone, and in ChatGPT or Claude](docs/tutorials/04-phone-and-assistants.md)
5. [The Improvement Map and spaced repetition](docs/tutorials/05-improvement-map.md)

## Where things are

| | |
|---|---|
| `apps/api` | The Vademecum server: FastAPI and SQLite, ingest, the encyclopedia, tutoring, sync |
| `apps/web` | The web app (React) |
| `apps/mcp` | The MCP server that ChatGPT and Claude connect to |
| `deploy/cloudflare` | The optional always-awake copy ([how to deploy it](deploy/cloudflare/README.md)) |
| `docs/adr` | Every design decision, with its reasons |
| `docs/technical-overview.md` | The full technical description: privacy boundary, configuration, tests |
| `AGENTS.md` | The product's rules, for anyone (or any coding agent) changing it |

## Roadmap

1. **Architecture.** Done: local install, source folder, encyclopedia, tutors, podcast, phone
   sync.
2. **Pedagogy.** In progress: a learner model behind the Improvement Map, with retrieval
   practice, spacing and interleaving to decide what you see next.
3. **Design.** Making the app calmer, clearer and easier to use day to day.

Work happens in the open. Every change runs the full test suite
([CI](.github/workflows/ci.yml)).

## Licence

[MIT](LICENSE).
