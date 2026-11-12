"""Send the course content to the live site.

    manage.py publish             validate everything, send it, load it on the server
    manage.py publish --dry-run   validate and pack, but send nothing

Nobody writes to the live database directly, not even this command. It sends *files*: every
course's `course.yaml`, `content/` folder and `duties.yaml` (the owner's own, which the
server tells only the owner's bot of, desk/duties.py), and its `private/` folder (the
owner's own material, which only the owner sees, ingest/private.py), packed into one
archive and piped over SSH to the server's deploy script. The server validates them again
with the same code, replaces its copy, and runs `sync_content` there. Raw material under
`source/` never leaves this computer.

The server is named by `--to user@host` or the DSAI_PUBLISH_SSH environment variable
(keep it in the git-ignored `.env` and run with `uv run --env-file .env ...`).
"""

import io
import os
import subprocess
import tarfile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingest import duties, private
from ingest.discover import clashes, find_courses
from ingest.management.commands.sync_content import safe_streams
from ingest.sync import sync_course

# What the SSH connection runs on the server, relative to the login user's home folder.
REMOTE_COMMAND = "dsai-vault/bin/deploy content"
MERGED = ".merged"  # see desk/handins.py


def pack(courses):
    """A .tar.gz, in memory, of `<slug>/course.yaml` and `<slug>/content/**` per course.

    Each course is packed under its own name, whatever folder it is grouped in here
    (`semester-5/rl/` is sent as `rl/`): the server keeps the courses side by side.

    Returns (bytes, number of files)."""
    buffer = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for slug, folder in sorted(courses.items()):
            paths = [folder / "course.yaml"]
            paths += sorted(p for p in (folder / "content").rglob("*") if p.is_file())
            # The note import_course leaves: which hand-in from the site these files
            # include. The server lets go of its own copy of that hand-in when it sees it.
            paths += [p for p in [folder / MERGED] if p.is_file()]
            paths += [p for p in [folder / duties.FILE] if p.is_file()]
            paths += private.files_in(folder if (folder / private.FOLDER).is_dir() else None)
            for path in paths:
                info = archive.gettarinfo(path, f"{slug}/{path.relative_to(folder).as_posix()}")
                # Whoever unpacks it owns the files; nothing about this computer is kept.
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
                count += 1
    return buffer.getvalue(), count


def send(target, archive):
    """Pipe the archive to the server's deploy script; its report prints as it arrives.

    Returns ssh's exit status: 0 only if the server validated and loaded everything."""
    # BatchMode: fail instead of waiting for a password that nobody will type.
    command = ["ssh", "-o", "BatchMode=yes", target, REMOTE_COMMAND]
    return subprocess.run(command, input=archive, check=False).returncode


class Command(BaseCommand):
    help = "Validate all course content and send it to the live site, which then loads it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            default=os.environ.get("DSAI_PUBLISH_SSH", ""),
            help="SSH address of the server, e.g. user@host (default: $DSAI_PUBLISH_SSH).",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Validate and pack, but send nothing."
        )

    def handle(self, *args, **options):
        safe_streams()
        # Only real courses: the example course is part of the code, not of the content.
        courses = find_courses([settings.COURSES_DIR])
        if not courses:
            raise CommandError(f"No courses found in {settings.COURSES_DIR}.")
        twice = clashes([settings.COURSES_DIR])
        if twice:
            raise CommandError(
                "Two folders with the same name, so it is unclear which is the course: "
                + ", ".join(sorted(twice))
                + ". Nothing was sent. Rename or remove one of each."
            )

        failed = 0
        for folder in courses.values():
            report = sync_course(folder, check_only=True)
            if report.errors:
                failed += 1
                self.stdout.write(self.style.ERROR(report.format()))
        if failed:
            raise CommandError(
                f"{failed} course{'s' * (failed != 1)} with errors. Nothing was sent. "
                "Fix the files listed above and run again."
            )

        archive, count = pack(courses)
        size = f"{len(archive) / 1024:.0f} KB"
        plural = "s" * (len(courses) != 1)
        summary = f"{len(courses)} course{plural}, {count} files, {size}"
        if options["dry_run"]:
            self.stdout.write(f"Valid. Would send {summary}. Nothing was sent (--dry-run).")
            return

        target = options["to"]
        if not target:
            raise CommandError(
                "No server given. Pass --to user@host, or set DSAI_PUBLISH_SSH in .env and "
                "run: uv run --env-file .env python manage.py publish"
            )
        self.stdout.write(f"Sending {summary} to {target} ...")
        self.stdout.flush()
        if send(target, archive) != 0:
            raise CommandError(
                "The server did not accept the content (its messages are above). "
                "The live site is unchanged unless it says otherwise."
            )
        self.stdout.write(self.style.SUCCESS("Published."))
