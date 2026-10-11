# DSAI Vault

A study site for university courses. Course material is turned into notes, flashcards,
problems and exam prep written as Markdown/YAML files; a Django app validates those files,
loads them into SQLite and renders them. People sign in with Google; their study progress
is saved to their account.

## Two kinds of session: decide which one you are in

**Content session** — "write up lecture 3", "make cards for chapter 5", "add the past paper".

- Read [docs/AUTHORING.md](docs/AUTHORING.md) first, then the page for each content type you
  will write under [docs/content-types/](docs/content-types/).
- Create and edit files **only** under the course's own folder: its `content/` and its
  `course.yaml`. The folder is `courses/<course>/`, or `courses/semester-N/<course>/`
  where courses are grouped by semester.
- The course's `source/` is read-only raw material.
- `courses/` is a git repository of its own (the content repository, see
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#course-files-and-the-educator-kit)). Commit there
  only when asked, and never anything under a `source/` folder.
- What an educator hands in is a zip, taken in with `manage.py import_course <zip>`. Never
  unpack one by hand: the command is what checks it.
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

- **Nothing writes to the live database directly.** Not SQL, not a copied `db.sqlite3`.
  Course content changes in exactly one way: course files reach the server and
  `sync_content` runs there. Accounts, progress, requests and applications change only
  by people using the site, and by the owner through the admin (Users, Requests and
  educators, and the switches on the Courses list). Locally the rule is the same, which
  is why a content session only ever writes files.
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
uv run python manage.py export_authoring educator-kit  rebuild the educator kit (then commit and push there)
uv run python manage.py import_course <zip>           take in the course files an educator handed in
uv run python manage.py test                          all tests
uv run ruff check . && uv run ruff format .           lint and format
```

## Map

```
config/      Django project: settings, root URL config
catalog/     the site: models, views, URLs, templates, template tags
progress/    each person's grades and marks, and the endpoint the browser saves them to
accounts/    the pieces around signing in that are ours (the rest is django-allauth),
             which courses each person chose for their dashboard, and what the site has
             already shown them (the tour, the news)
desk/        what people send in (Ask or report, applications to write a course), who
             writes which course (educators), the owner's switches for a course, and the
             message to the owner's bot when something arrives
ingest/      files -> database: schemas, Markdown renderer, sync, management commands
templates/   base page and shared fragments
static/      CSS, JavaScript (no build step), vendored libraries, fonts, icons
docs/        AUTHORING.md, ARCHITECTURE.md, DEPLOYMENT.md, content-types/, schemas/, example-course/
courses/     course folders, grouped as semester-N/<course>/. Not in this repository:
             a private git repository of its own, the owner's (source/ is in neither)
educator-kit/ the checkout of the educator kit's repository: instructions for an AI
             assistant, docs and tools for educators, no course content. Not in this
             repository either; it is generated from ingest/educator_kit/ and docs/
tools/       vendor_assets.py (fetch front-end libraries), make_icons.py (the logo, in
             every size, into static/brand/), make_figure.py (the chart on the landing
             page), katex_check.cjs
deploy/      what the server runs: compose file, proxy config, deploy script, setup scripts
edge/        the Cloudflare Pages function that forwards dsai3.pages.dev to the server
Dockerfile   the production image; .github/workflows/ci.yml tests, builds and deploys it
```

Locally, `db.sqlite3` and `media/` can be deleted: `migrate` plus `sync_content` rebuild
the course content, and the only thing lost is the local test user. On the server the same
file also holds real accounts and their progress, so there it is never deleted.
To sign in locally press "Development sign-in" on the landing page.

## Facts worth knowing before changing things

- Ids of cards, questions, formulas, terms and hooks are permanent, and so are the file
  names of documents. Every person's progress, in their browser and on the server, is
  keyed `<course>/<id>`. Never rename them in content, and never change how keys are built.
- One course folder is one academic year. When a course is taught again, the new year
  is a new folder whose `course.yaml` says `series: <first folder>`; never rewrite a
  finished year in place, because people's progress is keyed to its ids.
- The database holds two kinds of table. `catalog_*` is a cache of the course files and may
  be rebuilt at will. Users, `progress_*`, `accounts_*` (the courses each person chose,
  what they have been shown) and `desk_*` (requests, applications, educators, the
  switches for a course) exist nowhere else: never delete `db.sqlite3` on the server, and
  write a migration for any change to those models.
- Whether students see a course, and whether it asks for an educator, start from its
  `course.yaml` (`enabled`, `has_educator`) and are overridden by the owner's switches in
  the admin. Ask `desk.switches`, never the course row alone, and list courses with
  `catalog.mine.catalogue(request)`, which leaves out those not open.
- What a person sent is theirs. A notification to the owner's bot says that something
  arrived and never who sent it or what it says; an educator sees a request about their
  course without the sender. Keep both that way: the privacy page promises it.
- News for users is a list in `catalog/news.py`. A change people should know about gets an
  entry at the top with the next number; the tour is edited in place, never versioned.
- Every view needs a signed-in person unless it is marked `@login_not_required`. A new page
  is private by default; think before marking one public.
- Stored HTML is trusted because `ingest/markdown.py` renders with raw HTML disabled. Keep it
  that way, or templates using `|safe` become an injection risk.
- Pages are sent with a Content Security Policy (`SECURE_CSP` in `config/settings.py`):
  everything loads from the site itself. An inline `<script>` needs
  `nonce="{{ csp_nonce }}"`, and a font, script or image from another host will not load.
  Vendor it under `static/` instead of widening the policy.
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
