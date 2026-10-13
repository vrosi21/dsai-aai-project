"""Hand-ins: what educators do to a course, taken in on the site itself.

An educator uploads one zip, made by the educator kit, of their whole course. `take`
checks it and, if it passes, the course on the site becomes that version at once:

1. It must be plainly their course and nothing else (ingest/handin.py).
2. It must be built on the version the site has. The site remembers which version each
   educator last took from it or gave it (`Educator.synced_version`). If the course has
   changed since, someone else's work would be undone by taking this one in, so the
   person is told to fetch the latest and put their work on top of it.
3. It may add, change and remove. But whatever it removes or rewrites is shown to the
   person first, line by line, and taken in only when they have said that it is meant
   (`confirmed`): a slip of an AI assistant should not quietly delete half a course.
4. In course.yaml only the topics and the description are theirs.
5. The site's own validation, all of it, must pass.

Nothing is ever lost by a hand-in. Each one is kept whole in the database (`HandIn.files`
and `before`: the course as it stood after the step and when it began, each file's bytes
stored once in desk/blobs.py), so every earlier version can be fetched again, and a
hand-in can be taken back (`withdraw`), which puts the course back to the version before
it.

An edit made on the site (the `editor` app) is the same thing without the zip: `change`
makes the next version from the one the site has, and it passes the same checks, lands in
the same history and can be taken back the same way. Only two of the rules above have
nothing to ask there: it cannot be built on an old copy (2), and what it removes was
removed by hand a moment ago (3). Someone editing saves often, so their saves within
`SESSION` of each other are one step, not one each; except a save that removes a file,
which always begins a step, so that the step before it still holds that file.

Where the files go. The owner's course files are a folder the application cannot write
to, replaced as a whole when the owner publishes. A hand-in therefore does not touch it:
the course is unpacked into a folder of its own (settings.HANDINS_DIR) and the site reads
the course from there (`overlay`, registered with ingest.live). When the owner's published
files say that they include a hand-in (the `.merged` file that import_course writes), or
are the very same files, that folder is dropped and the hand-in is marked as merged. If
the owner publishes without it, the site keeps showing the hand-in and the sync report
says so. The folder is only a working copy, rebuilt from the database when missing.
"""

import os
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.utils import DatabaseError
from django.utils import timezone

from ingest import handin, live
from ingest.discover import find_courses
from ingest.sync import sync_course

from . import blobs, notices
from .models import Educator, HandIn

MAX_UPLOAD = 25 * 1024 * 1024  # the zip itself
MAX_UNPACKED = 60 * 1024 * 1024  # what it may unpack to
MARKER = ".handin"  # in the folder of an unpacked course: which hand-in it is
# In the owner's own course folder: the version of the newest hand-in their files include.
# Written by `manage.py import_course`, sent along by `publish`.
MERGED = ".merged"
STALE_LOCK = 300  # seconds after which a lock is taken to be left over from a crash
# Someone editing on the site saves again and again. Saves by one person that follow each
# other within this long are one step in the course's history, not one each.
SESSION = timedelta(minutes=30)


class Refused(Exception):
    """A hand-in that was not taken in. `kind` says why, `lines` says what exactly.

    conflict   the course changed on the site after this person took their copy, or
               (in the editor) the very thing they were editing changed meanwhile
    start      the site does not know which version this person started from
    confirm    it removes or rewrites something, and that was not confirmed
    owner      it changes what is the owner's in course.yaml
    invalid    the site's validation found errors (the lines are its report)
    nothing    it changes nothing
    busy       someone else is handing in the same course this very moment
    bad        it is not a zip of this course
    """

    def __init__(self, kind, lines):
        super().__init__(kind)
        self.kind = kind
        self.lines = list(lines)


def root():
    return Path(settings.HANDINS_DIR)


