# How the application works

For changing the application. If you are writing course content, read
[AUTHORING.md](AUTHORING.md) instead.

This file doubles as a map of Django as it is used here: each section names the Django
concept and the file where you can see it.

## The shape

```
courses/<course>/content/**     Markdown + YAML, written by authors
        |
        |  ingest/      manage.py sync_content
        v
SQLite                          catalog/models/    (a cache of the files)
        |
        |  catalog/     urls.py -> views/ -> templates/
        v
HTML page  +  static/js (Alpine)  +  localStorage (progress)
```

Two Django **apps**, listed in `INSTALLED_APPS` in [config/settings.py](../config/settings.py):

- **`catalog`** owns the database tables and every page.
- **`ingest`** owns no tables. It turns files into `catalog` rows and provides the
  management commands.

The split is the main design decision: content is written as files (so an AI can author it
and a human can read a diff), but served from a database (so pages are simple queries).
The database can be deleted at any time; `migrate` and `sync_content` rebuild it.

## A request, start to finish

Take `GET /courses/mlpr/cards/ch5-fisher/`.

1. **URL configuration.** [config/urls.py](../config/urls.py) includes
   [catalog/urls.py](../catalog/urls.py), where
   `path("courses/<slug:course>/cards/<slug:slug>/", views.deck, name="deck")` matches.
   The `<slug:…>` parts become keyword arguments of the view. The `name` is how the rest of
   the code refers to this address: `reverse("catalog:deck", args=[…])` in Python,
   `{% url 'catalog:deck' … %}` in templates. No URL is written out anywhere else.
2. **View.** [catalog/views/practice.py](../catalog/views/practice.py) `deck()` is a plain
   function: request in, response out. It calls `load_course()`
   ([views/common.py](../catalog/views/common.py)) for the course and the context every
   course page shares, fetches the deck with `get_object_or_404`, and builds the list of
   cards.
3. **Models and the ORM.** `deck.cards.select_related("deck", "topic")` is a lazy
   `QuerySet`: no SQL runs until it is iterated. `select_related` joins the related rows in
   the same query, which avoids one query per card (the "N+1" problem).
4. **Template.** `render(request, "catalog/deck.html", context)` fills
   [deck.html](../catalog/templates/catalog/deck.html), which `{% extends %}`
   [course_base.html](../catalog/templates/catalog/course_base.html), which extends
   [templates/base.html](../templates/base.html). Each level fills `{% block %}`s of the one
   above.
5. **Data for the browser.** `{{ player|json_script:"player-data" }}` prints the cards as
   JSON in an inert `<script>` tag, escaped so that no content can break out of it.
   [static/js/components/flashcards.js](../static/js/components/flashcards.js) reads it.

## Models

[catalog/models/](../catalog/models/) is a package; `__init__.py` re-exports every model so
Django finds them as `catalog.models.X`.

- [base.py](../catalog/models/base.py) has three **abstract** models. They create no table;
  subclasses inherit their fields and `Meta`. `CourseItem` (belongs to a course, has an
  order), `KeyedItem` (addressed by a permanent `key`, unique per course), `SluggedItem`
  (addressed by a file name).
- `related_name="%(class)ss"` on the shared foreign key gives each subclass its own reverse
  accessor: `course.cards`, `course.decks`, `course.questionsets`.
- Uniqueness is enforced by the database with `UniqueConstraint`, not only by the importer.
- Choices are `TextChoices` enums (`Document.Kind.LECTURE`), which gives
  `get_kind_display()` for free.
- Rendered HTML is stored (`body_html`, `front_html`, …). A page view never renders
  Markdown.
- Each model has `get_absolute_url()`: the one place its address is defined.

**Migrations** in [catalog/migrations/](../catalog/migrations/) are generated: after
changing a model run `manage.py makemigrations` then `migrate`. Commit the migration file.

**Admin.** [catalog/admin.py](../catalog/admin.py) registers every model read-only
(`has_change_permission` returns `False`), because an edit there would be overwritten by the
next sync. It is a browser for what the importer produced.

## The ingest pipeline

`manage.py sync_content` → [ingest/sync.py](../ingest/sync.py), per course:

