"""Bring everything that was changed on the site into your course files, as git history.

    manage.py take_in             every course that has steps your files lack
    manage.py take_in rl mlpr     these courses
    manage.py take_in --check     say what would be taken; change nothing
    manage.py take_in --from steps.json    from a saved `export_steps`, not from the server

Educators change a course on the site: a zip handed in, or a sitting of edits in the
editor. Each is a step in that course's history there (desk/handins.py). This command
asks the server for the steps your files do not have (`export_steps`, over the SSH
connection `publish` uses) and makes each one a commit in the `courses` repository:

    author    the person who made the step, by name
    date      when they made it
    message   the course, and what the step did: "rl: 1 new file, 12 lines added"

So the history that exists on the site exists in git as well, step by step, and `git log`
and `git diff` work on it. It is `import_course` for all of it at once, and it trusts
what arrives just as little:

1. A course is taken only if your files are the version its steps began from. If you
   changed the course yourself meanwhile, it is left alone and you are told to use
   `import_course`, which can put the two together.
2. Its folder must have no uncommitted changes.
3. Every step must arrive whole (its files give the version it says they are), contain
   nothing but a course's files, and the last one must pass the full validation here.

A year that was started on the site gets its folder made, beside the course it is a
year of. Nothing is pushed, published or loaded: read `git log`, run sync_content, and
publish. The site then lets go of its own copy of those steps.

An author is written as a name and an address that is not one (`…@educators.invalid`):
whose work a commit is belongs in the history; their e-mail address does not need to
leave the server for that.
"""

import base64
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ingest import handin
from ingest.discover import find_courses
from ingest.management.commands.import_course import uncommitted
from ingest.management.commands.publish import MERGED
from ingest.management.commands.sync_content import safe_streams
from ingest.schemas import SLUG_PATTERN
from ingest.sync import sync_course

SLUG = re.compile(SLUG_PATTERN)
REMOTE_COMMAND = "dsai-vault/bin/deploy manage export_steps"
BEGIN, END = "-----BEGIN DSAI STEPS-----", "-----END DSAI STEPS-----"


class Left(Exception):
    """A course that is not taken in. The message says why, and what to do instead."""


def fetch(target, slugs):
    """Ask the server for the steps. Returns what it printed."""
    # The slugs become part of a command line on the server: only plain ones get there.
    command = ["ssh", "-o", "BatchMode=yes", target, " ".join([REMOTE_COMMAND, *slugs])]
    done = subprocess.run(command, capture_output=True, check=False)
    if done.returncode != 0:
        raise CommandError(
            "The server did not answer:\n" + done.stderr.decode("utf-8", errors="replace").strip()
        )
    return done.stdout.decode("utf-8", errors="replace")


def parse(text):
    """The document between the two marker lines: (steps, {sha: bytes})."""
    try:
        body = text[text.index(BEGIN) + len(BEGIN) : text.index(END)]
        document = json.loads(body)
        steps = list(document["steps"])
        pool = {sha: base64.b64decode(data) for sha, data in document["blobs"].items()}
    except (ValueError, KeyError, TypeError) as error:
        raise CommandError("What arrived is not a list of steps (export_steps).") from error
    return steps, pool


