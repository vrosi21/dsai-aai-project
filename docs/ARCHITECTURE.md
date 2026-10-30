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
HTML page  +  static/js (Alpine)  +  progress: a copy in the browser,
                                     kept in step with progress/ on the server
```

Five Django **apps** of our own, listed in `INSTALLED_APPS` in
[config/settings.py](../config/settings.py):

- **`catalog`** owns the course tables and every page.
- **`ingest`** owns no tables. It turns files into `catalog` rows and provides the
  management commands.
- **`progress`** owns what a signed-in person has done, and the one endpoint that saves it.
- **`accounts`** is the little that is ours around signing in. The sign-in itself is
  django-allauth's.
- **`desk`** owns what people send to whoever runs the site (requests, applications to
  write a course), who writes which course, what they hand in for it, and the owner's
  switches for a course.

The split between files and database is the main design decision: content is written as
files (so an AI can author it and a human can read a diff), but served from a database (so
pages are simple queries). The `catalog` tables can be emptied at any time; `migrate` and
`sync_content` rebuild them. The user tables and those of `progress`, `accounts` and
`desk` cannot: they are the only data here that does not come from a file.

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

- A `Course` row is one course in **one academic year**. The same course in another
  year is another row, with its own folder, content and progress, tied to the first
  by a shared `series`. [catalog/editions.py](../catalog/editions.py) is the only
  place that reasons about that: `latest()` picks the newest year of each course for
  the dashboard and the navigation, `of()` lists a course's years for the switch at
  the top of its page. Nothing else in the code knows years exist, and adding a year
  is adding a folder (see [content-types/course.md](content-types/course.md)).
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
next sync. It is a browser for what the importer produced. People are the exception:
see [Accounts and progress](#accounts-and-progress). So are the two things about a
course that the owner decides and no file does, which are actions on the Courses list:
see [The desk](#the-desk-requests-educators-and-the-owners-switches).

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
- The dashboard sorts courses into sections by `semester`
  (`dashboard_groups` in [catalog/views/home.py](../catalog/views/home.py)): semesters with
  an active course first, the latest on top, finished semesters after. A course without
  a semester falls back to its year, or to the Archived fold once it is archived.
- `load_course()` returns the course and the shared context: navigation, counts and the
  manifest. It answers 404 for a course the owner has not opened, except to staff. [catalog/nav.py](../catalog/nav.py) builds those once per sync and caches them
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

**The logo** (a D whose inside is a keyhole) is one SVG path in
[tools/make_icons.py](../tools/make_icons.py). That script writes the tab icon and the
bitmap sizes into `static/brand/`, and the bare glyph into
[templates/components/logo.html](../templates/components/logo.html), which the page frame
includes so the mark takes the page's own colour. Change the shape there and re-run it;
do not edit its outputs.

**The landing page** ([catalog/templates/catalog/landing.html](../catalog/templates/catalog/landing.html),
[static/css/landing.css](../static/css/landing.css)) is laid out like the cover of a set
of notes: one statement in the reading typeface, the way in, one figure, and a ruled
list. It loads no JavaScript and is given no course data, so it costs no database
query. Its figure is a real chart, and nothing in it is made up:
[tools/make_figure.py](../tools/make_figure.py) draws Ebbinghaus's measurements of
forgetting (1885; the table is in the script, with its source) and, as dashed lines,
the days on which the site asks for a card again (`INTERVALS` in
[static/js/store/progress.js](../static/js/store/progress.js)). It writes
[templates/components/recall_figure.html](../templates/components/recall_figure.html).
The lines are an SVG stretched to fit; every dot and label is HTML placed over it by
percentage, so text stays readable on a phone. If the schedule changes, change the
copy in the script and re-run it; tests compare the two, and the file with what the
script draws. A picture on this site shows something real or is left out.

**The dashboard** ([catalog/templates/catalog/home.html](../catalog/templates/catalog/home.html))
follows the same layout: a headline in the reading typeface, then what to do (Next up)
beside what was done (the last fourteen days), then one ruled section per semester
with its heading in a column on the left. A course gets a card only if there is
something in it (`has_content` in [catalog/nav.py](../catalog/nav.py)); the others are
named in one line, and are left out of the navigation and of Settings too, because a
card for an empty course is a button that leads nowhere. Cards are plain plates with
a hairline edge; the course's hue appears only on the tag with its code. Hints for
whoever writes content (which folder, which command) are shown to staff and in DEBUG
only.

**The tour** ([templates/components/tour.html](../templates/components/tour.html),
[static/js/components/tour.js](../static/js/components/tour.js)) is a `<dialog>` that is
part of every page. It opens by itself once, the first time a person reaches their
dashboard, and whenever something dispatches a `tour` event, which the dashboard's
"How it works" button and Settings do. Its steps are stacked in one grid cell, which
keeps the dialog the height of the tallest. There is one tour: it is rewritten when the
site changes and always describes the site as it is.

**What a person has been shown** is a fact about the account, not about a browser
([catalog/welcome.py](../catalog/welcome.py), `accounts.Profile`). `welcome(request)`
decides what the dashboard and a course's front page offer unasked: the tour for
someone who has never taken it, otherwise the updates they have not seen, never both.
The moment either dialog opens, the browser posts to `/whats-new/seen/`
([static/js/seen.js](../static/js/seen.js)) with the number of the latest update the
page was built with, so a second device does not offer it again. It is a POST from the
browser and not something the view does while rendering, because showing a page must
not change anything. On the open site, where there is no account, the browser remembers
the tour as before and there is no news.

**What's new** ([catalog/news.py](../catalog/news.py),
[templates/components/news.html](../templates/components/news.html)). Updates are a
list in code, numbered upwards, newest first; announcing something is adding an entry at
the top. A profile stores the number current when the person took the tour
(`tour_update`) and the last number shown to them (`news_seen`). Taking the tour counts
as having seen everything so far. `/whats-new/` lists them all and marks those that came
after the person's tour. People who had an account before profiles existed were given
one by a data migration saying they toured at update 0: they are not walked through the
site again, and are told what changed instead.

**JavaScript** ([static/js/](../static/js/)) is plain ES modules.
[app.js](../static/js/app.js) registers everything with Alpine and starts it.

- `store/progress.js` is the only code that reads or writes progress, and the only code
  that talks to the server about it ([Accounts and progress](#accounts-and-progress)).
  It holds `records` (keyed `<course>/<id>`: `{s, t, k, n}`), `attempts`, `activity` and
  `clears` in one localStorage key per account, plus `prefs`: small settings of that
  browser which are stored alongside but never synced.
- `lib/overview.js` turns a course's manifest and that store into one set of numbers:
  how much of the reading, the cards and the problems is solid, shaky or missed. The
  dashboard's bars and the course page's tiles both come from it.
- `store/timer.js` is the focus timer. It stores when the block *ends*, so it survives
  navigation and reloads.
- `components/` has one module per interactive piece: `flashcards`, `paper`, `palace`,
  `cover`, `nextup`, `settings`, `shell`, `tour`, `news`, and `widgets` (small progress
  read-outs).
- `seen.js` tells the server that the tour or the news was shown.
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
- **Shape:** radii from `--r-xs` to `--r-xl`. What is pressed or typed into (buttons,
  fields, dropdowns, navigation items) is a rounded rectangle, `--r-control`; no pills.
  `--r-full` is for what is round by nature: dots, switches, progress bars. A dropdown
  draws its own arrow, set in from the edge as far as its text is on the other side.
- **Rules:** no decorative gradients, no blur or glass, no emoji as icons, no colour outside
  the tokens.
- Icons are Material Symbols in one sprite; add a name to `ICONS` in
  `tools/vendor_assets.py` and re-run it.
- **Nothing widens a phone's page.** A one-column grid that holds course content says
  `grid-template-columns: minmax(0, 1fr)`: the implicit `auto` column is at least as wide
  as its widest content. What can be wider than a phone scrolls in a box of its own: a
  display formula, a code block, a table (the renderer wraps it in a `.table-box`), and
  an inline formula with nowhere to break (`math.js` marks it `math--wide`). A chip in a
  `.chips` row wraps inside; inline code breaks anywhere. On a phone the top bar drops
  search, the theme switch and the ring's number (base.css).

## Accounts and progress

**Signing in.** `LoginRequiredMiddleware` (in `MIDDLEWARE`, [config/settings.py](../config/settings.py))
turns the usual rule round: every view needs a signed-in person unless it says otherwise
with `@login_not_required`. Three things say otherwise: `catalog.views.home`, which shows
the landing page to a visitor and the dashboard to anyone signed in; django-allauth's own
views; and the progress endpoint, which must answer a script with `401`, not with a
redirect to a web page. An anonymous request anywhere else is sent to `LOGIN_URL` (the
landing page) with `?next=` remembering where it was going.

[django-allauth](https://docs.allauth.org/) does the OAuth exchange with Google and creates
the Django user on first sign-in (`SOCIALACCOUNT_ONLY`: there are no passwords). Its URLs
are mounted under `/accounts/` in [config/urls.py](../config/urls.py). The few pages it
renders itself use [templates/allauth/layouts/base.html](../templates/allauth/layouts/base.html):
a template placed at the same path as one inside a package overrides it, because
`TEMPLATES["DIRS"]` is searched first.

The user model is Django's own `auth.User`, referred to as `settings.AUTH_USER_MODEL`.
Anything more we need to know about a person goes in a table with a foreign key to it, as
`progress` does.

In DEBUG the landing page also offers a development sign-in
([accounts/views.py](../accounts/views.py)), so working locally needs no Google client.

**Progress.** The browser's copy is what pages read and write, so nothing waits for the
network and studying works offline. [static/js/store/progress.js](../static/js/store/progress.js)
remembers which entries the server has not seen (`dirty`) and the number the server gave
it last time (`cursor`). A few seconds after a change, on arriving at a page, and when the
tab is hidden, it POSTs the dirty entries and the cursor to `/api/progress/sync/`.

[progress/sync.py](../progress/sync.py) folds them in inside one transaction and answers
with every row whose `version` is above that cursor, from any device, plus the new cursor.
Each person has a counter ([progress/models.py](../progress/models.py), `Ledger`); every row
changed by a request is stamped with the counter's next value. That makes "what is new
since I last asked" one indexed query.

Both sides apply the same rules, so the order of events never matters:

| | Rule |
|---|---|
| records | the copy graded later wins (`t`, the grading device's clock) |
| attempts | kept if that sitting is not already known |
| activity | per day, the larger count wins |
| clears | "forget everything before this moment", per course or for all; the later moment wins and older rows are dropped |

A clear is how "Clear progress" in Settings reaches a phone that still holds the old
grades: deleting rows would not, the phone would send them straight back.

The endpoint is a plain view returning `JsonResponse`, protected like any form post: the
session cookie says who, and the CSRF token (printed into `<html data-csrf>`, sent as the
`X-CSRFToken` header) says the request came from our own page. The payload is checked
entry by entry with Pydantic; a malformed entry is skipped, never the whole request.

Anyone with a Google account can sign in and post here, so the endpoint also guards the
disk: an account may hold at most `LIMITS` rows of each kind
([progress/sync.py](../progress/sync.py)), far above honest use, and Django refuses a
request body over 2.5 MB before the view sees it (`DATA_UPLOAD_MAX_MEMORY_SIZE`).

**Choosing courses.** A person says which courses they take, and the dashboard, the
list in the navigation and Next up then show those and nothing else. It decides what
is put in front of someone, not what they may open: any course can still be reached
by its address or by searching, and its page has a button to add or remove it.

- The choice is one small table, `Enrollment` in
  [accounts/models.py](../accounts/models.py): a user and a course's `series`, as text.
  Not a foreign key to `catalog.Course`, because those rows are a cache that can be
  rebuilt; and the series rather than the slug, so that next year's edition of a
  course takes its place without anyone choosing again. Operations on the table live
  on its **manager** (`Enrollment.objects.series_of`, `.replace`).
- [catalog/mine.py](../catalog/mine.py) narrows a list of courses to one person's
  (`mine`), asking the database once per request (`chosen`). The dashboard view and
  the context processor both go through it.
- [catalog/views/mine.py](../catalog/views/mine.py) has the page with the tick boxes
  (`/my-courses/`, a plain HTML form, POST then redirect) and the address the button
  on a course posts to. Someone with nothing chosen is sent to that page instead of
  the dashboard, which is what makes it the first thing after the first sign-in.
  Courses they already have progress in come ticked.
- Where the site runs open, with nobody signed in, there is nothing to choose and
  every course is shown.

**People in the admin.** [accounts/admin.py](../accounts/admin.py) replaces Django's own
user admin with a narrower one. The list shows each person with how much they have
graded and when they last studied, computed by correlated subqueries (`Subquery` with
`OuterRef`) because progress rows have no reverse link from the user. The page of one
person sums their progress by course. An administrator can rename a person, switch
them inactive, make them staff, delete them (progress goes along by cascade), and
clear their progress. Clearing calls `progress.sync.forget`, which records a clear
exactly as a device would, so the person's browsers drop their copies too. Nobody is
added by hand, and an administrator cannot demote or delete themselves there.
The link to the admin shows in the navigation for staff only.

With Google sign-in configured, the admin's own login page (user name and password)
is never shown: [config/urls.py](../config/urls.py) wraps it in
`staff_member_required`, which sends a visitor to the landing page instead.

**What can be marked.** Cards, questions, palace stops and formula-sheet rows always had
three grades. Documents now have them too: the reading page asks "Lost, Shaky or
Confident" ([catalog/templates/catalog/document.html](../catalog/templates/catalog/document.html)),
stored as the same record under the key `doc:<kind>:<slug>`. A plan phase stays a yes/no
mark.

## The desk: requests, educators and the owner's switches

[desk/](../desk/) is the app for what reaches the person who runs the site, and for the
people who write courses. Like `progress` and `accounts`, its tables are real data.

**Models** ([desk/models.py](../desk/models.py)). Every one names a course by its
`series`, as text, for the same reasons as `Enrollment`.

- `Request`: something a person sent: content they miss, a fault, an idea. It has a
  status and an `answer`, which the person reads on the page they sent it from.
- `EducatorApplication`: an offer to write a course, with a name, a phone number, the
  year of study and how they would write it. The form takes several courses at once
  (`EducatorApplicationForm.offers` stores one application for each, so each is
  answered on its own), and any open course can be ticked, also one that has an
  educator: a course may have several. One per person and course; sending the form
  again updates it. `decide()` answers it, and accepting is what creates the role.
- `Educator`: the role. This person writes this course.
- `CourseSetting`: the owner's two switches for a course, each with three positions:
  empty ("as the course file says"), yes, no.

**Two questions about a course** ([desk/switches.py](../desk/switches.py)): is it open
to students, and is someone writing it? Each starts from the course's own file
(`enabled`, `has_educator` in `course.yaml`), and the owner's switch, once used, wins.
An accepted educator also counts as someone writing it. The switches are not columns of
`catalog.Course` because that table is rebuilt from files; a test deletes every course
and checks that a switch is still in force afterwards. One `Switches` object per request
answers for every course from two queries.

- A course that is not open is left out of [catalog/mine.py](../catalog/mine.py)'s
  `catalogue(request)`, which the dashboard, the navigation, the page for choosing
  courses and both forms start from; search and Settings filter the same way; and
  `load_course()` answers 404 for it. Staff are the exception there, with a banner:
  that is how the owner reads a course before opening it.
- A course with nothing in it says so on the page of its current year
  ([catalog/templates/catalog/course.html](../catalog/templates/catalog/course.html))
  and offers to ask for it, and, unless someone writes it, to apply to become its
  educator. An earlier year asks for nothing.

**The pages** ([desk/views.py](../desk/views.py), [desk/forms.py](../desk/forms.py)).
`/ask/` and `/educators/apply/` are `ModelForm`s: the form is built from the model, the
view adds what the form cannot know (who is asking, with `save(commit=False)`), and a
successful POST redirects. The form only offers, and so only accepts, the courses this
person can see.

Ask or report is three forms, one per kind of thing (`REQUEST_FORMS`), because the three
need different things asked: content needs a course and what is missing; a fault needs
where, what, and what should be there instead; an idea needs what and what for, and no
course. They share a base class and each adds its own questions. Those questions are form
fields only: `compose()` writes the answers into the one `message` the model keeps, under
a word that says which is which ("Where:", "Should be:"), so a new question is never a
new column. The page shows one form at a time; the three kinds at its top are links to
`?kind=…`, which carry along the page the person came from and its course. A fault seen
on a course's page is about that course without the person saying so (`_course_of`).

Educators has three pages, because one person may write several courses:

- `/educators/` is the **dashboard** of someone who writes courses
  (`desk/educators.html`): what is waiting, most pressing first (`_waiting`: a request to
  answer, a copy the site has moved away from, a course still empty), a card per course,
  and the kit's version. For someone who writes none, the same address explains the role
  and lists the courses nobody writes (`desk/become.html`); `/educators/open/` is that
  list for someone who writes one and may take another.
- `/educators/courses/<series>/` is **one course** (`desk/course.html`): the form that
  takes a hand-in, the course's history, the state of their copy, what was asked, and
  the steps of a session. Its parts have ids (`#hand-in`, `#history`, `#files`,
  `#asked`), so a notification leads straight to the part it is about. They see a
  question and not who asked; they can mark it done with a line of answer.
