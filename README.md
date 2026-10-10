# DSAI Vault

One site for all your courses: lecture notes, labs, flashcards with honest grading,
practice problems, exam prep (plans, past papers, timed mocks), formula sheets and memory
aids. The content is plain Markdown and YAML written from your course material, usually by
an AI agent following [docs/AUTHORING.md](docs/AUTHORING.md). Django validates and renders
it. Your progress stays in your browser.

## Run it

You need [uv](https://docs.astral.sh/uv/). Node.js is optional (with it, every formula is
checked by KaTeX when content is loaded).

```
uv sync
uv run python manage.py migrate
uv run python manage.py sync_content
uv run python manage.py runserver
```

Open http://127.0.0.1:8000. A small example course is included so there is something to
click through.

To browse the database in Django's admin, create a login once:

```
uv run python manage.py createsuperuser
```

then visit http://127.0.0.1:8000/admin/. The admin is read-only on purpose: content is
edited in files.

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
the site                     + progress in localStorage
```

- `courses/` is not in git: it holds other people's material and your own notes. If you
  want history for the written content, run `git init` inside `courses/`.
- `db.sqlite3` and `media/` are rebuilt from `courses/` by `migrate` and `sync_content`.
- Progress (grades, read marks, exam attempts, activity) is stored in the browser. Move it
  between machines with Export and Import on the Settings page.

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
