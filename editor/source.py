"""What the editor reads and writes: the files of a course, as the site has them.

The database holds a course rendered (HTML, counts, links already resolved), which is what
a page view needs and no use to someone who wants to change the text. So a form is filled
from the file itself, read from the folder the site shows the course from
(`ingest.live.folder`), and what is saved is the new text of that one file, handed to
`desk.handins.change`, which decides whether the course may become that.

Nothing here writes to disk.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml
from django.http import Http404

from desk import handins
from ingest import handin, live, yamledit
from ingest.assets import Assets
from ingest.build import Builder
from ingest.discover import DOCUMENT_DIRS, discover
from ingest.load import load_bundle
from ingest.markdown import Renderer
from ingest.refs import Resolver
from ingest.report import Report
from ingest.yamlio import split_front_matter

# "lecture" -> "lectures": the folder under content/ that holds pages of each kind.
DIRS = {kind: directory for directory, kind in DOCUMENT_DIRS.items()}
CHEATSHEET = "content/reference/cheatsheet.md"


class Unreadable(Exception):
    """The file is not a page the form can show (no front matter, or not text). The raw
    view can still open it."""


def folder(course):
    found = live.folder(course.slug)
    if found is None:
        raise Http404
    return found


def page_name(kind, slug=""):
    """Where a page of this kind lives inside the course folder."""
    return CHEATSHEET if kind == "sheet" else f"content/{DIRS[kind]}/{slug}.md"


def fingerprint(data):
    """What a form remembers of the file it was filled from: on saving, the file must
    still be this, or someone else has changed it in the meantime."""
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def plain(name):
    """Whether `name` could be a file of a course at all. The real check is handin.check,
    on saving; this one only keeps a made-up address from being looked for on disk."""
    path = PurePosixPath(name)
    if name != path.as_posix() or path.is_absolute() or ".." in path.parts or "\\" in name:
        return False
    if name == "course.yaml":
        return True
    hidden = any(part.startswith((".", "_")) for part in path.parts)
    in_content = path.parts[:1] == ("content",) and len(path.parts) > 1
    return in_content and not hidden and path.suffix.lower() in handin.SUFFIXES


def read(course, name):
    """The bytes of one file of the course, or None if there is no such file."""
    if not plain(name):
        return None
    path = folder(course) / name
    return path.read_bytes() if path.is_file() else None


def listing(course):
    """Every file of the course: (name, size in bytes, whether it is text)."""
    return [
        (name, path.stat().st_size, path.suffix.lower() in handin.TEXT)
        for path, name in handin.course_files(folder(course))
    ]


@dataclass
class Page:
    """A Markdown page as the form sees it."""

    name: str
    original: str | None = None  # the file's text; None for a page that does not exist yet
    meta: dict = field(default_factory=dict)  # its front matter
    body: str = ""
    seen: str = ""

    @property
    def exists(self):
        return self.original is not None


def load_page(course, name):
    data = read(course, name)
    if data is None:
        return Page(name=name)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise Unreadable from error
    front, body, _line = split_front_matter(text)
    try:
        meta = yaml.safe_load(front) if front is not None else None
    except yaml.YAMLError as error:
        raise Unreadable from error
    if not isinstance(meta, dict):
        raise Unreadable
    return Page(name, text, meta, yamledit.clean(body), fingerprint(data))


def compose(page, meta, body):
    """The text of the page with this front matter and this body.

    What was not changed keeps its bytes: a page whose front matter was left alone keeps
    it as its author wrote it (comments, the order of keys, `[a, b]` lists), and saving
    without changing anything gives the very same file, which the site then reports as
    nothing to save.
    """
    body = yamledit.clean(body)
    if page.exists:
        front, old_body, _line = split_front_matter(page.original)
        same_meta = yamledit.tidy(page.meta) == yamledit.tidy(meta)
        same_body = yamledit.clean(old_body) == body
        if same_meta and same_body:
            return page.original
        if same_meta:
            head = page.original[: len(page.original) - len(old_body)]
            return f"{head}\n{body}\n"
        if same_body:
            return f"---\n{yamledit.dump(meta)}---\n{old_body}"
    return yamledit.page(meta, body)


def save(user, course, name, text, seen):
    """Make `text` the file `name` of the course (None removes the file). `seen` is the
    fingerprint the form was filled from; "" says the file was not there.

    Returns what handins.change returns; raises handins.Refused, and then nothing changed.
    """

    def edit(files):
        if fingerprint(files.get(name)) != seen:
            there = name in files
            raise handins.Refused(
                "conflict",
                ["It was changed on the site after you opened it."]
                if there
                else ["It was removed from the site after you opened it."]
                if seen
                else ["A file with this name was created in the meantime."],
            )
        if text is None:
            del files[name]
        else:
            files[name] = text.encode("utf-8")
        return files

    return handins.change(user, course, edit)


# ---- the preview --------------------------------------------------------------------------

# What can be linked to in each course, kept per process until the course changes: reading
# every file of a course to resolve the links of one preview would be the slowest thing
# the editor does, and it is asked for after every pause in typing.
_resolvers = {}


def resolver(course):
    where = folder(course)
    key = (course.content_hash, str(where))
    kept = _resolvers.get(course.slug)
    if kept is None or kept[0] != key:
        report = Report(course=course.slug)
        bundle = load_bundle(discover(where, report), report)
        found = Resolver(Builder(bundle, report).index()) if bundle is not None else None
        kept = _resolvers[course.slug] = (key, found)
    return kept[1]


def preview(course, name, body, takeaways=(), *, document=True):
    """Render a page's text with the renderer the importer uses, so what is previewed is
    what will be published. Returns the HTML and whatever the renderer objected to.

    Nothing is stored: images already in the course keep their addresses, and maths is
    left as text for the browser to typeset, as on every page of the site.
    """
    where = folder(course)
    report = Report(course=course.slug)
    renderer = Renderer(
        report=report,
        resolver=resolver(course),
        assets=Assets(course.slug),
        content_root=where / "content",
    )
    done = renderer.render(
        yamledit.clean(body),
        file=name,
        base_dir=(where / name).parent,
        document=document,
    )
    points = [
        renderer.inline(yamledit.clean(text), file=name, where=f"takeaways[{number}]")
        for number, text in enumerate(takeaways)
        if text.strip()
    ]
    problems = [
        {
            "level": issue.level,
            "line": None if issue.where else issue.line,
            "where": issue.where,
            "message": issue.message,
            "hint": issue.hint,
        }
        for issue in report.issues
    ]
    return {"html": done.html, "takeaways": points, "problems": problems, "words": done.words}
