"""`export_steps` on the server and `take_in` on the owner's computer: what was changed on
the site becomes commits in the courses repository, one per step."""

import datetime as dt
import io
import json
import subprocess
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from catalog.models import Course
from desk import handins
from desk.models import HandIn
from ingest import handin, live
from ingest.management.commands import take_in
from ingest.sync import sync_all

from .test_handins import DECK, NEW_CARD, NEW_GUIDE, SHEET, C, HandInCase

GUIDE = "content/guides/a-second-look.md"


def run(*args):
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


class TakeInCase(HandInCase):
    """The courses folder of a test is made a git repository, as the owner's is."""

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "courses"
        self.git("init", "--quiet")
        self.git("config", "user.name", "The Owner")
        self.git("config", "user.email", "owner@example.test")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "The course as published")
        self.subject = Course.objects.get(slug=C)
        self.ada.last_name = "Lovelace"
        self.ada.save()

    def git(self, *args):
        done = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        return done.stdout

    def change(self, user, name, text, *, at=None):
        def edit(files):
            files[name] = files.get(name, b"") + text.encode()
            return files

        step, _fresh = handins.change(user, self.subject, edit)
        if at is not None:  # as if made then: steps that lie apart are steps of their own
            HandIn.objects.filter(pk=step.pk).update(created_at=at, updated_at=at)
        return HandIn.objects.get(pk=step.pk)

    def three_steps(self):
        then = timezone.now() - dt.timedelta(days=2)
        first = self.change(self.ada, DECK, NEW_CARD, at=then)
        second = self.change(
            self.bob, SHEET, "\nOne more line from Bob.\n", at=then + dt.timedelta(hours=3)
        )
        third = self.change(self.ada, GUIDE, NEW_GUIDE)
        return first, second, third

    def exported(self, *slugs):
        path = self.tmp / "steps.json"
        path.write_text(run("export_steps", *slugs), encoding="utf-8")
        return str(path)

    def log(self):
        return [line.split("|") for line in self.git("log", "--format=%an|%ae|%s").splitlines()]


class ExportTests(TakeInCase):
    def test_it_lists_the_steps_not_yet_merged_with_each_file_once(self):
        first, second, third = self.three_steps()
        steps, pool = take_in.parse(run("export_steps"))
        self.assertEqual([step["id"] for step in steps], [first.pk, second.pk, third.pk])
        self.assertEqual((steps[0]["by"], steps[1]["by"]), ("Ada Lovelace", "Bob"))
        self.assertEqual(steps[0]["base"], handin.content_version(self.course))
        self.assertEqual(steps[2]["summary"], "1 new file")
        files = {name: pool[sha] for name, sha in steps[1]["files"].items()}
        self.assertEqual(handin.version_of(files), second.version)
        # Every file of the course as the first step left it, and the two that came after.
        self.assertEqual(len(pool), len(handin.files_of(self.course)) + 2)
        self.assertNotIn("@", json.dumps(steps))  # a name, never an address
        # What is merged or taken back is not exported; nor another course.
        HandIn.objects.filter(pk=third.pk).update(status="withdrawn")
        self.assertEqual(len(take_in.parse(run("export_steps"))[0]), 2)
        self.assertEqual(take_in.parse(run("export_steps", "another"))[0], [])
        self.assertEqual(HandIn.objects.filter(status="live").count(), 2)  # it only reads


