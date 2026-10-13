"""Bring what educators did on the site into your own course files.

    manage.py import_course rl-version-12.zip

Educators hand their work in on the site, where it is checked and goes live at once
(desk/handins.py). Your course files do not know about it until you take it in here. The
zip is one step of a course's history, downloaded from the admin (Hand-ins), and holds the
whole course as it stood after it. It is treated as untrusted all the same:

1. It must be plainly one course that exists here: course.yaml and content/, nothing else
   (ingest/handin.py).
2. Its course.yaml may differ from yours only in topics and description.
3. It is unpacked to a temporary folder and run through the full validation.
4. The course folder must have no uncommitted changes, so that what is taken in is a
   diff of its own that you can read and undo.

Then your files become that version. How depends on whether you changed the course
yourself since you last published it:

- You did not (your files are the version the hand-ins were built on: the zip says which
  that was). The course is replaced as a whole: what educators added, changed and removed.
- You did. Then files you do not have are added and files that only gained lines are
  taken. A file that differs in any other way stops everything until you decide: without
  the version both started from there is no telling whether they removed a line or you
  added one. `--theirs` replaces the course with the zip's version (your own changes stay
  in git); `--mine` keeps your version of those files and takes only what the zip adds.

Either way a small file, `.merged`, records which hand-in your files now include. `publish`
sends it along, and the site then lets go of its own copy of that hand-in. Nothing is
loaded into the database and nothing is published here: read the diff, run sync_content,
commit, publish.
"""

import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingest import handin
from ingest.discover import find_courses
from ingest.management.commands.publish import MERGED
from ingest.management.commands.sync_content import safe_streams
from ingest.sync import sync_course


def uncommitted(folder):
    """Changed files in `folder` that git knows about; [] when clean or not a repository."""
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    return done.stdout.splitlines() if done.returncode == 0 else []


class Command(BaseCommand):
    help = "Validate a course zip from the site and make your course files that version."

    def add_arguments(self, parser):
        parser.add_argument("zip", help="A hand-in downloaded from the admin, or a kit's zip.")
        parser.add_argument(
            "--check", action="store_true", help="Validate only; change nothing here."
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Go ahead although the course folder has uncommitted changes.",
        )
        choice = parser.add_mutually_exclusive_group()
        choice.add_argument(
            "--theirs",
            action="store_true",
            help="Replace the course with the zip's version, whatever you changed.",
        )
        choice.add_argument(
            "--mine",
            action="store_true",
            help="Where you and the zip both changed a file, keep yours.",
        )

    def handle(self, *args, **options):
        safe_streams()
        source = Path(options["zip"])
        if not source.is_file():
            raise CommandError(f"{source} is not a file.")
        try:
            slug, files, note = handin.unzip_with_note(source)
        except handin.HandInError as error:
            raise CommandError(f"Refused: {error}. Nothing was changed.") from error

        here = find_courses([settings.COURSES_DIR])
        target = here.get(slug)
        born = target is None
        if born:
            # A year that was started on the site: it names the course it is a year of,
            # and its folder is made beside that one.
            series = handin.series_of(files["course.yaml"])
            beside = here.get(series)
            if beside is None:
                raise CommandError(
                    f"There is no course `{slug}` here, and its course.yaml does not name a "
                    "course of yours that it is another year of (`series`). A hand-in can "
                    "change a course or add a year to one, not create a course: make it "
                    "with startcourse first."
                )
            target = beside.parent / slug
        mine = {} if born else handin.files_of(target)
        changed = (
            []
            if born
            else handin.protected_changes(
                mine["course.yaml"].decode("utf-8"),
                files["course.yaml"].decode("utf-8", errors="replace"),
            )
        )
        if changed:
            raise CommandError(
                "Refused: course.yaml changes what is not the educator's to change.\n  "
                + "\n  ".join(changed)
                + "\nNothing was changed."
            )

        with tempfile.TemporaryDirectory(prefix="dsai-handin-") as scratch:
            staged = Path(scratch) / slug
            for name, data in files.items():
                path = staged / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            report = sync_course(staged, check_only=True)
        if report.errors:
            self.stdout.write(self.style.ERROR(report.format()))
            raise CommandError("The zip is not valid (above). Nothing was changed.")
        for issue in report.warnings:
            self.stdout.write(self.style.WARNING(issue.format()))

        # Are your files the version the hand-ins started from? Then theirs simply
        # follows yours, and the course can be replaced as a whole.
        untouched = born or (bool(note.get("base")) and handin.version_of(mine) == note["base"])
        write, remove, kept, both = {}, [], [], []
        if untouched or options["theirs"]:
            write = {name: data for name, data in files.items() if mine.get(name) != data}
            remove = sorted(set(mine) - set(files))
        else:
            # You changed the course too. What the zip only adds is safe to take. Where
            # a file differs in any other way there is no telling, without the version
            # both started from, whether they removed a line or you added one.
            for name, data in sorted(files.items()):
                if (
                    name not in mine
                    or name == "course.yaml"
                    or handin.extends(mine[name], data, name)
                ):
                    if mine.get(name) != data:
                        write[name] = data
                else:
                    both.append(name)
            if both and not options["mine"]:
                raise CommandError(
                    "You changed this course yourself since it was last published, and it "
                    "differs from the zip in ways that cannot be put together by rule:\n  "
                    + "\n  ".join(both)
                    + "\nNothing was changed. --theirs replaces the course with the zip's "
                    "version (your own changes stay in git, to put back by hand); --mine "
                    "keeps your version of these files and takes only what the zip adds."
                )
            kept += both

        change = handin.compare(mine, {**mine, **write} if not remove else files)
        summary = str(change) if (write or remove) else "nothing to take"
        if kept:
            summary += f"; {len(kept)} of your files left as they are"
        if options["check"]:
            where = f" Its folder would be made at {target}." if born else ""
            self.stdout.write(f"{slug}: valid. {summary}.{where} Nothing was changed (--check).")
            return
        if born:
            target.mkdir(parents=True)
            self.stdout.write(f"{slug}: a year started on the site. Its folder is {target}.")
        marker = target / MERGED
        version = note.get("version") or handin.version_of(files)
        if not write and not remove:
            marker.write_text(version + "\n", encoding="utf-8", newline="\n")
            self.stdout.write(f"{slug}: your files already hold everything in it.")
            return
        dirty = uncommitted(target)
        if dirty and not options["force"]:
            raise CommandError(
                f"{target} has uncommitted changes ({len(dirty)} files). Commit them "
                "first, so that what is taken in can be read and undone on its own, or "
                "pass --force."
            )
        for name in remove:
            (target / name).unlink()
        for name, data in write.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_bytes(data)
        marker.write_text(version + "\n", encoding="utf-8", newline="\n")

        self.stdout.write(self.style.SUCCESS(f"{slug}: taken in. {summary}."))
        for line in change.lost:
            self.stdout.write(f"  gone: {line}")
        self.stdout.write(
            "Next: read the diff in the courses folder, then\n"
            f"  manage.py sync_content {slug}\n"
            "commit there, and publish. The site then drops its own copy of the hand-in."
        )