def git(folder, *args, check=True):
    done = subprocess.run(
        ["git", "-C", str(folder), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and done.returncode != 0:
        raise CommandError(f"git {' '.join(args[:2])} failed:\n{done.stderr.strip()}")
    return done


def author(name):
    """A step's author for git: their name, and an address that is not anyone's."""
    plain = re.sub(r"[<>\r\n]", "", name or "").strip() or "An educator"
    local = re.sub(r"[^a-z0-9]+", ".", plain.lower()).strip(".") or "educator"
    return f"{plain} <{local}@educators.invalid>"


class Command(BaseCommand):
    help = "Commit each step made on the site into the courses repository."

    def add_arguments(self, parser):
        parser.add_argument("courses", nargs="*", help="Course slugs (default: all).")
        parser.add_argument(
            "--from",
            dest="source",
            help="A file holding the output of `export_steps`, instead of asking the server.",
        )
        parser.add_argument(
            "--to",
            default=os.environ.get("DSAI_PUBLISH_SSH", ""),
            help="SSH address of the server, e.g. user@host (default: $DSAI_PUBLISH_SSH).",
        )
        parser.add_argument("--check", action="store_true", help="Say what would be taken only.")

    def handle(self, *args, **options):
        safe_streams()
        slugs = options["courses"]
        for slug in slugs:
            if not SLUG.match(slug):
                raise CommandError(f"`{slug}` is not the name of a course.")
        root = Path(settings.COURSES_DIR)
        if git(root, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
            raise CommandError(f"{root} is not a git repository: there is no history to add to.")

        if options["source"]:
            text = Path(options["source"]).read_text(encoding="utf-8")
        elif options["to"]:
            self.stdout.write(f"Asking {options['to']} for what was changed on the site ...")
            text = fetch(options["to"], slugs)
        else:
            raise CommandError(
                "No server given. Pass --to user@host, or set DSAI_PUBLISH_SSH in .env and "
                "run: uv run --env-file .env python manage.py take_in"
            )
        steps, pool = parse(text)
        by_course = {}
        for step in steps:
            if not slugs or step.get("slug") in slugs:
                by_course.setdefault(str(step.get("slug")), []).append(step)
        if not by_course:
            self.stdout.write("Nothing to take in: your files have every step there is.")
            return

        taken = 0
        for slug, chain in sorted(by_course.items()):
            try:
                taken += self.take(root, slug, chain, pool, check=options["check"])
            except Left as reason:
                self.stdout.write(self.style.WARNING(f"{slug}: left as it is. {reason}"))
        if taken and not options["check"]:
            self.stdout.write(
                "Next: read `git log` in the courses folder, then\n"
                "  manage.py sync_content\n"
                "and publish. The site then drops its own copy of these steps."
            )

    def take(self, root, slug, chain, pool, *, check):
        """Commit the steps of one course. Returns how many commits that made (or would)."""
        if not SLUG.match(slug):
            raise Left("Its name is not a course's name.")
        here = find_courses([root])
        folder = here.get(slug)
        born = folder is None
        versions = []
        for step in chain:
            try:
                files = {name: pool[sha] for name, sha in step["files"].items()}
                handin.check(files)
            except (KeyError, TypeError, AttributeError, handin.HandInError) as error:
                raise Left(f"Step {step.get('id')} is not a course's files: {error}.") from error
            if handin.version_of(files) != step.get("version"):
                raise Left(f"Step {step.get('id')} did not arrive whole.")
            versions.append(files)

        if born:
            if chain[0].get("base"):
                raise Left("There is no such course here.")
            beside = here.get(handin.series_of(versions[0]["course.yaml"]))
            if beside is None:
                raise Left("It is a new year of a course that is not here.")
            folder = beside.parent / slug
            mine = {}
        else:
            mine = handin.files_of(folder)
            if handin.version_of(mine) != chain[0].get("base"):
                raise Left(
                    "You changed this course yourself since its steps on the site began. "
                    "Download the newest step in the admin and use import_course, which "
                    "can put the two together (--theirs, --mine)."
                )
            if uncommitted(folder):
                raise Left("Its folder has uncommitted changes. Commit them first.")

        with tempfile.TemporaryDirectory(prefix="dsai-take-in-") as scratch:
            staged = Path(scratch) / slug
            for name, data in versions[-1].items():
                (staged / name).parent.mkdir(parents=True, exist_ok=True)
                (staged / name).write_bytes(data)
            report = sync_course(staged, check_only=True)
        if report.errors:
            self.stdout.write(self.style.ERROR(report.format()))
            raise Left("Its newest step is not valid here (above).")

        if check:
            self.stdout.write(f"{slug}: {len(chain)} step{'s' * (len(chain) != 1)} would be taken.")
            for step in chain:
                self.stdout.write(f"  {step['at'][:16]}  {step['by']}: {step['summary']}")
            return len(chain)

        made = 0
        for position, (step, files) in enumerate(zip(chain, versions, strict=True)):
            for name in set(mine) - set(files):
                (folder / name).unlink()
            for name, data in files.items():
                if mine.get(name) != data:
                    (folder / name).parent.mkdir(parents=True, exist_ok=True)
                    (folder / name).write_bytes(data)
            if position == len(chain) - 1:
                # Which step these files now include: `publish` sends it along, and the
                # site lets go of its own copy when it sees it.
                (folder / MERGED).write_text(step["version"] + "\n", encoding="utf-8", newline="\n")
            mine = files
            git(root, "add", "--all", "--", str(folder))
            if not git(root, "status", "--porcelain", "--", str(folder)).stdout.strip():
                continue  # a step that ended where it began
            when = dt.datetime.fromisoformat(step["at"]).isoformat(timespec="seconds")
            how = "edited on the site" if step.get("via") == "site" else "handed in as a zip"
            git(
                root,
                "commit",
                "--quiet",
                f"--author={author(step.get('by'))}",
                f"--date={when}",
                "-m",
                f"{slug}: {step['summary']}",
                "-m",
                f"Step {step['id']} of this course on the site, {how}.",
                "--",
                str(folder),
            )
            made += 1
        note = " (a year that was started on the site; its folder is new)" if born else ""
        self.stdout.write(self.style.SUCCESS(f"{slug}: {made} commit{'s' * (made != 1)}{note}."))
        return made