- `_desks` reads what both need, for all of a person's courses at once: one query for
  the requests, one for the hand-ins. "Your copy" compares `Educator.synced_version`
  with the version the site has now, which is the test a hand-in will meet; the page
  says so before the work instead of after.

**Telling the owner** ([desk/notify.py](../desk/notify.py)). When a request or an
application is stored, a short message goes to the owner's notification bot. The view
queues it with `transaction.on_commit`, so it is sent only once the row is really in the
database, and from a separate thread, so the person never waits for it and never sees it
fail. The message says that something arrived, for which course, and where in the admin
to read it; it never carries a name, a phone number or what was written, which is what
keeps the privacy page true. Failures that may pass (no answer, a 5xx) are tried twice
more; a refusal is final. Without `BMO_NOTIFY_URL` and `BMO_NOTIFY_TOKEN` nothing is
sent, and the address must be https because the token travels in a header.

**The courses' duties** ([desk/duties.py](../desk/duties.py)). Each course may have a
`duties.yaml` beside its `course.yaml`: the owner's own dated labs, tests, exams and the
like ([DUTIES.md](DUTIES.md)). It is private, so it is outside `content/` and travels
nowhere a course travels (downloads, hand-ins, the editor, the history, the kit), and no
page renders it. [ingest/duties.py](../ingest/duties.py) is its format, checked by
`sync_course` like any file of the course but kept out of `ingest/schemas.py`, which the
public kit copies. After a real `sync_content`, `duties.push()` compares every course's
duties with `ToldDuty` (what the bot was last told, one row per duty) and posts what is
new, changed or gone, in batches of at most 50, through `notify.deliver`. A row changes
only once the bot took its batch, so what was not delivered goes with the next sync. It
waits for the answer instead of using a thread: a command ends, and its threads with it.

