# DSAI Vault

A study site for university courses. Course material is turned into notes, flashcards,
problems and exam prep written as Markdown/YAML files; a Django app validates those files,
loads them into SQLite and renders them. Study progress lives in the browser.

## Two kinds of session: decide which one you are in

**Content session** — "write up lecture 3", "make cards for chapter 5", "add the past paper".

- Read [docs/AUTHORING.md](docs/AUTHORING.md) first, then the page for each content type you
  will write under [docs/content-types/](docs/content-types/).
- Create and edit files **only** under `courses/<course>/content/` and
  `courses/<course>/course.yaml`.
- `courses/<course>/source/` is read-only raw material.
- Do not touch Python, templates, CSS or JavaScript. If the site cannot show something,
  report it; do not change code to make it fit.
- Finish with `uv run python manage.py sync_content <course>` printing no errors.

**App session** — "add a page", "fix the player", "change the theme".

- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first.
- Do not edit course content to work around a bug; fix the code.
- Changing what a content file may contain means changing `ingest/schemas.py`, then running
  `manage.py export_schemas`, then updating the docs and the example course.
- Finish with the tests and `ruff check` clean.

## The live site

The site also runs on a server, at https://dsai3.pages.dev.
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) is the full account; read it before touching
anything under `deploy/`, `edge/`, `.github/` or the `Dockerfile`.

- **Nothing writes to the live database directly.** Not SQL, not the admin, not a copied
  `db.sqlite3`. Its content changes in exactly one way: course files reach the server and
  `sync_content` runs there. Locally the rule is the same, which is why a content session
  only ever writes files.
- **Content goes live with `publish`**, which validates all courses, sends their files to
  the server, and prints the server's own sync report. A content session ends at a clean
  local `sync_content`; it runs `publish` only when the owner asks.
- **Code goes live by merging to `main`**: GitHub Actions tests it and deploys it. Work on
  a branch and open a pull request. Do not push to `main`, merge, or deploy by hand unless
  the owner asks.
- **Server addresses and secrets stay out of the repository.** They live in the local
  `.env`, in GitHub Actions secrets and in the server's own `.env`. Never print, copy or
  commit them, and never write the server's address into a tracked file.
- On the server, a session may look (`deploy status`, `deploy logs`). Changing anything
  there, or in the owner's Cloudflare or GitHub settings, is for the owner to do or to ask
  for explicitly.

## Commands

```
uv sync                                               install dependencies into .venv
uv run python manage.py runserver                     the site, http://127.0.0.1:8000
uv run python manage.py content_status [course]       what exists, what is still unwritten
uv run python manage.py sync_content [course] --check validate course files only
uv run python manage.py sync_content [course]         validate and load into the database
uv run python manage.py sync_content --watch          re-sync whenever a file changes
uv run --env-file .env python manage.py publish       send all course content to the live site
uv run python manage.py startcourse <slug> --title "…" --code X --year "2026-27"
uv run python manage.py export_schemas                regenerate docs/schemas and fields.md
uv run python manage.py test                          all tests
uv run ruff check . && uv run ruff format .           lint and format
```

## Map

```
config/      Django project: settings, root URL config
catalog/     the site: models, views, URLs, templates, template tags
ingest/      files -> database: schemas, Markdown renderer, sync, management commands
templates/   base page and shared fragments
static/      CSS, JavaScript (no build step), vendored libraries, fonts, icons
docs/        AUTHORING.md, ARCHITECTURE.md, DEPLOYMENT.md, content-types/, schemas/, example-course/
courses/     course folders: NOT in git (raw material and written content)
tools/       vendor_assets.py (fetch front-end libraries), katex_check.cjs
deploy/      what the server runs: compose file, proxy config, deploy script, setup scripts
edge/        the Cloudflare Pages function that forwards dsai3.pages.dev to the server
Dockerfile   the production image; .github/workflows/ci.yml tests, builds and deploys it
```

`db.sqlite3` and `media/` are derived from `courses/`; deleting them loses nothing that
`migrate` plus `sync_content` cannot rebuild. The same holds on the server. Progress is in
the browser, not the database.

## Facts worth knowing before changing things

- Ids of cards, questions, formulas, terms and hooks are permanent. Browser progress is keyed
  `<course>/<id>`. Never rename them in content, and never change how keys are built.
- Stored HTML is trusted because `ingest/markdown.py` renders with raw HTML disabled. Keep it
  that way, or templates using `|safe` become an injection risk.
- `docs/example-course/` is both the reference for authors and the fixture for the tests.
  If you change a schema, update it in the same change.
- `docs/schemas/*.json` and `docs/content-types/fields.md` are generated. Do not edit them.
- Maths is typeset in the browser by KaTeX and verified at sync by the same KaTeX build under
  Node (optional: without Node only the structural lint runs).

## Working with the owner

- The owner is learning Django through this project. When you change application code,
  explain the Django concept involved (what it is, why Django does it that way, where it
  lives here). Prefer the idiomatic, standard Django way over a clever one.
- The visual direction is a pastel Material 3 look with a per-course hue. No decorative
  gradients, no glass effects, no emoji as icons. See the design section of ARCHITECTURE.md.
- Do not commit, push, publish or deploy unless asked.
