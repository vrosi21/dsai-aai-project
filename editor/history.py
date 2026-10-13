"""Looking at a course's history: what a step changed, line by line, and every version a
file has had.

The steps themselves, and what each one kept, are desk.handins' (`HandIn`, `kept`,
`before`). This module only reads them and puts two versions side by side. The
comparison is Python's own `difflib`, the same family of algorithm as `git diff`.
"""

import difflib
from dataclasses import dataclass, field
from datetime import datetime

from desk.models import HandIn
from ingest import handin

from . import source

CONTEXT = 3  # unchanged lines shown around a change
FILE_LINES = 400  # lines of one file's comparison that are shown
PAGE_LINES = 3000  # lines of all files' comparisons on one page


@dataclass
class Line:
    kind: str  # "add", "del" or "same"
    old: int | None  # its line number before, and after
    new: int | None
    text: str


@dataclass
class FileChange:
    name: str
    state: str  # "new", "removed" or "changed"
    is_text: bool = True
    hunks: list = field(default_factory=list)  # each a list of Line
    added: int = 0
    removed: int = 0
    cut: int = 0  # lines not shown because there are too many


def _text(data):
    """The lines of a file, or None if it is not text (an image)."""
    if data is None:
        return []
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n").split("\n")
    except UnicodeDecodeError:
        return None


def lines(old, new, *, limit=FILE_LINES):
    """Two lists of lines compared: (hunks, lines added, lines removed, lines cut)."""
    hunks, added, removed, shown, cut = [], 0, 0, 0, 0
    matcher = difflib.SequenceMatcher(None, old, new)
    for group in matcher.get_grouped_opcodes(CONTEXT):
        hunk = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                # An equal stretch is as long on both sides: line i before is line j after.
                hunk += [Line("same", i + 1, i + 1 + j1 - i1, old[i]) for i in range(i1, i2)]
                continue
            gone = [Line("del", i + 1, None, old[i]) for i in range(i1, i2)]
            came = [Line("add", None, j + 1, new[j]) for j in range(j1, j2)]
            removed += len(gone)
            added += len(came)
            hunk += gone + came
        room = max(0, limit - shown)
        cut += max(0, len(hunk) - room)
        if room:
            hunks.append(hunk[:room])
            shown += min(len(hunk), room)
    return hunks, added, removed, cut


def compare(before, after, *, limit=PAGE_LINES):
    """What turns one version of a course ({name: bytes}) into another, file by file."""
    changes, room = [], limit
    for name in sorted({*before, *after}):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        state = "new" if old is None else "removed" if new is None else "changed"
        change = FileChange(name, state)
        a, b = _text(old), _text(new)
        if a is None or b is None:
            change.is_text = False
        else:
            # A file that ends in a newline has an empty last "line"; not worth a row.
            a = a[:-1] if a and a[-1] == "" else a
            b = b[:-1] if b and b[-1] == "" else b
            change.hunks, change.added, change.removed, change.cut = lines(
                a, b, limit=min(FILE_LINES, room)
            )
            room = max(0, room - sum(len(hunk) for hunk in change.hunks))
        changes.append(change)
    return changes


@dataclass
class Version:
    """One version a file has had."""

    sha: str | None  # None: at this point the file was removed
    at: datetime
    step: HandIn | None  # the step that made it; None for the file as first published
    current: bool = False  # it is what the site has now


def versions(course, name):
    """Every version the file `name` has had on the site, newest first: as it was before
    anything was changed here, after each step, and after each save within a step."""
    found = []

    def add(sha, at, step):
        # Not there yet is not a version; and a save that left the file alone made none.
        if (found or sha is not None) and (not found or found[-1].sha != sha):
            found.append(Version(sha, at, step))

    rows = (
        HandIn.objects.filter(slug=course.slug)
        .defer("archive")
        .select_related("user")
        .order_by("created_at", "pk")
    )
    for row in rows:
        if not found and row.before:
            add(row.before.get(name), row.created_at, None)
        saves = [entry for entry in row.trail if name in entry.get("files", {})]
        for entry in saves:
            add(entry["files"][name], datetime.fromisoformat(entry["at"]), row)
        if not saves and row.files:
            add(row.files.get(name), row.updated_at or row.created_at, row)
    now = source.fingerprint(source.read(course, name)) or None
    for version in found:
        version.current = version.sha == now
    return found[::-1]


def summary(changes):
    """One line for a list of changes: "2 files changed, 1 new"."""
    new = sum(change.state == "new" for change in changes)
    gone = sum(change.state == "removed" for change in changes)
    lines_added = sum(change.added for change in changes)
    lines_removed = sum(change.removed for change in changes)
    return handin.summary(new, lines_added, lines_removed, gone) if changes else "no difference"
