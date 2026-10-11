"""Take in the course files an educator handed in.

    manage.py import_course rl-20261012-1830.zip

The zip was made on someone else's computer by the educator kit (`tools/pack.py`). This
command treats it as untrusted, and changes nothing unless every check passes:

1. It must be plainly one course that exists here: course.yaml and content/, nothing else
   (ingest/handin.py).
2. Its course.yaml may differ from yours only in topics and description. Title, code,
   year, whether the course is open, and the rest stay yours.
3. It is unpacked to a temporary folder and run through the site's own validation, the
   full one: links between files and maths included.
4. The course folder must have no uncommitted changes, so that what was handed in is a
   diff of its own that you can read and undo.

Then the course's content/ is replaced with what was handed in. Nothing is loaded into the
database and nothing is published: look at the diff, run sync_content, commit, publish.
"""

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingest import handin
from ingest.discover import find_courses
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
    help = "Validate a course zip handed in by an educator and put it into the course folder."

    def add_arguments(self, parser):
        parser.add_argument("zip", help="The file made by the kit's tools/pack.py.")
        parser.add_argument(
            "--check", action="store_true", help="Validate only; change nothing here."
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Go ahead although the course folder has uncommitted changes.",
        )

    def handle(self, *args, **options):
        safe_streams()
        source = Path(options["zip"])
        if not source.is_file():
            raise CommandError(f"{source} is not a file.")
        try:
            with zipfile.ZipFile(source) as archive:
                slug, members = handin.inspect(archive)
                files = {name: handin.read(archive, info) for name, info in members.items()}
        except zipfile.BadZipFile as error:
            raise CommandError(f"{source.name} is not a zip file.") from error
        except handin.HandInError as error:
            raise CommandError(f"Refused: {error}. Nothing was changed.") from error

        target = find_courses([settings.COURSES_DIR]).get(slug)
        if target is None:
            raise CommandError(
                f"There is no course `{slug}` here. A hand-in can change a course, not "
                "create one: make it with startcourse first."
            )
        yours = (target / "course.yaml").read_text(encoding="utf-8")
        theirs = files["course.yaml"].decode("utf-8", errors="replace")
        changed = handin.protected_changes(yours, theirs)
        if changed:
            raise CommandError(
                "Refused: course.yaml changes what is not the educator's to change.\n  "
                + "\n  ".join(changed)
                + "\nNothing was changed. Ask them to restore those lines, or apply the "
                "rest by hand."
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
                raise CommandError(
                    "The hand-in is not valid (above). Nothing was changed. Send that "
                    "report back to the educator."
                )
            for issue in report.warnings:
                self.stdout.write(self.style.WARNING(issue.format()))

            before = {name: path.read_bytes() for path, name in handin.course_files(target)}
            added = sorted(set(files) - set(before))
            removed = sorted(set(before) - set(files))
            edited = sorted(
                name for name in set(files) & set(before) if files[name] != before[name]
            )
            summary = f"{len(added)} new, {len(edited)} changed, {len(removed)} removed"
            if options["check"]:
                self.stdout.write(f"{slug}: valid. {summary}. Nothing was changed (--check).")
                return
            if not (added or edited or removed):
                self.stdout.write(f"{slug}: nothing differs from what is here.")
                return

            dirty = uncommitted(target)
            if dirty and not options["force"]:
                raise CommandError(
                    f"{target} has uncommitted changes ({len(dirty)} files). Commit them "
                    "first, so that this hand-in can be read and undone on its own, or "
                    "pass --force."
                )
            # File by file, and only the kinds of file that travel: anything else that
            # sits in the course folder (raw material, notes) is not touched.
            for name in removed:
                (target / name).unlink()
            for name in [*added, *edited]:
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staged / name, target / name)

        self.stdout.write(self.style.SUCCESS(f"{slug}: taken in. {summary}."))
        for name in removed:
            self.stdout.write(f"  removed  {name}")
        self.stdout.write(
            "Next: read the diff in the courses folder, then\n"
            f"  manage.py sync_content {slug}\n"
            "commit there, and publish when you are ready."
        )