| Step | Module | What it does |
|---|---|---|
| discover | [discover.py](../ingest/discover.py) | Classifies files by path. Computes the content hash used to skip unchanged courses |
| load | [yamlio.py](../ingest/yamlio.py), [load.py](../ingest/load.py) | Parses YAML keeping line numbers; lints quoting, duplicate keys, anchors |
| validate | [schemas.py](../ingest/schemas.py) | Pydantic models, `extra="forbid"`. Errors are rewritten into plain language |
| index | [build.py](../ingest/build.py) `Builder.index` | Records every id and file name; checks uniqueness, topics, exam scopes |
| render | [markdown.py](../ingest/markdown.py), [refs.py](../ingest/refs.py), [assets.py](../ingest/assets.py) | Markdown → HTML, references → URLs via `reverse()`, images → `media/` |
| maths | [mathcheck.py](../ingest/mathcheck.py) | Structural lint, then KaTeX under Node if available |
| write | [store.py](../ingest/store.py) | One `transaction.atomic()`: upsert by natural key, delete what vanished |

Properties worth preserving:

- **Everything before `write` only reads.** All problems are collected in a
  [Report](../ingest/report.py) and none of them touch the database.
- **All or nothing per course.** `store.write` is wrapped in `transaction.atomic`.
- **Primary keys are stable.** Rows are matched by natural key and updated in place, so a
  future table that points at `Card` by foreign key survives re-imports.
- **Error messages are an interface.** They are read by AI agents: file, line, field, what
  is wrong, how to fix it. Tests in
  [ingest/tests/test_errors.py](../ingest/tests/test_errors.py) pin them.

**Management commands** are classes in
[ingest/management/commands/](../ingest/management/commands/). A file `foo.py` with a
`Command(BaseCommand)` class becomes `manage.py foo`.

### Markdown

One renderer for documents and for the Markdown strings inside YAML. markdown-it-py with
plugins for maths, callouts, footnotes, definition lists, task lists and attributes.

- **Raw HTML is off.** This is the security boundary: stored HTML contains only tags the
  renderer produced, which is what makes `{{ html|safe }}` in templates acceptable. If you
  ever enable raw HTML, sanitise the output (for example with `nh3`).
- Maths is not rendered on the server. `$x$` becomes `<span class="math inline">x</span>`
  and the browser typesets it. Fragments are collected so sync can verify them.
- Attributes are an allow-list (`class`, `id`, `width`, `height`, `title`).
- Fuzzy link detection is off (it would link `train.py`); typographic replacement is off
  (it would turn `(c)` into ©).

### Adding a content type

1. Model in `catalog/models/`, then `makemigrations`.
2. Schema in `ingest/schemas.py` (with field descriptions) and an entry in `SCHEMAS`.
3. Where its files live: `ingest/discover.py`. Loading: `ingest/load.py`.
4. Index and rows: `ingest/build.py`. Writing: `ingest/store.py`.
5. If it can be linked to: `ingest/refs.py`.
6. View, URL, template; an entry in `catalog/nav.py`.
7. An example in `docs/example-course/`, tests, a page in `docs/content-types/`,
   then `manage.py export_schemas`.

## Pages

- Views are functions, grouped by area in [catalog/views/](../catalog/views/).
- `load_course()` returns the course and the shared context: navigation, counts and the
  manifest. [catalog/nav.py](../catalog/nav.py) builds those once per sync and caches them
  with Django's cache framework under a key that includes the course's content hash, so a
  new sync simply uses a new key.
- Navigation lists only sections that have content.
- `?scope=<exam>` and `?topic=<id>` narrow the card player, formulas, glossary and hooks;
  see `scope_topics()` in [views/common.py](../catalog/views/common.py).
- [catalog/context_processors.py](../catalog/context_processors.py) adds variables to every
  template (the list of active courses for the rail).
- [catalog/templatetags/ui.py](../catalog/templatetags/ui.py) defines `{% icon %}`,
  `{% topic_chips %}` and small filters.

## Front end

No bundler and no npm project. Third-party files are fetched once by
[tools/vendor_assets.py](../tools/vendor_assets.py) (pinned versions, integrity checked) and
committed under `static/vendor/`, `static/fonts/` and `static/icons.svg`.

**JavaScript** ([static/js/](../static/js/)) is plain ES modules.
[app.js](../static/js/app.js) registers everything with Alpine and starts it.

- `store/progress.js` is the only code that reads or writes progress. One localStorage key,
  `dsai:v1`, holding `records` (keyed `<course>/<id>`: `{s, t, k, n}`), `attempts`,
  `activity`. Every record has a timestamp and cleared grades leave a tombstone, so two
  copies merge by "newest wins". Import does that today; a server sync would do the same.
- `store/timer.js` is the focus timer. It stores when the block *ends*, so it survives
  navigation and reloads.
- `components/` has one module per interactive piece: `flashcards`, `paper`, `palace`,
  `cover`, `nextup`, `settings`, `shell`, and `widgets` (small progress read-outs).
- `math.js` typesets `.math` nodes with KaTeX and adds callout icons. The `x-math` directive
  does the same for HTML inserted later.

