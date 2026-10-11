import io
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from catalog.models import Course
from ingest import handin
from ingest.fields import render_markdown
from ingest.management.commands.export_schemas import FIELDS, TARGET, render
from ingest.management.commands.publish import REMOTE_COMMAND, send
from ingest.schemas import SCHEMAS

from .base import CourseTestCase


def run(*args):
    out = StringIO()
    call_command(*args, stdout=out, stderr=out)
    return out.getvalue()


class SyncCommandTests(CourseTestCase):
    def test_sync_all_then_report_unchanged(self):
        self.assertIn("example: synced", run("sync_content"))
        self.assertIn("example: unchanged", run("sync_content"))

    def test_check_writes_nothing(self):
        self.assertIn("valid (check only", run("sync_content", "example", "--check"))
        self.assertFalse(Course.objects.exists())

    def test_errors_make_the_command_fail(self):
        self.edit("content/decks/least-squares.yaml", "topic: lsq", "topic: nope")
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("sync_content", stdout=out)
        text = out.getvalue()
        self.assertIn("FAILED - database not updated", text)
        self.assertRegex(text, r"content/decks/least-squares\.yaml:\d+\s+topic")

    def test_unknown_course(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("sync_content", "nosuch", stdout=out)
        self.assertIn("startcourse nosuch", out.getvalue())

    def test_courses_are_found_in_semester_folders(self):
        grouped = settings.COURSES_DIR / "semester-1"
        grouped.mkdir()
        self.course.rename(grouped / "example")
        (settings.COURSES_DIR / "notes").mkdir()  # a folder that is not a course: ignored
        self.assertIn("example: synced", run("sync_content"))
        self.assertEqual(Course.objects.get().slug, "example")
        self.assertIn("example: unchanged", run("sync_content", "example"))
        self.assertIn("example", run("content_status"))

    def test_moving_a_course_between_folders_keeps_it_the_same_course(self):
        run("sync_content")
        before = Course.objects.get(slug="example")
        grouped = settings.COURSES_DIR / "semester-1"
        grouped.mkdir()
        self.course.rename(grouped / "example")
        self.assertIn("example: unchanged", run("sync_content"))
        self.assertEqual(Course.objects.get(slug="example").pk, before.pk)

    def test_two_folders_with_one_name_are_an_error_and_neither_is_loaded(self):
        run("sync_content")
        shutil.copytree(self.course, settings.COURSES_DIR / "semester-1" / "example")
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("sync_content", stdout=out)
        self.assertIn("two folders are named `example`", out.getvalue())
        # What was loaded before stays: an unclear folder is not a removed course.
        self.assertTrue(Course.objects.filter(slug="example").exists())

    def test_removed_folder_is_removed_from_the_database(self):
        run("sync_content")
        (self.course / "course.yaml").unlink()
        self.assertIn("removed from the database", run("sync_content"))
        self.assertFalse(Course.objects.exists())


class StartCourseTests(CourseTestCase):
    def test_new_course_is_valid_and_empty(self):
        run("startcourse", "rl", "--title", "Reinforcement Learning: an intro", "--year", "2026-27")
        folder = settings.COURSES_DIR / "rl"
        for name in (
            "source/lectures",
            "source/homework",
            "content/decks",
            "content/memory/palaces",
        ):
            self.assertTrue((folder / name).is_dir(), name)
        self.assertIn("rl: synced", run("sync_content", "rl"))
        course = Course.objects.get(slug="rl")
        self.assertEqual(course.title, "Reinforcement Learning: an intro")
        self.assertEqual((course.code, course.year), ("RL", "2026-27"))

    def test_semester_is_written_and_read_back(self):
        run("startcourse", "dms", "--title", "Dynamic Modeling", "--semester", "5")
        run("sync_content", "dms")
        self.assertEqual(Course.objects.get(slug="dms").semester, 5)
        # Left out, the field stays empty rather than becoming 0.
        run("startcourse", "aai", "--title", "Applied AI")
        run("sync_content", "aai")
        self.assertIsNone(Course.objects.get(slug="aai").semester)

    def test_another_year_of_a_course_names_its_series(self):
        run("startcourse", "example-2027", "--title", "Example", "--year", "2027-28",
            "--series", "example")  # fmt: skip
        run("sync_content", "example-2027")
        self.assertEqual(Course.objects.get(slug="example-2027").series, "example")
        with self.assertRaises(CommandError):
            run("startcourse", "x", "--title", "X", "--series", "Not A Folder")

    def test_refuses_to_overwrite(self):
        with self.assertRaises(CommandError):
            run("startcourse", "example", "--title", "Again")

    def test_a_course_can_start_closed_or_with_someone_writing_it(self):
        run("startcourse", "nlp", "--title", "NLP", "--closed")
        run("startcourse", "rl", "--title", "RL", "--has-educator")
        run("startcourse", "aai", "--title", "Applied AI")
        run("sync_content")
        flags = {c.slug: (c.enabled, c.has_educator) for c in Course.objects.all()}
        self.assertEqual(flags["nlp"], (False, False))
        self.assertEqual(flags["rl"], (True, True))
        self.assertEqual(flags["aai"], (True, False))
        self.assertEqual(flags["example"], (True, True))  # as its course.yaml says

    def test_where_semester_folders_are_used_a_new_course_goes_into_its_own(self):
        home = settings.COURSES_DIR
        # No semester folders yet: the course sits directly in the courses folder.
        run("startcourse", "rl", "--title", "RL", "--semester", "5")
        self.assertTrue((home / "rl" / "course.yaml").is_file())
        (home / "semester-5").mkdir()
        (home / "rl").rename(home / "semester-5" / "rl")
        # Now there are: the next one follows.
        run("startcourse", "nlp", "--title", "NLP", "--semester", "6")
        self.assertTrue((home / "semester-6" / "nlp" / "course.yaml").is_file())
        run("startcourse", "misc", "--title", "No semester")
        self.assertTrue((home / "misc" / "course.yaml").is_file())
        # Its address is its own name wherever it sits, so the name is taken everywhere.
        with self.assertRaises(CommandError):
            run("startcourse", "rl", "--title", "Again", "--semester", "4")
        run("sync_content")
        self.assertCountEqual(
            Course.objects.values_list("slug", flat=True), ["example", "rl", "nlp", "misc"]
        )

    def test_rejects_a_bad_slug(self):
        with self.assertRaises(CommandError):
            run("startcourse", "Bad Slug", "--title", "X")


class StatusCommandTests(CourseTestCase):
    def test_lists_source_files_nothing_refers_to(self):
        (self.course / "source/lectures").mkdir(parents=True)
        (self.course / "source/lectures/L1.pdf").write_bytes(b"%PDF")
        (self.course / "source/lectures/L2.pdf").write_bytes(b"%PDF")
        self.edit(
            "content/lectures/01-least-squares.md",
            "topics: [lsq]\n",
            "topics: [lsq]\nsources: [lectures/L1.pdf]\n",
        )
        run("sync_content")
        text = run("content_status", "example")
        self.assertIn("not written up yet (1)", text)
        self.assertIn("lectures/L2.pdf", text)
        self.assertNotIn("    lectures/L1.pdf", text)


class EducatorKitTests(CourseTestCase):
    """What educators are given: a kit with the instructions, the docs and two tools that
    run with Python alone, and no course content. Their own course sits in the kit's
    courses/ folder, which git ignores."""

    def setUp(self):
        super().setUp()
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        self.exported = run("export_authoring", str(self.kit))
        # An educator unpacks the files of their course into the kit.
        shutil.copytree(self.course, self.kit / "courses" / "example")
        self.course = self.kit / "courses" / "example"

    def tool(self, name, *args):
        # A separate Python process, as an educator would start it. -I keeps it from
        # picking up this project through the environment.
        command = [sys.executable, "-I", str(self.kit / "tools" / name), *args]
        done = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", cwd=self.kit, check=False
        )
        return done.returncode, done.stdout + done.stderr

    def checker(self, *args):
        return self.tool("check.py", *args)

    def test_the_kit_is_complete_and_holds_no_course(self):
        self.assertIn("Wrote the educator kit", self.exported)
        for name in (
            "README.md",
            "AGENTS.md",
            "CLAUDE.md",
            ".gitignore",
            ".gitattributes",
            ".claude/skills/update-cms/SKILL.md",
            "courses/README.md",
            "docs/README.md",
            "docs/AUTHORING.md",
            "docs/content-types/fields.md",
            "docs/schemas/course.schema.json",
            "docs/example-course/example/course.yaml",
            "tools/check.py",
            "tools/pack.py",
            "tools/rules/__init__.py",
            "tools/rules/handin.py",
            "tools/rules/schemas.py",
        ):
            self.assertTrue((self.kit / name).is_file(), name)
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        run("export_authoring", str(fresh))
        self.assertEqual([p.name for p in (fresh / "courses").iterdir()], ["README.md"])

    def test_everything_an_educator_writes_is_ignored_by_git(self):
        rules = (self.kit / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn("/courses/*", rules)
        self.assertIn("!/courses/README.md", rules)
        self.assertIn("/out/", rules)

    def test_the_readme_says_how_the_kit_is_used(self):
        readme = (self.kit / "README.md").read_text(encoding="utf-8")
        for said in (
            "holds no course content",
            "git pull",
            "Download the course files",
            "Update the CMS for this course: rl",
            "uv run tools/pack.py rl",
            "source/",
        ):
            self.assertIn(said, readme)

    def test_what_the_tools_run_is_the_importer_itself_and_needs_no_django(self):
        from ingest.management.commands.export_authoring import HEADER, RULES

        for name in RULES:
            with self.subTest(module=name):
                copied = (self.kit / "tools" / "rules" / name).read_text(encoding="utf-8")
                original = (settings.BASE_DIR / "ingest" / name).read_text(encoding="utf-8")
                self.assertEqual(copied, HEADER + original)
                self.assertNotIn("import django", original)
                self.assertNotIn("from django", original)

    def test_it_passes_a_valid_course(self):
        status, text = self.checker("example")
        self.assertEqual(status, 0, msg=text)
        self.assertIn("example: structure is valid", text)
        self.assertIn("2 lectures", text)
        self.assertIn("10 cards", text)
        # With no course named it checks every course in courses/.
        self.assertIn("example: structure is valid", self.checker()[1])

    def test_it_reports_problems_the_way_the_importer_does(self):
        self.edit("content/decks/least-squares.yaml", "topic: lsq", "topic: nope")
        self.append("content/reference/glossary.yaml", "colour: blue\n")
        deck = self.read("content/decks/gradient-descent.yaml")
        first = next(line for line in deck.split("\n") if line.strip().startswith("- id:"))
        self.append("content/decks/gradient-descent.yaml", f"{first}\n    q: Again?\n    a: Yes.\n")

        status, text = self.checker("example")
        self.assertEqual(status, 1, msg=text)
        self.assertIn("example: FAILED", text)
        line = self.line_of("content/decks/least-squares.yaml", "topic: nope")
        self.assertIn(f"content/decks/least-squares.yaml:{line}  topic", text)
        mistakes = (
            "no topic named `nope` in this course",
            "unknown field `colour`",
            "is already used at content/decks/gradient-descent.yaml",
        )
        for mistake in mistakes:
            self.assertIn(mistake, text)
        # The importer says the same three things in the same words. (It says more: with
        # the glossary refused, links to its terms fail too, which only it can see.)
        full = self.check().format()
        for mistake in mistakes:
            self.assertIn(mistake, full)
        self.assertIn(f"content/decks/least-squares.yaml:{line}  topic", full)
        # And a course with errors is not packed.
        status, text = self.tool("pack.py", "example")
        self.assertEqual(status, 1)
        self.assertIn("Not packed", text)
        self.assertFalse((self.kit / "out").exists())

    def test_it_lists_what_is_left_to_write(self):
        (self.course / "source/lectures").mkdir(parents=True)
        (self.course / "source/lectures/L1.pdf").write_bytes(b"%PDF")
        (self.course / "source/lectures/L2.pdf").write_bytes(b"%PDF")
        self.edit(
            "content/lectures/01-least-squares.md",
            "topics: [lsq]\n",
            "topics: [lsq]\nsources: [lectures/L1.pdf]\n",
        )
        status, text = self.checker("--status", "example")
        self.assertEqual(status, 0, msg=text)
        self.assertIn("not written up yet (1)", text)
        self.assertIn("lectures/L2.pdf", text)
        self.assertNotIn("    lectures/L1.pdf", text)

    def test_a_course_that_is_not_there_is_refused_with_what_to_do(self):
        status, text = self.checker("nothing")
        self.assertEqual(status, 1)
        self.assertIn("no course folder courses/nothing", text)
        self.assertIn("download your course", text)

    def test_packing_makes_one_file_without_the_raw_material(self):
        (self.course / "source/lectures").mkdir(parents=True)
        (self.course / "source/lectures/L1.pdf").write_bytes(b"%PDF")
        (self.course / "content/notes.txt").write_text("to self", encoding="utf-8")
        status, text = self.tool("pack.py", "example")
        self.assertEqual(status, 0, msg=text)
        (packed,) = (self.kit / "out").glob("example-*.zip")
        with zipfile.ZipFile(packed) as archive:
            names = archive.namelist()
        self.assertIn("example/course.yaml", names)
        self.assertIn("example/content/decks/least-squares.yaml", names)
        self.assertIn("example/content/lectures/assets/residuals.svg", names)
        self.assertEqual([n for n in names if "source" in n or n.endswith(".txt")], [])

    def test_exporting_again_replaces_its_own_files_and_never_a_course(self):
        (self.kit / "docs/AUTHORING.md").write_text("edited by hand", encoding="utf-8")
        (self.kit / "tools/rules/left-over.py").write_text("old", encoding="utf-8")
        before = self.read("content/decks/least-squares.yaml")
        run("export_authoring", str(self.kit))
        self.assertIn(
            "# Writing course content", (self.kit / "docs/AUTHORING.md").read_text("utf-8")
        )
        self.assertFalse((self.kit / "tools/rules/left-over.py").exists())
        self.assertEqual(self.read("content/decks/least-squares.yaml"), before)

    def test_it_will_not_write_into_the_application_or_its_courses(self):
        for target in (settings.BASE_DIR, settings.COURSES_DIR, self.kit / "no-such-folder"):
            with self.subTest(target=target), self.assertRaises(CommandError):
                run("export_authoring", str(target))


class HandInTests(CourseTestCase):
    """A course comes back from an educator as a zip (ingest/handin.py) and is taken in
    with `import_course`, which trusts nothing in it."""

    def setUp(self):
        super().setUp()
        # What the educator has: a copy of the course, somewhere else.
        self.theirs = self.tmp / "educator" / "example"
        shutil.copytree(self.course, self.theirs)

    def packed(self, name="handin.zip"):
        target = self.tmp / name
        target.write_bytes(handin.pack(self.theirs))
        return str(target)

    def raw_zip(self, files):
        target = self.tmp / "made-by-hand.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return str(target)

    def refused(self, *args):
        with self.assertRaises(CommandError) as raised:
            run("import_course", *args)
        return str(raised.exception)

    def test_what_they_wrote_arrives_and_nothing_else_changes(self):
        deck = self.theirs / "content/decks/least-squares.yaml"
        deck.write_text(
            deck.read_text("utf-8").replace("Least squares", "Least squares, again", 1), "utf-8"
        )
        # A guide that is here and that the educator has dropped.
        self.write("content/guides/old-note.md", "---\ntitle: An old note\n---\n\nGone soon.\n")
        new = self.theirs / "content/guides/a-second-look.md"
        new.write_text(
            "---\ntitle: A second look\ntopics: [lsq]\n---\n\nSomething more.\n", "utf-8"
        )
        (self.course / "source").mkdir()
        (self.course / "source/L1.pdf").write_bytes(b"%PDF")  # the owner's own raw material

        checked = run("import_course", self.packed(), "--check")
        self.assertIn("1 new, 1 changed, 1 removed", checked)
        self.assertIn("Nothing was changed", checked)
        self.assertNotIn("again", self.read("content/decks/least-squares.yaml"))

        text = run("import_course", self.packed())
        self.assertIn("example: taken in. 1 new, 1 changed, 1 removed", text)
        self.assertIn("removed  content/guides/old-note.md", text)
        self.assertIn("again", self.read("content/decks/least-squares.yaml"))
        self.assertTrue((self.course / "content/guides/a-second-look.md").is_file())
        self.assertFalse((self.course / "content/guides/old-note.md").exists())
        self.assertTrue((self.course / "source/L1.pdf").is_file())
        self.assertFalse(Course.objects.exists(), "taking in must not touch the database")
        self.assertIn("nothing differs", run("import_course", self.packed()))

    def test_topics_and_the_description_are_theirs_to_change(self):
        course = self.theirs / "course.yaml"
        text = course.read_text("utf-8").replace(
            "description: Least squares", "description: Now least squares"
        )
        course.write_text(text + "  - id: extra\n    title: An extra topic\n", "utf-8")
        self.assertIn("taken in", run("import_course", self.packed()))
        self.assertIn("id: extra", self.read("course.yaml"))

    def test_the_rest_of_course_yaml_is_not(self):
        course = self.theirs / "course.yaml"
        for old, new, said in (
            ("code: EX101", "code: MINE", "`code` was 'EX101' and is now 'MINE'"),
            ("enabled: true", "enabled: false", "`enabled` was True and is now False"),
            ("  - id: lsq", "  - id: least", "topics removed or renamed: lsq"),
        ):
            with self.subTest(change=new):
                original = course.read_text("utf-8")
                course.write_text(original.replace(old, new, 1), "utf-8")
                message = self.refused(self.packed())
                self.assertIn(said, message)
                self.assertIn("Nothing was changed", message)
                course.write_text(original, "utf-8")
        self.assertIn("code: EX101", self.read("course.yaml"))

    def test_content_the_site_would_refuse_is_not_taken_in(self):
        deck = self.theirs / "content/decks/least-squares.yaml"
        deck.write_text(deck.read_text("utf-8").replace("topic: lsq", "topic: nope", 1), "utf-8")
        out = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command("import_course", self.packed(), stdout=out)
        self.assertIn("not valid", str(raised.exception))
        self.assertRegex(out.getvalue(), r"content/decks/least-squares\.yaml:\d+\s+topic")
        self.assertIn("topic: lsq", self.read("content/decks/least-squares.yaml"))

    def test_a_zip_that_is_not_plainly_one_course_is_refused(self):
        yaml_text = self.read("course.yaml")
        cases = {
            "climbs out of its folder": {
                "example/course.yaml": yaml_text,
                "example/../../evil.py": "x",
            },
            "an absolute path": {"example/course.yaml": yaml_text, "/etc/passwd": "x"},
            "raw material": {"example/course.yaml": yaml_text, "example/source/L1.pdf": "x"},
            "a program": {"example/course.yaml": yaml_text, "example/content/run.py": "x"},
            "two courses": {"example/course.yaml": yaml_text, "other/course.yaml": yaml_text},
            "no course.yaml": {"example/content/decks/a.yaml": "title: A"},
            "a file beside the folder": {"course.yaml": yaml_text},
            "an image with a script": {
                "example/course.yaml": yaml_text,
                "example/content/lectures/assets/x.svg": "<svg><script>alert(1)</script></svg>",
            },
        }
        for what, files in cases.items():
            with self.subTest(what):
                message = self.refused(self.raw_zip(files))
                self.assertIn("Refused", message)
                self.assertIn("Nothing was changed", message)
        self.assertFalse((self.tmp / "evil.py").exists())
        self.assertFalse((self.course / "content/run.py").exists())

    def test_it_changes_a_course_and_never_makes_one(self):
        shutil.move(self.theirs, self.theirs.parent / "brand-new")
        self.theirs = self.theirs.parent / "brand-new"
        self.assertIn("no course `brand-new`", self.refused(self.packed()))
        self.assertIn("not a zip file", self.refused(str(self.course / "course.yaml")))
        self.assertIn("not a file", self.refused(str(self.tmp / "missing.zip")))

    def test_uncommitted_work_in_the_course_folder_is_not_mixed_with_a_hand_in(self):
        deck = self.theirs / "content/decks/least-squares.yaml"
        deck.write_text(deck.read_text("utf-8") + "\n", "utf-8")
        dirty = "ingest.management.commands.import_course.uncommitted"
        with mock.patch(dirty, return_value=[" M content/decks/least-squares.yaml"]):
            self.assertIn("uncommitted changes", self.refused(self.packed()))
            self.assertIn("taken in", run("import_course", self.packed(), "--force"))


class PublishCommandTests(CourseTestCase):
    """`publish` validates, packs and hands the archive to `send`, which runs ssh. Here
    `send` is replaced by a stand-in, so the tests see exactly what would have gone out
    without contacting anything."""

    SEND = "ingest.management.commands.publish.send"

    def names(self, archive):
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            return tar.getnames()

    def test_sends_course_files_but_never_source_material(self):
        (self.course / "source/lectures").mkdir(parents=True)
        (self.course / "source/lectures/L1.pdf").write_bytes(b"%PDF")
        with mock.patch(self.SEND, return_value=0) as send:
            text = run("publish", "--to", "me@server.test")
        target, archive = send.call_args.args
        self.assertEqual(target, "me@server.test")
        names = self.names(archive)
        self.assertIn("example/course.yaml", names)
        self.assertIn("example/content/decks/least-squares.yaml", names)
        self.assertIn("example/content/lectures/assets/residuals.svg", names)
        self.assertEqual([name for name in names if "source" in name.split("/")], [])
        self.assertIn("Published", text)
        self.assertFalse(Course.objects.exists(), "publishing must not touch the local database")

    def test_invalid_content_is_never_sent(self):
        self.edit("content/decks/least-squares.yaml", "topic: lsq", "topic: nope")
        out = StringIO()
        with mock.patch(self.SEND) as send, self.assertRaises(CommandError) as raised:
            call_command("publish", "--to", "me@server.test", stdout=out)
        send.assert_not_called()
        self.assertIn("Nothing was sent", str(raised.exception))
        self.assertRegex(out.getvalue(), r"content/decks/least-squares\.yaml:\d+\s+topic")

    def test_dry_run_sends_nothing(self):
        with mock.patch(self.SEND) as send:
            text = run("publish", "--dry-run")
        send.assert_not_called()
        self.assertRegex(text, r"Would send 1 course, \d+ files")

    def test_a_course_in_a_semester_folder_is_sent_under_its_own_name(self):
        """The server keeps the courses side by side, however they are grouped here."""
        grouped = settings.COURSES_DIR / "semester-1"
        grouped.mkdir()
        self.course.rename(grouped / "example")
        with mock.patch(self.SEND, return_value=0) as send:
            run("publish", "--to", "me@server.test")
        names = self.names(send.call_args.args[1])
        self.assertIn("example/course.yaml", names)
        self.assertEqual([name for name in names if name.startswith("semester-1")], [])

    def test_two_folders_with_one_name_are_never_sent(self):
        grouped = settings.COURSES_DIR / "semester-1" / "example"
        shutil.copytree(self.course, grouped)
        with mock.patch(self.SEND) as send, self.assertRaises(CommandError) as raised:
            run("publish", "--to", "me@server.test")
        send.assert_not_called()
        self.assertIn("same name", str(raised.exception))

    def test_needs_to_know_the_server(self):
        with (
            mock.patch.dict(os.environ, {"DSAI_PUBLISH_SSH": ""}),
            mock.patch(self.SEND) as send,
            self.assertRaises(CommandError) as raised,
        ):
            run("publish")
        send.assert_not_called()
        self.assertIn("DSAI_PUBLISH_SSH", str(raised.exception))

    def test_a_refusal_by_the_server_is_an_error(self):
        with mock.patch(self.SEND, return_value=1), self.assertRaises(CommandError):
            run("publish", "--to", "me@server.test")

    def test_send_pipes_the_archive_to_the_deploy_script(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as ssh:
            self.assertEqual(send("me@server.test", b"archive"), 0)
        command = ssh.call_args.args[0]
        self.assertEqual((command[0], command[-2:]), ("ssh", ["me@server.test", REMOTE_COMMAND]))
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(ssh.call_args.kwargs["input"], b"archive")


class SchemaExportTests(SimpleTestCase):
    def test_committed_schemas_match_the_models(self):
        """docs/schemas/ is generated. If this fails, run: manage.py export_schemas"""
        for name, model in SCHEMAS.items():
            path = TARGET / f"{name}.schema.json"
            self.assertTrue(path.is_file(), f"{path} is missing")
            self.assertEqual(path.read_text(encoding="utf-8"), render(model), name)

    def test_committed_field_reference_matches_the_models(self):
        self.assertEqual(FIELDS.read_text(encoding="utf-8"), render_markdown())