**In the admin** ([desk/admin.py](../desk/admin.py)). Requests and applications are
listed and answered there; what the person wrote is read-only. Accepting an application
(an action on the list, or the status on its page) calls `decide()`. The switches are
actions on the Courses list ([catalog/admin.py](../catalog/admin.py)), whose columns say
whether students see each course and who writes it; the join by series is done with
`Subquery`, as in the people admin.

**Why an educator is not given the admin.** The admin is one door: `is_staff` opens all
of it, and narrowing it per table and per course is work that would have to be right
every time. More to the point, the course tables are a cache. A row added there is gone
at the next sync, because content only ever arrives as files. So an educator works on
files, on their own computer, in a copy of the **educator kit**: a public repository with
the instructions for an AI assistant, the authoring docs and three tools, and no course
(see [DEPLOYMENT.md](DEPLOYMENT.md#course-files-and-the-educator-kit)).

**A course as one zip** ([ingest/handin.py](../ingest/handin.py)) is how course files
travel. A course's page for its educators gives it as a download (`desk.views.files`: a POST,
because it also notes which version that person now has), the kit packs it again in the
same shape, and the site takes that file back. The module imports no Django, so the kit
runs the very same code. It trusts nothing in a zip: a path that leaves the course
folder, a link, a kind of file a course is not made of, an image carrying a script and
anything oversized are refused.

**What a hand-in may do** (`handin.compare`). Anything: add, change, remove. `compare`
does not forbid; it reports what one version does to another: new files, files that are
gone, lines gained, lines removed or rewritten (the old lines are no longer a
subsequence of the new ones), and the ids that disappear, since students' progress is
stored under them. Two things stand between a slip and the live site:

- *It has to be meant.* A hand-in that takes anything away is first shown back to the
  person, line by line, and taken in only with the box on the form ticked (`confirmed`).
- *It has to be built on what the site has.* The site remembers the version of the course
  each educator last took from it or gave it (`Educator.synced_version`; a version is
  `handin.version_of`, a fingerprint of the files). If the course has changed since,
  taking the zip in would undo someone else's work, so it is refused and the page shows
  how to put the work on top of the latest version. That is why the download is a POST:
  it records the version.

**Taking one in** ([desk/handins.py](../desk/handins.py), `take`). The view
(`desk.views.hand_in`) receives the file in `request.FILES`; the form says
`enctype="multipart/form-data"`, which is what lets a browser send a file. Under a lock
per course (a file made with `O_EXCL`, which works across the server's worker processes),
the zip is checked, unpacked into a folder of its own, and given to `sync_course`, which
validates everything and writes to the database only if nothing is wrong. So one call is
both the check and the going live. A refusal (`Refused`, with a kind and the lines of the
reason) changes nothing and is shown on the page with what to do about it.

An edit made on the site is the same thing without the zip (`handins.change`, see
[The editor](#the-editor-changing-a-course-on-the-site)).

**Nothing is lost.** Each `HandIn` row keeps the whole course as it stood after that step
(`files`) and when the step began (`before`), with the fingerprint of both. The steps of
a course are its history: each can be looked at line by line, downloaded, and brought
back ([The history](#the-history-versions-of-a-course)). An educator can take back
their own newest hand-in, and the owner any, in the admin (`handins.withdraw`): it and
every later one for the course are withdrawn, since each was built on the one before,
and the course is loaded again as it was.

**How versions are kept** ([desk/blobs.py](../desk/blobs.py)). As git does it: the bytes
of a file are one `Blob` row under their SHA-256, and a version of a course is a list of
names with hashes (`{name: sha}`, a JSON column). A file that did not change between two
versions is stored once however many steps name it, which is what makes it affordable to
keep the whole course at every step, images included. `blobs.put(files)` stores a
version and returns its list, `blobs.get(list)` gives it back, and nothing is ever
deleted. All of it is in the database, so a backup of the database is a backup of every
version. `handins.kept(step)` and `handins.before(step)` are the two versions of a step;
the zip an educator or `import_course` downloads is packed from `kept` when it is asked
for. Lists of steps are read with `HandIn.objects.light()`, which leaves those columns
in the database. (Until migration 0007 a step kept a zip, `archive`. The migration
converts the steps there are, each only if it reproduces its recorded version to the
byte; the column stays one release longer as their backup, and `kept` still reads it
for a step that was not converted.)

**Into the owner's git history** ([take_in.py](../ingest/management/commands/take_in.py),
[export_steps.py](../desk/management/commands/export_steps.py)). The steps of a course
are a history on the site; the owner's `courses/` folder is a git repository. `take_in`
joins the two: it asks the server for every step that is live and not merged
(`handins.pending`, printed by `export_steps` as one JSON document: the steps, and each
file's bytes once), and makes each a commit there, with the step's author (a name, and
an address that is not one), its time and its summary. It holds what arrives to the same
rules as a zip: only a course's files (`handin.check`), each step whole (its files give
the version it claims), the newest valid under the full check, the owner's files at the
version the steps began from, and nothing uncommitted in the folder. The last commit
carries `.merged`, so the next `publish` settles the steps as it would after
`import_course`.

**Where the site reads a course from** ([ingest/live.py](../ingest/live.py)). The owner's
course files are a folder the application cannot write to, replaced as a whole by
`publish`. A hand-in therefore never touches them. `live.folders()` gives the folder to
read each course from: the owner's, unless there are hand-ins on the site that the
owner's files do not include yet, in which case it is the newest hand-in, unpacked under
`settings.HANDINS_DIR`. `ingest` does not know about hand-ins; `desk` registers
`handins.overlay` with it when Django starts (`DeskConfig.ready`). A real sync also
settles: the owner's files include a hand-in when they say so (`import_course` writes
the hand-in's version into a file, `.merged`, which `publish` sends along), or when they
are the very same files. Then it is marked merged and its folder removed. If the owner
publishes without it, the site keeps showing the hand-in and the sync report says so.
The unpacked folder is a working copy, rebuilt from the database when missing.

**Notifications** ([desk/notices.py](../desk/notices.py), `desk.Notification`). The bell at
the top of every page lists what the site has to tell a person: an answer to their
request, a decision on their application, a change to a course they write, a new version
of the kit; the owner gets theirs there too, beside the message to the bot.
`notices.tell(users, title, body, url)` leaves one row per person, at the place where
the thing happens. The context processor hands the newest few to every page lazily, so
it is one query, and only for someone signed in. Opening the bell posts to
`/notifications/read/` ([bell.js](../static/js/components/bell.js)), which is what makes
the number go away on every device. A notification says what happened and where to
look, never who sent something or what they wrote. When someone asks for content of a
course or reports a fault in it, the people who write that course are told this way
(and nobody is told of what they sent themselves).

**Announcements** (`desk.Announcement`, in the admin). Something the owner writes: a
title, a few lines, an optional link. Who it is for is two choices, students and
educators or educators only, and optionally one course (`notices.audience_of`): with a
course it reaches those who have it on their dashboard (`accounts.Enrollment`) and those
who write it, or, for educators only, those who write it. Adding one is
announcing it: `notices.announce` leaves a copy under each person's bell, in one
`bulk_create`. Each copy points back at the announcement (a nullable foreign key with
`CASCADE`), and that is what makes one correctable: saving a change in the admin updates
every copy (`notices.correct`), and deleting it lets the database delete the copies with
it. `ModelAdmin.save_model` is the hook that does both. The link is checked by a
validator on the model: a page of this site or an https address, nothing else, so what
is typed there can never be a script under somebody's bell. People who join later do not
get earlier announcements.

**Kit versions and telling educators** ([desk/kit.py](../desk/kit.py)). The versions of
the kit are a list in code, written into the kit as `VERSION` and `CHANGELOG.md`. A
version is announced by adding a row under Kit announcements in the admin. That is a
step of its own because the site learns the number at deploy, which can be before the
kit's repository has it. An announcement puts a notice on each educator's page until
they dismiss it (`Profile.kit_seen`) and e-mails them.

**E-mail** ([desk/mail.py](../desk/mail.py)) is the same news by another road, for
when a mail server is configured; without one the bell is the only channel. It goes to
educators only, about their role.
`settings.MAILERS` names where mail goes, as `DATABASES` names where data goes: a real
server once `DSAI_EMAIL_HOST` is set, the terminal in development, nowhere otherwise.
One message per person, sent from a thread, and a failure is a log line.

**The educator tour** ([desk/templates/desk/educator_tour.html](../desk/templates/desk/educator_tour.html))
is a second dialog on the parts of the first (`tour(offer, 'educator')` in
[tour.js](../static/js/components/tour.js)). It is offered once, the first time someone
arrives after being given the role (`welcome()["educator_tour"]`), before any news, and
stays in Settings beside the first tour. [desk/templates/desk/guide.html](../desk/templates/desk/guide.html)
is the long form: the kit's README told from the site's side.

## The editor: changing a course on the site

[editor/](../editor/) lets the people who write a course change it in the browser: the
same people as a hand-in (`desk.access.may_write`: its educators, and staff), all
addresses under `/edit/<course>/`. It keeps nothing of a course itself (its one model
holds images that wait to be saved, see [Images](#images)). An edit is a hand-in that
the site makes itself, so everything a hand-in is held to holds here, and the owner's course
files are still never written by the application.

```
form in the browser -> editor view -> the new text of ONE file -> desk.handins.change()
                                                                    lock, checks, sync,
                                                                    a step in the history
```

**Reading.** The database holds a course rendered, which is no use to someone who wants
to change the text. [editor/source.py](../editor/source.py) reads the file itself from
`ingest.live.folder(slug)`: `load_page` splits a Markdown page into its front matter (a
dict) and its body, `fingerprint` is the SHA-256 of the file as loaded, which the form
carries along (`seen`).

**The form.** [editor/forms.py](../editor/forms.py) `PageForm` draws a page's front
matter field by field and its text in one box. Which fields a kind of page is asked for
is `SHOWN`; what a file holds beyond those is kept. What the fields may contain is not
decided there: `clean()` builds the front matter and gives it to
`ingest.schemas.DocMeta`, the model the importer checks a file against, so a field's
error is the importer's own message. `Rows` is a widget for a list of lines (takeaways,
sources): one box each, all under one name, read back with `getlist`.

**Writing.** `source.compose` makes the file's new text and leaves alone what was not
changed: a page whose front matter was not touched keeps it byte for byte (comments, key
order, `[a, b]` lists), and saving without a change gives the same file, which is
reported as nothing to save. Front matter that did change is written by
[ingest/yamledit.py](../ingest/yamledit.py), the opposite direction of `yamlio.py`: one
canonical layout, and anything with a line break or a backslash as a literal block, where
LaTeX cannot be damaged. A test reads back every YAML file of the example course through
it.

**Saving** is `desk.handins.change(user, course, edit)`. `edit` is a function that is
given the course's files as they are at that moment, under the course's lock, and
returns them as they should be; `source.save` passes one that checks the fingerprint
first (`Refused("conflict")` if the file is no longer what the form was filled from),
then replaces or removes the one file. `change` then does what `take` does for a zip:
the file rules (`ingest.handin.check`), the owner's fields of `course.yaml` (skipped for
staff, who act for the owner), `sync_course` on a staged copy, a `HandIn` row with
`via="site"`. It differs in two places, both because the edit is made from the site's
own version by hand: there is no "built on your copy" test, and no second confirmation
for what it removes.

**Saves and steps.** Someone editing saves often. Saves by one person within
`handins.SESSION` (30 minutes) of each other extend one `HandIn` (`saves`, `updated_at`,
and its summary recomputed against the version the step began from), so the history
reads as sittings, and others are told once per step. A save that removes a file always
begins a step, so the one before it still holds that file. A step made on the site is
taken back like any other. A step keeps only where it ended, so each save also leaves a
line in the step's `trail`: when, and which files it changed, by hash. The bytes are in
the blob store either way; the trail is what makes a text that a later save of the same
sitting replaced findable again.

### Everything in YAML: forms made from the schemas

Cards, questions, formulas, terms, hooks, palaces, an exam's page and the course's own
file are YAML, and what each may contain is written down once, as the Pydantic models of
[ingest/schemas.py](../ingest/schemas.py). Their forms are made from those models, so
there is no form class per content type and no second set of rules.

**A schema as a form** ([editor/schema.py](../editor/schema.py)).

- `describe(model, course)` walks a model's fields and gives each a kind: one line,
  Markdown, an id, a number, a choice (a `Literal`), a topic or lecture of the course to
  pick, lines of links, a group (a nested model), a list (a list of nested models, as
  deep as the content goes: a plan has phases that have tasks). Labels and the help
  under a field are the schema's own descriptions.
- These are not Django `Form`s: Django's forms are flat, content is nested. The nesting
  is in the names of the inputs (`steps-0-do`, `plan-phases-1-tasks-0-text`), and
  `parse(nodes, request.POST)` reads them back into the dicts and lists that would stand
  in the file. Entries of a list are taken in the order they stand on the page, so
  moving one in the browser is moving its element; what is empty or the schema's default
  is left out, and a number or a date becomes one.
- `validate(model, data)` is the model itself, and its errors come back keyed by place
  (`("steps", 1, "do")`), in the importer's words (`schemas.explain`). `bind` lays values
  and errors over the fields for [nodes.html](../editor/templates/editor/nodes.html),
  which includes itself for a group and `row.html` for each entry of a list.
- Some fields apply to some items only: the choices of a multiple-choice question, the
  steps of a derivation. `SWITCHES` names the choice that decides (`type` for a
  question; for a card a choice that is not written anywhere, since a card's kind
  follows from the fields it has). The page shows and hides with `x-show`; `parse` does
  not read what is hidden, so turning a card into another kind takes the old fields out.

**One piece of a file** ([ingest/yamledit.py](../ingest/yamledit.py), second half). A
deck of forty cards is one file, and changing one card must change that card's lines and
nothing else. `yaml.compose` says where every part of a file sits, so `replace_item`,
`add_item`, `remove_item` and `set_keys` work on lines of text: the rest of the file
keeps its comments and layout byte for byte, and the history shows what really changed.
None of them is trusted: `changed()` reads the result back and compares it with the data
that was meant, and writes the file whole (correct, without its comments) if they
differ.

**Kinds of content** ([editor/items.py](../editor/items.py)). Most YAML files are a few
settings and a list. A `Shelf` describes one such kind (decks, problem sets, papers, the
sections of the formula sheet, the glossary, the hooks): the file or folder, the key of
the list, the schema of an item. `save_item`, `remove_item`, `save_keys` and `create`
are functions from the file's text to its new text, run through `handins.change` under
the course's lock on the file as it is at that moment. An item's form carries the
fingerprint of that item's data, not of the file: two people on two cards of one deck do
not collide, two on the same card do. Whether anything changed at all is asked of the
schema (`same`, `_meaning`): `kind: phrase` written out and left to its default are the
same item, so saving a form untouched writes nothing.

**The pages.** [editor/shelves.py](../editor/shelves.py) is one set of views for every
shelf (the list of decks, one deck with its cards, a card, a deck's settings, a new deck
with its first card, removing either); the URL says which shelf, as an extra argument
from [editor/urls.py](../editor/urls.py) (`shelf_urls`).
[editor/whole.py](../editor/whole.py) has the files that are one form each: an exam's
page (with its papers, which are a shelf inside the exam's folder), a palace, and
`course.yaml` in two halves, the description and topics for the people who write the
course and the rest for staff. All of them draw
[item.html](../editor/templates/editor/item.html); its preview column shows every
Markdown field of the form as the site would render it (`POST …/preview/fields/`).

### Images

An image can be pasted into a text box of the editor, dropped on it, or chosen with the
Image button ([editor.js](../static/js/components/editor.js), `place`). What the course
keeps of it is decided on the server, in [editor/images.py](../editor/images.py), with
[Pillow](https://pillow.readthedocs.io/):

- **Checked**: at most 8 MB as sent and 40 megapixels, a JPEG, PNG, WebP or GIF that
  really decodes (Pillow is kept to those four formats), and not an animation. A
  claim of being larger than it is ("decompression bomb") is refused before decoding.
- **Turned and cleaned**: a phone stores a portrait photo lying down with a note to turn
  it. It is turned for real, and then nothing but the pixels and the colour profile is
  written out: no EXIF, so no position, time or phone model travels with a photo.
- **Scaled and encoded**: 1600 pixels on the longer side at most, as WebP, twice: for
  photographs (quality 82) and exactly (lossless). The smaller one is kept, which is the
  exact one for a screenshot or a drawing and the other for a photo.
- **Named by its bytes**: `<first 16 hex digits of its SHA-256>.webp`, so the same image
  is the same file however often it is uploaded.
- An **SVG** is kept as it is, if it is small and carries no script (the rule a hand-in
  already holds an SVG to, `ingest.handin.SVG_ACTIVE`).

**Kept aside, then part of the course.** When the picture is chosen the form is not
saved yet, and an image belongs to a course only once some text shows it. So it waits in
`editor.Upload` (the app's one model), and the form gets back the line of Markdown that
shows it, `![…](assets/<name>)`. The preview can already show it: `source.PreviewAssets`
tells the renderer that the file is known although it is not in the folder
(`Assets.knows`, which a sync always answers with no) and gives it the address of
`views.image`, which serves an upload to the people who write that course. On a save,
`images.attach` looks at the file being written, and adds the uploads it refers to into
the `assets/` folder beside it, where the format wants an image. From there it is a file
of the course like any other: in the step's history, in the blob store once, copied to
`MEDIA_ROOT` by the sync. An upload nothing came to refer to is deleted after a week;
one kept for another course is never taken.

The two addresses that take or give a file begin with `/edit/image/`, ahead of the
course, so that `BODY_LIMITS` (9 MB there, for someone signed in) and `RATE_LIMITS` (30
a minute) can name them.

### Another year of a course

A course is written once per academic year; each year is a course of its own (its own
address, content and progress) in one `series`. Which years there can be is not listed
anywhere: [catalog/editions.py](../catalog/editions.py) works it out. `current_year()`
is the year today falls in (it turns in `settings.ACADEMIC_YEAR_STARTS`, October), and
`years()` runs from `settings.FIRST_ACADEMIC_YEAR` to it, so each October there is one
more. `missing(course)` are those a course has no edition for, and the year switch at the
top of a course's page offers **Add a year** to the people who write it when there are
any.

**Adding one** (`editor.whole.years`) makes a year that begins on the site. No folder
among the owner's course files stands behind it, and the application never writes one.
It is `desk.handins.begin`: the first step of a new course's history, with nothing
before it (`base_version` empty). Its `course.yaml` is made from the year it was added
beside (title, code, topics, settings; the year and the `series` set), and its content
is empty or a copy of another year's. `handins.overlay` shows a course whose first step
on the site has no base although the owner has no folder for it; any other course
without an owner's folder stays gone, as before. Taking that first step back takes the
year back.

**The owner's files.** `import_course` used to refuse a zip of a course it did not know.
It now takes one whose `course.yaml` names, as its `series`, a course the owner has, and
makes the folder beside that course (so in the same `semester-N`). After the next
`publish` the site reads the year from there and lets go of its own copy, as with any
hand-in.

**Which year is the course.** `editions.latest` keeps one course per series for the
dashboard and the navigation: the newest year *that has something in it*
(`Course.has_content`, noted by the sync), or the newest if none has. A year someone has
just added therefore does not hide last year's notes.

### The history: versions of a course

`/edit/<course>/history/` and the pages under it, for the same people as the editor
([editor/history.py](../editor/history.py), the views at the end of
[editor/views.py](../editor/views.py)).

- **The steps**, newest first, and **one step**: what it changed in each file, line by
  line. `history.compare(before, after)` is `difflib.SequenceMatcher` over the lines of
  each file that differs, grouped into hunks with three lines of context, capped per
  file and per page. An image is said to be replaced, not shown.
- **Bring this version back** (`views.restore`) makes the course what it was after a
  step. It is `handins.change` with a function that returns that version, so it passes
  every check, is a new step built on what is there now, and can itself be taken back.
  Nothing is ever removed from the history. A version whose `course.yaml` differs in
  what is the owner's can be brought back by staff only.
- **Every version of one file** (`history.versions`): as it was before anything changed
  on the site, after each step, and after each save within a step, read from `before`,
  `files` and `trail`. One version can be read, compared with the file as it is now, and
  put back, which is an ordinary save of that one file. A hash opens a file only if this
  file of this course really had that version: the list is the access check.

**Reading one course cheaply.** `live.folder(slug)` is asked on every page of the
editor. `handins.overlay` answers it from the folder already unpacked for the newest
step (its `.handin` marker says which step and version), with one query that leaves the
archives in the database; `live.folders()`, which a sync asks, stays exact.

**The preview** (`POST /edit/<course>/preview/`) renders the text with
`ingest.markdown.Renderer`, the importer's own, and returns the HTML with whatever the
renderer objected to, line by line. Links are resolved against an index of the course
that `source.resolver` keeps per process until the course's `content_hash` changes.
Nothing is stored. The browser typesets the maths with the KaTeX it already has
([editor.js](../static/js/components/editor.js) uses the `x-math` directive of
[math.js](../static/js/math.js)) and lists what KaTeX refuses beside the server's
objections. The same component is the toolbar (buttons that write Markdown and LaTeX
around the selection) and the question before leaving with unsaved text.

**Where it shows.** `load_course` puts `can_edit` and `editing` in the context of every
course page. `catalog/course_base.html` draws an Edit switch for those who may write the
course; it posts to `editor:switch`, which keeps the choice in the session. Switched on,
a page fills the `editing` block with what it offers (`Edit this page`, `New lecture`).

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
- [progress/tests/](../progress/tests/): the four merge rules, played as one or two
  devices against `sync()`; the endpoint's 401, CSRF check and query count.
- [accounts/tests/](../accounts/tests/): who is sent to the landing page, the sign-in flow
  is reachable signed out, the development sign-in is closed outside DEBUG, deleting an
  account takes its progress with it.

- The educator kit is exported to a temporary folder and its tools are run as separate
  Python processes, the way an educator runs them, in a real git repository: unpack,
  stash, unpack the newer version, pop.
- [desk/tests/test_handins.py](../desk/tests/test_handins.py): what one version does
  to another; additions going live; a removal shown first and taken in once confirmed; a
  stale copy, invalid content and a changed course code each refused with nothing
  changed; every step downloadable and an earlier one brought back; taking a hand-in
  back; a publish with and without the hand-in.
- [desk/tests/](../desk/tests/): asking, applying, the educator's page, the switches
  and a closed course, the admin actions, and that a notification never carries what a
  person wrote. The bot is never contacted: the one function that opens a connection is
  replaced. Code queued with `transaction.on_commit` does not run inside a test's
  transaction, so those tests use `captureOnCommitCallbacks(execute=True)`.
- [editor/tests/](../editor/tests/): who gets in; a form filled from a file and saved
  untouched changes nothing; a change to the text leaves the front matter's bytes alone
  and the reverse; a new page and its address; removing, and not removing what is linked
  to; two people on one page; the preview's HTML and line numbers; the owner told once
  per step. What an edit may do to a course is tested with the hand-ins
  (`EditedOnTheSiteTests` in desk/tests/test_handins.py), and the writer in
  [ingest/tests/test_yamledit.py](../ingest/tests/test_yamledit.py).
  [test_history.py](../editor/tests/test_history.py): the comparison itself; a version
  brought back as a new step; a removed page and a text replaced within one sitting
  found and put back; that a hash alone opens nothing.
  [test_items.py](../editor/tests/test_items.py) and
  [test_whole.py](../editor/tests/test_whole.py): a schema read as a form and a form
  read back as data; every item and every file of the example course sent back
  untouched changes nothing; one card changed leaves the rest of its file byte for
  byte; a new deck, exam, paper, palace; what the schema and the course refuse.
  [test_images.py](../editor/tests/test_images.py): a photo made as a phone writes one
  (on its side, with a position) comes out upright, as WebP, without the position; what
  is not a picture is refused; an upload joins the course with the text that shows it.
  [test_years.py](../editor/tests/test_years.py): the year turns in October; a year
  added empty and as a copy; a full sync keeps it; `import_course` makes its folder.
- [desk/tests/test_take_in.py](../desk/tests/test_take_in.py): in a courses folder
  that is a git repository, three steps by two people become three commits with their
  authors and dates; a course the owner changed, uncommitted work, a step that did not
  arrive whole and a path outside the course are refused; the SSH command line.
  How versions are kept
  (`KeptTests`) and that converted steps are the same course to the byte
  (`ConvertedTests`, which runs the migration's function) are with the hand-ins.
- [catalog/tests/test_welcome.py](../catalog/tests/test_welcome.py): who is offered the
  tour, who the news, and that each is offered once per account.

A test that needs a signed-in person calls `self.client.force_login(user)`, which puts a
session for that user into the test client without going through a sign-in page.

`TestCase` wraps each test in a transaction that is rolled back. `SimpleTestCase` is for
tests that need no database.

## Development and production

There is one [config/settings.py](../config/settings.py). It reads environment variables,
and with none set you get local development. The server sets `DJANGO_DEBUG=0` and its own
values (naming its hosts in `DJANGO_ALLOWED_HOSTS` turns debug mode off by itself); the block at the end of the file, `if not DEBUG:`, is everything that then changes.
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
- **Content Security Policy.** `SECURE_CSP` in settings, sent as a header by Django's
  `ContentSecurityPolicyMiddleware` in development and production alike, so a violation
  shows up in the browser console while developing. Pages may load scripts, styles,
  fonts and images from this site only. An inline `<script>` must carry
  `nonce="{{ csp_nonce }}"`; data for scripts goes through `json_script`, which is not
  executed and needs none. `'unsafe-eval'` is allowed because Alpine.js compiles the
  expressions in `x-data` and `@click`, and inline styles because course colours and
  KaTeX use them.
- **Rate limits.** [config/ratelimit.py](../config/ratelimit.py) is a small middleware:
  `RATE_LIMITS` in settings lists a path and how many calls a minute one account (or,
  signed out, one address) may make to it; the call after that gets `429` with
  `Retry-After`. It covers saving progress, search and starting a sign-in. The
  browser treats a refused save like being offline and sends it with the next one.
- **Request size.** [config/bodylimit.py](../config/bodylimit.py), a second small
  middleware. Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` limits a form's fields but not the
  files sent with it, and the CSRF check reads the whole form, files included, before
  the login check has asked who is there. So a request that declares more than that
  setting is answered `413` before anything reads it, except on the paths in
  `BODY_LIMITS` for a signed-in person: the page where an educator hands in a course.
  It answers in `__call__`, because the CSRF check is a `process_view` hook and those
  run after every middleware has let the request through.
- **Debug mode** is on only where `DJANGO_ALLOWED_HOSTS` is empty, or where
  `DJANGO_DEBUG=1` asks for it. A server has to name its hosts, so one where
  `DJANGO_DEBUG=0` was forgotten still runs with debug off.
- **`check --deploy`** is Django's own review of exactly these settings. A test
  ([config/tests.py](../config/tests.py)) and CI keep it clean.
- **Tests** always run with development settings, because `config/settings.py` is read
  with no variables set. The tests that need the production side start a second
  Python process with the server's variables.

## Not built yet, and where it would go

- **Limiting who may sign in** (faculty addresses only): an allauth adapter, described in
  [DEPLOYMENT.md](DEPLOYMENT.md#who-may-sign-in).
- **Marks on more things.** Glossary terms, hooks and formulas are graded only in their
  cover-and-recall mode, and do not count towards a course's bar
  ([static/js/lib/overview.js](../static/js/lib/overview.js) lists what does).
- **A stricter Content Security Policy.** The one in place allows `'unsafe-eval'` for
  Alpine. Dropping it means Alpine's CSP build and moving every inline expression into
  registered components.
- **Exact rate limits.** Counters live in each worker's memory; a shared cache (Redis,
  or Django's file cache on a memory disk) under the name `limits` would make them
  exact without touching [config/ratelimit.py](../config/ratelimit.py).
- **Files for download.** Source files are served only when `SERVE_SOURCE_FILES` is on
  (debug). For production, object storage (R2) behind signed URLs.
- **Search** is `icontains` over stored text. SQLite FTS5 is the next step.
- **Diagrams and plots** in content are images today.