A component reads `$store.progress.rev` (directly or through a store method) to re-run when
progress changes.

**CSS** ([static/css/](../static/css/)) uses cascade layers, declared in
[tokens.css](../static/css/tokens.css): `reset, tokens, base, layout, components, prose,
study, pages, utilities`. A later layer wins over an earlier one regardless of selector
specificity; remember that when a rule seems to be ignored.

### Design system

A pastel take on Material 3.

- **Colour is computed.** Every role (`--surface`, `--primary-container`, `--on-surface`, …)
  is an OKLCH colour derived from one custom property, `--hue`. A course's pages set
  `--hue` on `<html>`. An element can carry its own hue with the class `tone` (course cards
  on the dashboard). `light-dark()` supplies the dark theme.
- **Grades have fixed colours** in every course: missed, shaky, solid, key.
- **Depth is tonal.** Surfaces step through `--surface-low … --surface-highest`; shadows are
  kept for things that float.
- **Type:** Google Sans Flex for the interface, Literata for reading, JetBrains Mono for
  code.
- **Shape:** radii from `--r-xs` to `--r-xl`; pills for buttons and navigation.
- **Rules:** no decorative gradients, no blur or glass, no emoji as icons, no colour outside
  the tokens.
- Icons are Material Symbols in one sprite; add a name to `ICONS` in
  `tools/vendor_assets.py` and re-run it.

## Tests

`manage.py test` runs Django's test runner.

- [ingest/tests/](../ingest/tests/): `CourseTestCase` copies the example course to a
  temporary folder; each test breaks the copy in one way and asserts the reported file,
  line and message. Also the Markdown dialect, YAML positions, the commands, and that the
  generated docs are up to date.
- [catalog/tests/](../catalog/tests/): every page renders for the example course; scope
  filtering; search; the source-file view cannot escape its folder; query counts.
- [config/tests.py](../config/tests.py): the production settings refuse to start without
  a secret key and pass `check --deploy`.

`TestCase` wraps each test in a transaction that is rolled back. `SimpleTestCase` is for
tests that need no database.

## Development and production

There is one [config/settings.py](../config/settings.py). It reads environment variables,
and with none set you get local development. The server sets `DJANGO_DEBUG=0` and its own
values; the block at the end of the file, `if not DEBUG:`, is everything that then changes.
[DEPLOYMENT.md](DEPLOYMENT.md) covers the server; the Django side of it is this:

- **WSGI.** `runserver` is a development convenience. In production
  [gunicorn](../Dockerfile) imports `config/wsgi.py` and calls Django for each request.
- **Static files** are part of the code. `collectstatic` gathers them into `STATIC_ROOT`
  when the image is built, and `STORAGES["staticfiles"]`
  ([config/storage.py](../config/storage.py)) gives each a name containing a hash of its
  contents. `{% static 'js/app.js' %}` then prints `…/app.2bee543c4f7e.js`, and the
  `import` lines inside the modules are rewritten the same way. WhiteNoise serves them
  from the Django process. In DEBUG none of this happens and files are served as written.
- **Media files** are data: images that `sync_content` copies out of course content into
  `MEDIA_ROOT`. Django serves them only in DEBUG ([config/urls.py](../config/urls.py)); in
  production the web server in front does.
- **`SECURE_PROXY_SSL_HEADER`.** Django sits behind proxies that end HTTPS and pass plain
  HTTP on. This setting names the header in which they report the original scheme, so
  `request.is_secure()` is right and secure cookies work.
- **`check --deploy`** is Django's own review of exactly these settings. A test
  ([config/tests.py](../config/tests.py)) and CI keep it clean.
- **Tests** always run with development settings, because `config/settings.py` is read
  with no variables set. The two tests that need the production side start a second
  Python process with the server's variables.

## Not built yet, and where it would go

- **Accounts and server-side progress.** A `progress` app with one table,
  `Record(user, key, data, updated_at)`, mirroring the browser store, and one endpoint that
  merges by timestamp. `store/progress.js` would call it after `flush()`. Sign-in with
  faculty Google accounts is planned first: see
  [DEPLOYMENT.md](DEPLOYMENT.md#next-signing-in-with-faculty-accounts).
- **Content Security Policy.** Django's `ContentSecurityPolicyMiddleware`; Alpine then needs
  its CSP build.
- **Files for download.** Source files are served only when `SERVE_SOURCE_FILES` is on
  (debug). For production, object storage (R2) behind signed URLs.
- **Search** is `icontains` over stored text. SQLite FTS5 is the next step.
- **Diagrams and plots** in content are images today.
