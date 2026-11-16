# Applied AI project: a course study assistant (team Fine-Tuners)

Kemal Sivro and Volodymyr Rodin, Applied AI 2026-27. The assistant answers a student's
question from one DSAI course's notes: it retrieves the relevant notes (BM25, a frozen
embedding model, or both) and puts them in the prompt of Qwen3-4B-Instruct-2507. Notation,
answer format and citations are handled in the prompt first; a LoRA fine-tuned copy of
the model is the fallback if that is not enough. The course files it answers from
are `courses/`, the content of the DSAI Vault site this repository was copied from.

| File | What it is |
|---|---|
| [docs/Week_06_Handout_Sivro_Rodin.pdf](docs/Week_06_Handout_Sivro_Rodin.pdf) ([.docx](docs/Week_06_Handout_Sivro_Rodin.docx)) | Week 6 handout, submitted in Week 7: literature matrix, data cards, the dataset opened, the Week 3 required change |
| [docs/data-cards.md](docs/data-cards.md) | The handout's data cards, readable here |
| [notebooks/01_open_vault.ipynb](notebooks/01_open_vault.ipynb) | Opens `courses/`, counts notes, cards and problems per course, shows the data-quality problems, writes the split. Run it from `notebooks/`; PyYAML is the only extra package |
| [docs/held-out-split.yaml](docs/held-out-split.yaml) | Topic-based dev/test split of the 1,091 questions (seed 2026), written by the notebook |
| [data/unanswerable-questions.yaml](data/unanswerable-questions.yaml) | 41 questions the notes deliberately do not answer, or answer the other way |

Everything below this section is the DSAI Vault site's own README.

# DSAI Vault

One site for all your courses: lecture notes, labs, flashcards with honest grading,
practice problems, exam prep (plans, past papers, timed mocks), formula sheets and memory
aids. The content is plain Markdown and YAML written from your course material, usually by
an AI agent following [docs/AUTHORING.md](docs/AUTHORING.md). Django validates and renders
it. You sign in with Google, and your progress follows you between devices.

## This copy: read this first

This repository is a **development copy** of DSAI Vault for a project, not the site that
runs live. What that changes, ahead of everything else in this file:

- **Nothing here deploys.** There is no server, no `publish`, no `take_in`, no
  `deploy status`. Ignore whatever the rest of this file or the docs say about the live
  site, the server, `deploy/`, `edge/`, the educator kit and the owner's bot (BMO):
  those belong to the original repository. There is no CI either: the original's GitHub Actions
  workflow (`.github/`) was removed from this copy, so a push runs nothing. Run the tests and
  `ruff` locally. Links to `docs/DEPLOYMENT.md` and
  `docs/DUTIES.md` lead nowhere here; those pages were left out.
- **The content is part of this repository.** `courses/semester-N/<course>/` holds each
  course's `course.yaml` and `content/`, and is committed here like the code. (In the
  original it is a separate repository.) `source/`, `private/` and `duties.yaml` do not
  exist here, and `.gitignore` keeps them out.
- **No database comes with it.** It is built from the files:
  `uv sync`, `uv run python manage.py migrate`, `uv run python manage.py sync_content`,
  then `uv run python manage.py runserver` and "Development sign-in" on the landing page.
  `db.sqlite3` is yours, local, and never committed.
- **How work goes back to the original:** on a branch here, described in its commits. The
  owner of the original pulls it, reviews it and merges it there. Do not rename files or
  ids to "tidy up": ids of cards, questions, formulas, terms and hooks, and the file names
  of documents, are permanent, because people's progress is keyed to them.
- **Never in `content/`:** graded work, i.e. real quiz or test questions with their
  answers, solutions or expected results of graded lab tasks, homework or project
  answers, or anything made from someone's own hand-ins. The lab policies give 0 points
  for shared work.
- What still applies as written: the app's architecture ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)),
  how content is written ([docs/AUTHORING.md](docs/AUTHORING.md), `docs/content-types/`),
  the tests (`uv run python manage.py test --parallel 4`) and `uv run ruff check .`.

## Run it

You need [uv](https://docs.astral.sh/uv/). Node.js is optional (with it, every formula is
checked by KaTeX when content is loaded).

```
uv sync
uv run python manage.py migrate
uv run python manage.py sync_content
uv run python manage.py runserver
```

Open http://127.0.0.1:8000 and press **Development sign-in** (offered only on your own
machine; no Google client is needed locally). A small example course is included so there
is something to click through.

To open Django's admin, make that development user an administrator once:

```
uv run python manage.py make_admin dev@localhost
```

then visit http://127.0.0.1:8000/admin/. Course content is read-only there on purpose:
it is edited in files. People and their progress can be looked at and managed.

## Add a course

```
uv run python manage.py startcourse rl --title "Reinforcement Learning" --code RL --year "2026-27"
```

This creates `courses/rl/` with a `course.yaml`, a `source/` tree for raw material and an
empty `content/` tree.

1. Put slides, lab sheets, homework and past papers under `courses/rl/source/`.
2. Ask an AI agent to write the content, for example:
   *"Content session for `rl`. Read docs/AUTHORING.md. Write lecture notes and a deck for
   everything in source/lectures that is not written up yet."*
3. It finishes by running `uv run python manage.py sync_content rl`. Refresh the site.

`uv run python manage.py content_status rl` shows what a course contains and which source
files nothing has been written from yet.

## How it fits together

```
courses/<course>/source/     raw PDFs, notebooks            read by the author, never shown
courses/<course>/content/    Markdown + YAML                the only thing authors write
        |  manage.py sync_content   (validates; errors name file, line and field)
        v
db.sqlite3                   Django models                  a cache, safe to delete
        |  views + templates
        v
the site                     + each person's progress, saved to their account
```

- `courses/` is not in git: it holds other people's material and your own notes. If you
  want history for the written content, run `git init` inside `courses/`.
- `db.sqlite3` and `media/` are rebuilt from `courses/` by `migrate` and `sync_content`.
- Progress (grades, ratings, exam attempts, activity) is saved to the signed-in person's
  account. The browser keeps a working copy, so studying still works offline. Unlike the
  course tables, the account and progress tables in `db.sqlite3` are not rebuilt from
  anything.

## Documentation

- [docs/AUTHORING.md](docs/AUTHORING.md): how to write content. Start here for content work.
- [docs/content-types/](docs/content-types/): one page per kind of file, plus the generated
  field reference.
- [docs/example-course/](docs/example-course/): a complete course using every feature.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the application works, for changing it.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): how the live site runs, and how code and content
  reach it.

## Development

```
uv run python manage.py test         tests
uv run ruff check .                  lint
uv run ruff format .                 format
uv run python tools/vendor_assets.py refresh the vendored front-end libraries
```

There is no JavaScript build step: `static/js/` is loaded by the browser as written.

## The live site

The site is served from a VPS at https://dsai3.pages.dev.

```
git push, pull request, merge                      code:    GitHub Actions tests and deploys it
uv run --env-file .env python manage.py publish    content: validated, sent, loaded on the server
```

Course files never go through GitHub, and nothing edits the live database: it is rebuilt
from the files on the server, just as the local one is. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