@contextmanager
def lock(slug):
    """One hand-in per course at a time, across the server's worker processes.

    Creating a file with O_EXCL either succeeds or fails as one step, whoever else tries
    at the same moment, which is all a lock needs. Without it two people handing in
    together would each be checked against the same version, and the second to finish
    would undo what the first did.
    """
    root().mkdir(parents=True, exist_ok=True)
    path = root() / f".lock-{slug}"
    if path.exists() and time.time() - path.stat().st_mtime > STALE_LOCK:
        path.unlink(missing_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Refused(
            "busy", ["Someone is handing in this course right now. Try again in a minute."]
        ) from None
    try:
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


def take(user, course, upload, *, confirmed=False):
    """Check an uploaded zip and, if it passes, make it the course on the site.

    `confirmed` is the person saying that what the zip removes or rewrites is meant.
    Returns the HandIn that records it; raises Refused otherwise, and then nothing at
    all has changed.
    """
    if upload.size > MAX_UPLOAD:
        raise Refused("bad", [f"The file is larger than {MAX_UPLOAD // 1024 // 1024} MB."])
    try:
        slug, files = handin.unzip(upload, MAX_UNPACKED)
    except handin.HandInError as error:
        raise Refused("bad", [f"{error}."]) from error
    if slug != course.slug:
        raise Refused(
            "bad", [f"This zip is the course `{slug}`. Here it has to be `{course.slug}`."]
        )

    with lock(slug):
        folder = live.folder(slug)
        if folder is None:
            raise Refused("bad", ["The site has no files for this course to work on yet."])
        current = handin.files_of(folder)
        now = handin.version_of(current)
        if handin.version_of(files) == now:
            raise Refused("nothing", ["This is exactly what the site already has."])

        # Built on what the site has? Staff are taken at their word; an educator's
        # starting point is on record from their last download or hand-in.
        role = Educator.objects.filter(user=user, series=course.series).first()
        if role is None and not user.is_staff:
            raise Refused("bad", ["You do not write this course."])
        if role is not None and not role.synced_version:
            raise Refused("start", [])
        if role is not None and role.synced_version != now:
            raise Refused("conflict", [])

        theirs = files["course.yaml"].decode("utf-8", errors="replace")
        problems = handin.protected_changes(current["course.yaml"].decode("utf-8"), theirs)
        if problems:
            raise Refused("owner", problems)
        change = handin.compare(current, files)
        if not change.only_adds and not confirmed:
            raise Refused("confirm", change.lost)

        target = _go_live(slug, files)
        record = HandIn.objects.create(
            user=user,
            series=course.series,
            slug=slug,
            base_version=now,
            version=handin.version_of(files),
            new_files=change.added,
            removed_files=change.removed,
            grown=change.grown,
            shrunk=change.shrunk,
            removed_ids=change.ids,
            lines_added=change.lines_added,
            lines_removed=change.lines_removed,
            files=blobs.put(files),
            before=blobs.put(current),
        )
        (target / MARKER).write_text(_mark(record), encoding="utf-8")
    remember(user, course.series, record.version)
    notices.tell(
        notices.educators_of(course.series, but=user),
        f"{course.title} changed on the site",
        f"{record.summary}. Download the course files again before your next hand-in.",
        url=f"/educators/courses/{course.series}/#files",
    )
    notices.tell(
        [owner for owner in notices.owners() if owner.pk != user.pk],
        f"Hand-in on the site: {course.title}",
        f"{record.summary}. It is live.",
        url=f"/admin/desk/handin/{record.pk}/change/",
    )
    return record


def begin(user, series, slug, files):
    """A course that starts on the site: another year of `series`, with no folder among
    the owner's course files behind it yet. `files` is the whole of it ({name: bytes}),
    course.yaml included.

    It is the first step of that course's history, with nothing before it. Until the
    owner takes it into the course files (import_course makes its folder) and publishes,
    the site shows it from this step and those that follow, as it shows any hand-in.
    Returns the step; raises Refused, and then nothing was made.
    """
    if not user.is_staff and not Educator.objects.filter(user=user, series=series).exists():
        raise Refused("bad", ["You do not write this course."])
    with lock(slug):
        taken = live.folder(slug) is not None or HandIn.objects.filter(slug=slug).exists()
        if taken:
            raise Refused("conflict", ["There is a course at this address already."])
        try:
            handin.check(files, MAX_UNPACKED)
        except handin.HandInError as error:
            raise Refused("bad", [f"{error}."]) from error
        target = _go_live(slug, files)
        kept_as = blobs.put(files)
        step = HandIn.objects.create(
            user=user,
            series=series,
            slug=slug,
            via=HandIn.Via.SITE,
            base_version="",  # born here: this is what says so (see overlay)
            version=handin.version_of(files),
            new_files=sorted(files),
            files=kept_as,
            trail=[{"at": timezone.now().isoformat(timespec="seconds"), "files": kept_as}],
        )
        (target / MARKER).write_text(_mark(step), encoding="utf-8")
    notices.tell(
        [owner for owner in notices.owners() if owner.pk != user.pk],
        f"A new year was started on the site: {slug}",
        "It is live. To have it in your course files, download it in the admin and run "
        "import_course.",
        url=f"/admin/desk/handin/{step.pk}/change/",
    )
    return step


def _go_live(slug, files):
    """Make `files` the course on the site, if they are a valid course. Returns the folder
    they are in now; raises Refused("invalid") with the importer's report otherwise, and
    then the course is untouched."""
    work = root() / f".incoming-{uuid.uuid4().hex}"
    staged = work / slug
    try:
        _write(staged, files)
        # The importer validates first and writes to the database only if nothing is
        # wrong, so this one call is both the check and the going live.
        report = sync_course(staged)
        if report.errors:
            raise Refused("invalid", report.format().splitlines())
        target = root() / slug
        shutil.rmtree(target, ignore_errors=True)
        staged.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def files_at(slug, version):
    """The course as it was at `version`, as {name: bytes}, or None if the site no longer
    knows. It is what one of the steps on record left behind, or the owner's own files."""
    row = HandIn.objects.filter(slug=slug, version=version).order_by("-pk").first()
    if row is not None:
        return kept(row)
    began = HandIn.objects.filter(slug=slug, base_version=version).order_by("-pk").first()
    if began is not None and began.before:
        return blobs.get(began.before)
    owner = find_courses(settings.CONTENT_ROOTS).get(slug)
    if owner is not None:
        files = handin.files_of(owner)
        if handin.version_of(files) == version:
            return files
    return None


def kept(row):
    """The course as it stood after this step: {name: bytes}."""
    if row.files:
        return blobs.get(row.files)
    # A step from before the blob store that could not be converted still has its zip.
    return handin.unzip(bytes(row.archive), MAX_UNPACKED)[1]


def before(row):
    """The course as it stood when this step began, or None if that is not on record
    (a step from before the site kept it, whose starting point has since moved on)."""
    if row.before:
        return blobs.get(row.before)
    if not row.base_version and row.via == HandIn.Via.SITE and row.files:
        return {}  # the step a year began with on the site: before it there was nothing
    return files_at(row.slug, row.base_version) if row.base_version else None


def _session(user, slug, version):
    """The step a save on the site belongs to, if this person is in the middle of one:
    the newest step of the course, theirs, made in the editor, not long ago, and still
    what the site has."""
    newest = (
        HandIn.objects.filter(slug=slug, status=HandIn.Status.LIVE)
        .order_by("-created_at", "-pk")
        .defer("archive", "files")
        .first()
    )
    if newest is None or newest.via != HandIn.Via.SITE or newest.user_id != user.pk:
        return None
    if newest.version != version:
        return None
    last = newest.updated_at or newest.created_at
    return newest if timezone.now() - last <= SESSION else None


def change(user, course, edit):
    """An edit made on the site: the next version of the course, made from the one the
    site has at this very moment.

    `edit(files)` is given the course's files as they are now ({name: bytes}) and returns
    them as they should be. It is called while the course is locked, so nothing it reads
    can change under it; it raises Refused("conflict", …) if what the person was looking
    at is no longer what is there. Everything a zip from the kit has to pass applies here
    too, with two differences. There is no question of which copy it was built on: it is
    built on the site's. And what it removes needs no second confirmation: the person
    changed that very thing, on purpose, a moment ago.

    Returns (the step in the course's history, whether this save began a new step). Raises
    Refused, and then nothing at all has changed.
    """
    slug = course.slug
    role = Educator.objects.filter(user=user, series=course.series).first()
    if role is None and not user.is_staff:
        raise Refused("bad", ["You do not write this course."])

    with lock(slug):
        folder = live.folder(slug)
        if folder is None:
            raise Refused("bad", ["The site has no files for this course to work on yet."])
        current = handin.files_of(folder)
        now = handin.version_of(current)
        files = edit(dict(current))
        try:
            handin.check(files, MAX_UNPACKED)
        except handin.HandInError as error:
            raise Refused("bad", [f"{error}."]) from error
        version = handin.version_of(files)
        if version == now:
            raise Refused("nothing", ["Nothing was changed."])
        # What is the owner's in course.yaml stays the owner's; whoever runs the site is
        # the owner, or acts for them.
        if not user.is_staff:
            theirs = files["course.yaml"].decode("utf-8", errors="replace")
            problems = handin.protected_changes(current["course.yaml"].decode("utf-8"), theirs)
            if problems:
                raise Refused("owner", problems)

        # A save that removes a file never joins the step before it. That step then keeps
        # the file, so a page written and removed in one sitting can still be fetched.
        gone = current.keys() - files.keys()
        step = None if gone else _session(user, slug, now)
        target = _go_live(slug, files)
        fresh = step is None
        after = blobs.put(files)
        if fresh:
            done = handin.compare(current, files)
            step = HandIn(user=user, series=course.series, slug=slug, base_version=now)
            step.via = HandIn.Via.SITE
            step.before = blobs.put(current)
        else:
            # The step grows: what it did is now everything since it began.
            done = handin.compare(blobs.get(step.before), files)
            step.saves += 1
            step.updated_at = timezone.now()
        # What this one save changed. The step itself keeps only where it ends up, so
        # this is where a text that a later save of the sitting replaces can be found.
        changed = {
            name: after.get(name)
            for name in {*current, *files}
            if current.get(name) != files.get(name)
        }
        step.trail = [
            *step.trail,
            {"at": timezone.now().isoformat(timespec="seconds"), "files": changed},
        ]
        step.version, step.files = version, after
        step.new_files, step.removed_files = done.added, done.removed
        step.grown, step.shrunk, step.removed_ids = done.grown, done.shrunk, done.ids
        step.lines_added, step.lines_removed = done.lines_added, done.lines_removed
        step.save()
        (target / MARKER).write_text(_mark(step), encoding="utf-8")

    if fresh:
        # Once per step, not once per save. Their own copy of the course, if they keep
        # one with the kit, is behind now as well, and their page says so by itself.
        notices.tell(
            notices.educators_of(course.series, but=user),
            f"{course.title} changed on the site",
            "It is being edited on the site. Download the course files again before your "
            "next hand-in.",
            url=f"/educators/courses/{course.series}/#files",
        )
        notices.tell(
            [owner for owner in notices.owners() if owner.pk != user.pk],
            f"Edited on the site: {course.title}",
            "It is live.",
            url=f"/admin/desk/handin/{step.pk}/change/",
        )
    return step, fresh


def remember(user, series, version):
    """This person now has (or has just given the site) this version of the course.
    Their next hand-in is taken only if the site still has it."""
    Educator.objects.filter(user=user, series=series).update(
        synced_version=version, synced_at=timezone.now()
    )


def _write(folder, files):
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _mark(record):
    """What the folder of an unpacked course says about itself: which step it is, and at
    which version, since a step made in the editor grows while its number stays."""
    return f"{record.pk} {record.version}"


def _working(record):
    """The folder holding this step's course, if it is there already."""
    target = root() / record.slug
    marker = target / MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == _mark(record):
        return target
    return None


def _unpacked(record, files):
    """The folder holding this step's course, unpacking it if it is not there."""
    target = _working(record)
    if target is None:
        target = root() / record.slug
        shutil.rmtree(target, ignore_errors=True)
        _write(target, files)
        (target / MARKER).write_text(_mark(record), encoding="utf-8")
    return target


def _included(owner, record, files):
    """Whether the owner's course folder already has this hand-in: it says so itself
    (the file import_course wrote), or it is the very same files, or it holds every
    file and line of it."""
    note = owner / MERGED
    if note.is_file() and note.read_text(encoding="utf-8").strip() == record.version:
        return True
    theirs = handin.files_of(owner)
    return handin.version_of(theirs) == record.version or handin.holds(theirs, files)


def overlay(base, *, settle=False, notes=None, only=None):
    """For ingest.live: the folder to read each course from, given the owner's (`base`).

    A course with hand-ins on the site is read from the newest of them, unless the
    owner's files already include it. With `settle` (a real sync, not a check) those
    overtaken hand-ins are marked as merged and their folders removed.

    Asked about one course (`only`), which is what a page does while someone waits, the
    answer comes from the folder that is already unpacked, if it is this very step.
    Whether the owner's files have caught up is a question for the next sync: until one
    has run, the site shows the hand-in either way.
    """
    # light(): each row names every file of a course. Only the newest of a course is
    # ever opened, and what it kept is fetched when it is asked for.
    rows = HandIn.objects.filter(status=HandIn.Status.LIVE).light()
    if only is not None:
        rows = rows.filter(slug=only)
    try:
        rows = list(rows.order_by("created_at", "pk"))
    except DatabaseError:  # the table is not there yet: a first migrate is still to come
        return base
    result, newest, count, born = dict(base), {}, {}, set()
    for row in rows:
        if row.slug not in newest and not row.base_version:
            born.add(row.slug)  # its first step had nothing before it: it began here
        newest[row.slug] = row
        count[row.slug] = count.get(row.slug, 0) + 1

    for slug, row in newest.items():
        owner = base.get(slug)
        if owner is None and slug not in born:
            continue  # the owner has taken the course away; there is nothing to show
        if only is not None and not settle:
            ready = _working(row)
            if ready is not None:
                result[slug] = ready
                continue
        files = kept(row)
        if owner is None:
            # A year started on the site, which the owner's files have no folder for.
            result[slug] = _unpacked(row, files)
            if notes is not None:
                notes[slug] = (
                    "a year that was started on the site: your course files have no "
                    "folder for it. Take it in (admin, Hand-ins, download; then "
                    "import_course, which makes the folder) and publish again."
                )
            continue
        if _included(owner, row, files):
            if settle:
                HandIn.objects.filter(slug=slug, status=HandIn.Status.LIVE).update(
                    status=HandIn.Status.MERGED, settled_at=timezone.now()
                )
            continue
        result[slug] = _unpacked(row, files)
        if notes is not None:
            n = count[slug]
            notes[slug] = (
                f"shown as handed in by its educators: the published files lack {n} "
                f"hand-in{'s' * (n != 1)}. Take the newest in (admin, Hand-ins, download; "
                "then import_course) and publish again."
            )
    if settle and only is None and root().is_dir():
        for folder in root().iterdir():
            # What is left over: courses whose hand-ins were merged or taken back.
            if (
                folder.is_dir()
                and not folder.name.startswith(".")
                and folder not in result.values()
            ):
                shutil.rmtree(folder, ignore_errors=True)
    return result


def root_base(record):
    """The version the owner's files had when the hand-ins now on the site began: what
    `import_course` compares the owner's own files with."""
    first = (
        HandIn.objects.filter(slug=record.slug, status=HandIn.Status.LIVE)
        .order_by("created_at", "pk")
        .first()
    )
    return (first or record).base_version


def withdraw(record, by=None):
    """Take a hand-in back, and with it every later one for the same course: each was
    built on the one before. The course returns to the version before it, and that is
    loaded at once. Returns the sync report, or None if the course is gone."""
    later = HandIn.objects.filter(
        slug=record.slug, status=HandIn.Status.LIVE, created_at__gte=record.created_at
    )
    authors = {row.user for row in later.select_related("user") if row.user}
    later.update(status=HandIn.Status.WITHDRAWN, settled_at=timezone.now())
    folder = live.folders(settle=True).get(record.slug)
    report = sync_course(folder, force=True) if folder else None
    if folder is None:
        # What was taken back is the step a year began with: without it there is no
        # such course. Its rows go now, not at the next full sync.
        from catalog.models import Course

        Course.objects.filter(slug=record.slug).delete()
        shutil.rmtree(root() / record.slug, ignore_errors=True)
    # Everyone who writes the course now has a copy that is ahead of the site.
    told = {*authors, *notices.educators_of(record.series)} - {by}
    notices.tell(
        told,
        f"A hand-in for `{record.slug}` was taken back",
        "The course on the site is as it was before it. Download the course files "
        "again before your next hand-in.",
        url=f"/educators/courses/{record.series}/#files",
    )
    return report
