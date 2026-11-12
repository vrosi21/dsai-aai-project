# Private and unlisted: what only the owner sees, and what is not listed

Every item of a course with a page of its own (a lecture, lab, homework, guide or the cheat
sheet, a deck, a problem set, an exam with its papers, a memory palace) is one of three:

| | Lists, search, counts, review | Its own page, by address | Who |
|---|---|---|---|
| **public** | yes | yes | every signed-in person |
| **unlisted** | no | yes | whoever has the address |
| **private** | no | no: "page not found" | the owner alone |

A card, a question or a stop of a palace is what its deck, set or palace is. A hook written
on a card is seen where the card is. A course's formulas, glossary and the hooks of
`memory/hooks.yaml` belong to the whole course and are always public.

This page is **not in the educator kit**, which is public: nothing about private material
belongs in [AUTHORING.md](AUTHORING.md), [content-types/](content-types/),
[schemas/](schemas/), the example course, or anything else `export_authoring` copies.

## The owner's own material: private/

`courses/semester-N/<course>/private/`, laid out exactly like `content/`:

```
private/lectures/04-my-notes.md        private/decks/my-cards.yaml
private/labs/02-lab-report-notes.md    private/problems/my-set.yaml
private/homework/03-my-answers.md      private/exams/<exam>/assessment.yaml ...
private/guides/defence.md              private/memory/palaces/<palace>.yaml
```

Each file has the format of the same file in `content/` (AUTHORING.md and content-types/).
What is in `private/`:

- **is private, always.** No switch on the site makes it public or unlisted. To share
  something written there, move the file to `content/` deliberately, in the files.
- **never reaches an educator.** A download, a hand-in, the editor and the history take
  `course.yaml` and `content/` only, and a hand-in that brings a `private/` file is refused.
  The site's editor cannot open it either: it is written in the files, like duties.
- **reaches the server only with `publish`**, and is in the private `courses/` repository.
- **may hold graded work**: the owner's lab reports, code and results, answers to graded
  homework or projects, real quiz or test questions with their answers, defence study
  guides. That kind of material goes here and nowhere else on the site: never in
  `content/`, which everyone sees and the course's educators receive. A lab policy gives 0
  points for shared work; private material is not shared.

Rules the sync enforces:

- **A name is used once across `content/` and `private/`**: a private lecture cannot have
  the file name of a lecture in `content/`, nor a private card the id of a card there.
- **`content/` stands on its own.** It is what the educators have, so nothing in it may link
  to private material (`guide:my-notes`, a deck's `lecture:`, an exam's scope): the sync
  says so as an error. Links the other way, from private material to `content/`, are fine.
- **Formulas, glossary, cheat sheet and `memory/hooks.yaml` stay in `content/`**: they are
  one file each for the whole course. Private formulas go in a guide or a deck.
- `uv run python manage.py sync_content <course>` checks `private/` with everything else.

Images: a private page may show images from `private/.../assets/` like any page. Like every
image of the site they are served from `/media/` under a name made from their bytes, so the
address cannot be guessed, but whoever has the exact address can open the image without
signing in. Leave out of private pages any image that must not be seen even then.

## Switching an item: unlisted, private, public again

On the page of any item of `content/`, the owner (a staff account) sees a switch in the bar
at the top: **Public · Unlisted · Private**. Lists mark for the owner what is not public.
What the switch does:

- **Unlisted**: out of every list, search, count, progress figure and the review pool; its
  page still opens from its address, for sharing a link with a few people.
- **Private**: out of the site for everyone but the owner.
- **Public** again: as written.

A switch is the owner's alone: educators cannot set it, see it, or undo it with a hand-in. It
is kept in the database (`desk.Visibility`, also in the admin), not in the files, and
survives every sync and publish. It hides the item on the site only: an item of `content/`
switched to private is still among the course's files, so the course's educators still have
it, as they write it. Material no educator may see is written in `private/` instead.

## For a content session

- Write the owner's own or graded material in `private/`, never in `content/`.
- Ask before moving anything from `private/` to `content/`; never do it for graded work.
- Never link from `content/` to `private/`.
- Finish with `sync_content <course>` printing no errors, as always.
