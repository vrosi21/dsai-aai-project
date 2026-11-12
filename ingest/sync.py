"""The pipeline, start to finish, for one course or all of them.

    discover -> load + validate -> index (cross-checks) -> render -> maths check -> write

Everything up to `write` only reads files and collects problems in a Report. The database is
touched in the last step, and only if the report has no errors — so a half-valid course never
replaces a working one.
"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from catalog.models import Course

from . import duties, live, mathcheck, private, store
from .assets import Assets
from .build import Builder
from .discover import clashes, content_hash, discover
from .load import load_bundle
from .markdown import Renderer
from .refs import Resolver
from .report import Report


def sync_course(course_dir, *, check_only=False, force=False):
    course_dir = Path(course_dir)
    slug = course_dir.name
    report = Report(course=slug)
    # The owner's own list of dated duties, beside course.yaml (ingest/duties.py). The site
    # shows it nowhere; it is checked here like any file of the course.
    dated = duties.read(course_dir, report)
    # The owner's private material (ingest/private.py) counts as content too.
    digest = content_hash(course_dir)
    owner = private.owner_folder(course_dir)
    if owner is not None:
        digest = hashlib.sha256((digest + private.digest(owner)).encode()).hexdigest()
    if not check_only and not force and report.ok:
        # A file can be saved again without changing (an edit undone, a restored copy). The
        # bytes still match, so there is nothing to import, but record that they were
        # compared: the "files changed" notice on the site goes by this time.
        unchanged = Course.objects.filter(slug=slug, content_hash=digest)
        if unchanged.update(synced_at=timezone.now()):
            report.status = "unchanged"
            return report

    files = private.add(discover(course_dir, report), report)
    bundle = load_bundle(files, report)
    if bundle is None:
        report.status = "failed"
        return report

    builder = Builder(bundle, report)
    resolver = Resolver(builder.index())
    assets = Assets(slug)
    renderer = Renderer(report=report, resolver=resolver, assets=assets, content_root=files.content)
    try:
        plan = builder.plan(renderer, resolver)
    except Exception:
        # A broken cross-reference already reported can leave gaps that trip the second
        # pass. Those errors are what the author needs; a crash with a clean report is a bug.
        if report.ok:
            raise
        report.status = "failed"
        return report

    if report.ok and files.private:
        private.check_shared(course_dir, report)

    note = mathcheck.check(renderer.math, report)
    if note:
        report.notes.append(note)
    report.counts = plan.counts()
    if dated:
        report.counts["duties"] = len(dated)

    if not report.ok:
        report.status = "failed"
    elif check_only:
        report.status = "checked"
    else:
        assets.commit()
        store.write(plan, digest)
        report.status = "synced"
    return report


def sync_all(*, only=None, check_only=False, force=False):
    """Sync every course under settings.CONTENT_ROOTS (or just the slugs in `only`)."""
    # Where each course is read from. For a course educators have added to since the
    # last publish that is the course as handed in (ingest/live.py); a real sync also
    # lets go of hand-ins that the published files now contain.
    notes = {}
    courses = live.folders(settle=not check_only, notes=notes)
    reports = []
    twice = clashes(settings.CONTENT_ROOTS)
    for slug, folders in twice.items():
        # Neither folder is loaded: picking one would be a guess about which is meant.
        report = Report(course=slug, status="failed")
        places = " and ".join(f"{folder.parent.name}/{folder.name}" for folder in folders)
        report.error(
            "course.yaml",
            f"two folders are named `{slug}`: {places}",
            hint="a course's address is its folder's name, so it must be unique. "
            "Rename or remove one of them",
        )
        reports.append(report)
        courses.pop(slug, None)
    for slug in only or []:
        if slug not in courses:
            report = Report(course=slug, status="failed")
            roots = ", ".join(str(root) for root in settings.CONTENT_ROOTS)
            report.error(
                "course.yaml",
                f"no course folder `{slug}` with a course.yaml was found",
                hint=f"looked in: {roots}. Create one with: manage.py startcourse {slug}",
            )
            reports.append(report)
    for slug, path in courses.items():
        if only and slug not in only:
            continue
        report = sync_course(path, check_only=check_only, force=force)
        if slug in notes:
            report.notes.append(notes[slug])
        reports.append(report)

    if not only and not check_only:
        for course in Course.objects.exclude(slug__in=[*courses, *twice]):
            report = Report(course=course.slug, status="removed")
            report.notes.append("its folder no longer exists, so it was removed from the database")
            course.delete()
            reports.append(report)
    return reports