class TakeInTests(TakeInCase):
    def test_each_step_becomes_a_commit_by_its_author_at_its_time(self):
        first, _second, third = self.three_steps()
        source = self.exported()
        self.assertIn("3 steps would be taken", run("take_in", "--from", source, "--check"))
        self.assertEqual(len(self.log()), 1)  # --check changed nothing
        text = run("take_in", "--from", source)
        self.assertIn(f"{C}: 3 commits.", text)
        self.assertIn("sync_content", text)
        self.assertEqual(
            self.log(),
            [
                ["Ada Lovelace", "ada.lovelace@educators.invalid", f"{C}: 1 new file"],
                ["Bob", "bob@educators.invalid", f"{C}: 2 lines added"],
                ["Ada Lovelace", "ada.lovelace@educators.invalid", f"{C}: 3 lines added"],
                ["The Owner", "owner@example.test", "The course as published"],
            ],
        )
        # The owner made the commits; each carries the day its step was made.
        self.assertEqual(self.git("log", "-1", "--format=%cn").strip(), "The Owner")
        day = self.git("log", "--format=%ad", "--date=short").splitlines()[2]
        self.assertEqual(day, timezone.localtime(first.created_at).date().isoformat())
        self.assertIn(f"Step {first.pk} of this course on the site", self.git("log", "--format=%b"))
        # One commit holds one step's changes, and the files are now the newest version.
        self.assertEqual(self.git("show", "--stat", "--format=", "HEAD~2").count("|"), 1)
        self.assertEqual(handin.content_version(self.course), third.version)
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.assertEqual((self.course / ".merged").read_text("utf-8").strip(), third.version)
        # Published, the site reads the owner's files again and lets go of its copies.
        sync_all()
        self.assertEqual(live.folder(C), self.course)
        self.assertEqual(set(HandIn.objects.values_list("status", flat=True)), {"merged"})
        self.assertIn("Nothing to take in", run("take_in", "--from", self.exported()))

    def test_a_course_the_owner_changed_meanwhile_is_left_to_import_course(self):
        self.three_steps()
        source = self.exported()
        self.append(SHEET, "\nThe owner's own line.\n")
        self.git("commit", "--quiet", "-am", "Mine")
        text = run("take_in", "--from", source)
        self.assertIn("left as it is. You changed this course yourself", text)
        self.assertIn("import_course", text)
        self.assertEqual(len(self.log()), 2)
        self.assertNotIn("lsq-extra", self.read(DECK))

    def test_uncommitted_work_in_the_folder_stops_it(self):
        self.change(self.ada, DECK, NEW_CARD)
        source = self.exported()
        # Something of the owner's own in the folder, which is not part of the course.
        (self.course / "notes-to-self.txt").write_text("unfinished", encoding="utf-8")
        text = run("take_in", "--from", source)
        self.assertIn("uncommitted changes", text)
        self.assertEqual(len(self.log()), 1)
        self.assertNotIn("lsq-extra", self.read(DECK))

    def test_what_did_not_arrive_whole_or_is_not_a_course_is_not_written(self):
        self.change(self.ada, DECK, NEW_CARD)
        document = run("export_steps")
        steps, _pool = take_in.parse(document)
        sha = steps[0]["files"][DECK]
        broken = self.tmp / "broken.json"
        body = json.loads(document.split(take_in.BEGIN)[1].split(take_in.END)[0])
        body["blobs"][sha] = body["blobs"][steps[0]["files"][SHEET]]
        broken.write_text(take_in.BEGIN + json.dumps(body) + take_in.END, encoding="utf-8")
        self.assertIn("did not arrive whole", run("take_in", "--from", str(broken)))
        body = json.loads(document.split(take_in.BEGIN)[1].split(take_in.END)[0])
        body["steps"][0]["files"]["../../escaped.md"] = sha
        broken.write_text(take_in.BEGIN + json.dumps(body) + take_in.END, encoding="utf-8")
        self.assertIn("is not a course's files", run("take_in", "--from", str(broken)))
        self.assertFalse((self.tmp / "escaped.md").exists())
        broken.write_text("no markers here", encoding="utf-8")
        with self.assertRaisesMessage(CommandError, "not a list of steps"):
            run("take_in", "--from", str(broken))
        self.assertEqual((len(self.log()), self.git("status", "--porcelain").strip()), (1, ""))

    def test_a_year_started_on_the_site_gets_its_folder_and_its_history(self):
        files = handin.files_of(self.course)
        about = files["course.yaml"].replace(
            b'year: "2026-27"', b'year: "2025-26"\nseries: example'
        )
        handins.begin(self.ada, C, "example-2025", {**files, "course.yaml": about})
        text = run("take_in", "--from", self.exported())
        self.assertIn("example-2025: 1 commit (a year that was started on the site", text)
        folder = self.repo / "example-2025"
        self.assertTrue((folder / "course.yaml").is_file())
        self.assertEqual(self.log()[0][2], f"example-2025: {len(files)} new files")
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        sync_all()
        self.assertEqual(live.folder("example-2025"), folder)

    def test_it_asks_the_server_the_way_publish_reaches_it(self):
        self.change(self.ada, DECK, NEW_CARD)
        answer = mock.Mock(returncode=0, stdout=run("export_steps").encode(), stderr=b"")
        real = subprocess.run

        def fake(command, **options):
            return answer if command[0] == "ssh" else real(command, **options)

        with mock.patch(
            "ingest.management.commands.take_in.subprocess.run", side_effect=fake
        ) as ran:
            text = run("take_in", C, "--to", "me@server.test")
        asked = next(call.args[0] for call in ran.call_args_list if call.args[0][0] == "ssh")
        self.assertEqual(
            asked,
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "me@server.test",
                f"dsai-vault/bin/deploy manage export_steps {C}",
            ],
        )
        self.assertIn(f"{C}: 1 commit.", text)
        # Only the name of a course ever becomes part of that command line.
        for bad in ("x; rm -rf ~", "$(whoami)", "a b", "../x"):
            with (
                self.subTest(bad=bad),
                self.assertRaisesMessage(CommandError, "not the name of a course"),
            ):
                run("take_in", bad, "--to", "me@server.test")
        with self.assertRaisesMessage(CommandError, "No server given"):
            run("take_in", "--to", "")

    def test_the_courses_folder_must_be_a_repository(self):
        import shutil

        shutil.rmtree(self.repo / ".git", ignore_errors=True)
        with self.assertRaisesMessage(CommandError, "not a git repository"):
            run("take_in", "--from", "anything.json")
