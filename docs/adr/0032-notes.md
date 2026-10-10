# ADR 0032 — Notes: the owner's own notebooks

- Status: accepted
- Date: 2026-10-10
- Prompted by the owner's feedback of 10 October: "allow users to create their own notebooks with
  hierarchical notes … in markdown format", in the spirit of Joplin but written here.

## Decision

- **One tree.** A shared table, `notes` (migration 0020), holds notebooks and notes. A notebook is a
  note marked as one, and any note can hold notes of its own, up to eight deep. Each note has:
  - a title;
  - Markdown text;
  - its place among its siblings;
  - and an optional "Use as a source" mark.
- **Everywhere.** Notes are written on the Mac or the phone (the cloud copy), and sync both ways like
  answers and flags. The later edit wins.
- **Files.** On the Mac, each note is also a Markdown file at
  `<source folder>/notes/<notebook>/<…>/<title>.md`, with its id in a short front matter. An edit made
  in any editor comes back in, and a note deleted in Vademecum takes its file with it. This is the
  same mechanism the encyclopedia pages use.
- **As a source.** Only a note the owner marks "Use as a source" is copied, plainly, to
  `piles/lowconfidence/My notes/`. There the folder scan reads it in and the background builder (ADR
  0033) builds from it as low-confidence material. Unmarking it removes the copy. Anything already
  built from it stays, as with any source.
- **The web app.** A Notes tab shows the tree beside the note, or above it on a phone. It has:
  - write and read modes;
  - a draft kept until it is saved (⌘S);
  - add inside, move into, and delete;
  - search by title and text.
- **Assistants.** `notes_search`, `note_read` and `note_write` let ChatGPT or Claude file a note from
  a conversation when asked. The in-chat app can read, write and move notes but not delete them,
  as for everything else.

## Consequences

- Notes are the owner's own words, and Vademecum does not check them. A note built as a source is
  rated low confidence, so what is built from it is held to that rating.
- Changing a note marked as a source makes a new version of the source. What was built from the old
  version stays until the owner retires it.
