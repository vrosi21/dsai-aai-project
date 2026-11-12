"""The owner's private material: courses/<course>/private/, laid out like content/.

    private/lectures/04-my-notes.md       private/decks/my-cards.yaml
    private/labs/02-my-lab.md             private/problems/my-set.yaml
    private/guides/defence.md             private/exams/<exam>/...
                                          private/memory/palaces/<palace>.yaml

Everything in it is checked like content/ and shown on the site to the owner alone: it is
private, and no switch makes it anything else (catalog/visibility.py), because it may hold
graded work. It is not among a course's files that travel: a download, a hand-in, the
editor and the history take course.yaml and content/ only (ingest/handin.py), so no
educator ever has it, and the educator kit knows nothing of it. It reaches the server
with the owner's `publish`. docs/PRIVATE.md says how to write it.

A name is used once across content/ and private/: a lecture there cannot have the file
name of one here. A course's formulas, glossary, cheat sheet and hooks are one file each
and stay in content/.
"""

import contextlib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .discover import CourseFiles, discover, find_course

FOLDER = "private"


@dataclass
class WithPrivate(CourseFiles):
    """A course's files with its private material added: names inside private/ are given
    relative to the owner's folder, wherever content/ was read from."""

    owner: Path | None = None
    private: frozenset = frozenset()  # the files that came from private/

    def rel(self, path):
        path = Path(path)
        if self.owner is not None and path.is_relative_to(self.owner / FOLDER):
            return path.relative_to(self.owner).as_posix()
        return super().rel(path)


def owner_folder(course_dir):
    """The folder whose private/ belongs to the course read from `course_dir`: that folder
    itself, or, for a course as handed in (a copy of course.yaml and content/ only), the
    owner's own folder of the course. None when it has no private material."""
    course_dir = Path(course_dir)
    if (course_dir / FOLDER).is_dir():
        return course_dir
    owner = find_course(course_dir.name, settings.CONTENT_ROOTS)
    if owner is not None and (Path(owner) / FOLDER).is_dir():
        return Path(owner)
    return None


def files_in(owner):
    if owner is None:
        return []
    return sorted(p for p in (owner / FOLDER).rglob("*") if p.is_file())


def digest(owner):
    """A fingerprint of the private material, part of what decides whether to sync again."""
    sha = hashlib.sha256()
    for path in files_in(owner):
        sha.update(path.relative_to(owner).as_posix().encode() + b"\0" + path.read_bytes())
    return sha.hexdigest()


def add(files, report):
    """`files` (what discover found in content/) with the course's private material."""
    owner = owner_folder(files.root)
    merged = WithPrivate(**{name: getattr(files, name) for name in files.__dataclass_fields__})
    if owner is None:
        return merged
    merged.owner = owner
    extra = discover(owner, report, content=FOLDER)
    for path in (extra.formulas, extra.glossary, extra.cheatsheet, extra.hooks):
        if path is not None:
            report.error(
                merged.rel(path),
                "a course's formulas, glossary, cheat sheet and hooks are one file each, in "
                "content/ only",
                hint="move what is private into a guide or a deck in private/",
            )

    taken = set()

    def join(mine, theirs, what):
        names = {Path(p).stem for p in mine}
        for path in theirs:
            if path.stem in names:
                report.error(
                    merged.rel(path),
                    f"content/ already has the {what} `{path.stem}`",
                    hint="a name is used once across content/ and private/: rename this one",
                )
            else:
                mine.append(path)
                taken.add(path)

    for kind, paths in extra.documents.items():
        join(merged.documents.setdefault(kind, []), paths, kind)
    join(merged.decks, extra.decks, "deck")
    join(merged.problem_sets, extra.problem_sets, "problem set")
    join(merged.palaces, extra.palaces, "palace")
    for slug, entry in extra.assessments.items():
        if slug in merged.assessments:
            report.error(
                merged.rel(owner / FOLDER / "exams" / slug),
                f"content/ already has the exam `{slug}`",
                hint="a name is used once across content/ and private/: rename this one",
            )
            continue
        merged.assessments[slug] = entry
        taken.update(p for p in [entry.assessment, *entry.papers] if p is not None)
    merged.private = frozenset(taken)
    return merged


HINT = (
    "content/ is what the course's educators have, so it must stand without private/: "
    "link from private/ to content/, never the other way"
)


def check_shared(course_dir, report):
    """Check content/ alone, as an educator's copy of the course is checked. What fails
    only without private/ is a link from shared content to the owner's private material,
    and an error: the educators' copy would be broken by it."""
    from .assets import Assets
    from .build import Builder
    from .load import load_bundle
    from .markdown import Renderer
    from .refs import Resolver
    from .report import Report

    alone = Report(course=report.course)
    files = discover(course_dir, alone)
    bundle = load_bundle(files, alone)
    if bundle is not None:
        builder = Builder(bundle, alone)
        resolver = Resolver(builder.index())
        assets = Assets(report.course)  # never committed: nothing is written
        renderer = Renderer(
            report=alone, resolver=resolver, assets=assets, content_root=files.content
        )
        # A crash after an error is that error's doing; the errors are what counts.
        with contextlib.suppress(Exception):
            builder.plan(renderer, resolver)
    seen = {(issue.file, issue.message) for issue in report.errors}
    for issue in alone.errors:
        if (issue.file, issue.message) not in seen:
            report.error(issue.file, issue.message, line=issue.line, where=issue.where, hint=HINT)
