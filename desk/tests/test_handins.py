"""Hand-ins: an educator uploads their course and the site takes it in (desk/handins.py,
ingest/handin.py). The example course is the course being written."""

import io
import shutil
import zipfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail as outbox
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Card, Course, Document
from desk import handins
from desk import mail as desk_mail
from desk.models import Educator, HandIn, Notification
from ingest import handin, live
from ingest.tests.base import CourseTestCase

User = get_user_model()
C = "example"
PAGE = f"/educators/courses/{C}/"  # the course's own page, for the people who write it
UPLOAD = f"/educators/courses/{C}/hand-in/"
FILES = f"/educators/courses/{C}/files.zip"
DECK = "content/decks/least-squares.yaml"
SHEET = "content/reference/cheatsheet.md"
NEW_GUIDE = "---\ntitle: A second look\ntopics: [lsq]\n---\n\nSomething more.\n"
NEW_CARD = "  - id: lsq-extra\n    q: One more?\n    a: Yes.\n"
TITLE = "Example 101: Fitting a Line"


class CompareTests(SimpleTestCase):
    """What one version does to another: counted and listed, never forbidden here."""

    def change(self, before, after):
        return handin.compare({"a.yaml": before.encode()}, {"a.yaml": after.encode()})

    def test_lines_added_anywhere_are_only_additions(self):
        change = self.change("a\nb\nc\n", "zero\na\nb\nmiddle\nc\nend\n")
        self.assertTrue(change.only_adds)
        self.assertEqual((change.grown, change.shrunk), ({"a.yaml": 3}, {}))
        self.assertEqual(str(change), "3 lines added")

    def test_a_removed_or_rewritten_line_is_counted_and_named(self):
        change = self.change("first\nsecond\nthird\n", "first\nSECOND\nthird\nfourth\n")
        self.assertFalse(change.only_adds)
        self.assertEqual((change.shrunk, change.grown), ({"a.yaml": 1}, {"a.yaml": 2}))
        self.assertIn("a.yaml: 1 line removed or rewritten", change.lost)
        self.assertIn("    line 2: second", change.lost)
        self.assertEqual(str(change), "2 lines added, 1 line removed or rewritten")

    def test_reordering_takes_something_away_too(self):
        self.assertFalse(self.change("a\nb\n", "b\na\n").only_adds)

    def test_repeated_lines_are_counted_not_merely_found(self):
        self.assertTrue(self.change("- x\n- x\n", "- x\n- y\n- x\n").only_adds)
        self.assertFalse(self.change("- x\n- x\n", "- x\n- y\n").only_adds)

    def test_line_endings_and_trailing_spaces_do_not_count(self):
        change = self.change("a\nb\n", "a  \r\nb\r\n\r\n")
        self.assertTrue(change.only_adds)
        self.assertEqual(change.grown, {})

    def test_files_that_come_and_go(self):
        old = {"a.yaml": b"x\n", "pic.png": b"\x89PNG1"}
        change = handin.compare(old, {"pic.png": b"\x89PNG2", "b.md": b"new\n"})
        self.assertEqual(
            (change.added, change.removed, change.swapped), (["b.md"], ["a.yaml"], ["pic.png"])
        )
        self.assertIn("a.yaml: the whole file", change.lost)
        self.assertEqual(str(change), "1 new file, 1 file removed")
        self.assertTrue(handin.compare(old, old).empty)

    def test_ids_that_disappear_are_named_and_one_that_moves_is_not(self):
        old = {"d.yaml": b"cards:\n  - id: one\n    q: Q\n  - id: two\n    q: Q\n"}
        gone = handin.compare(old, {"d.yaml": b"cards:\n  - id: one\n    q: Q\n"})
        self.assertEqual(gone.ids, ["two"])
        self.assertIn("1 id disappear", gone.lost[-1])
        moved = {
            "d.yaml": b"cards:\n  - id: one\n    q: Q\n",
            "e.yaml": b"cards:\n  - id: two\n    q: Q\n",
        }
        self.assertEqual(handin.compare(old, moved).ids, [])

    def test_a_version_is_a_fingerprint_of_the_files(self):
        files = {"course.yaml": b"title: T\n", "content/a.md": b"a\n"}
        same = dict(reversed(files.items()))
        self.assertEqual(handin.version_of(files), handin.version_of(same))
        self.assertNotEqual(
            handin.version_of(files), handin.version_of({**files, "content/a.md": b"b\n"})
        )
        # A note the site adds to a zip travels beside the course, not in it.
        packed = handin.pack_files("x", files, {"base": "abc"})
        slug, back, note = handin.unzip_with_note(packed)
        self.assertEqual((slug, back, note), ("x", files, {"base": "abc"}))
        self.assertEqual(handin.unzip(packed)[1], files)

    def test_holds_asks_whether_everything_of_one_is_in_the_other(self):
        snapshot = {"course.yaml": b"topics:\n  - id: a\n", "d.yaml": b"x\ny\n"}
        more = {"course.yaml": b"title: T\ntopics:\n  - id: a\n  - id: b\n", "d.yaml": b"x\ny\nz\n"}
        self.assertTrue(handin.holds(more, snapshot))
        self.assertFalse(handin.holds(snapshot, more))


class HandInCase(CourseTestCase):
    def setUp(self):
        super().setUp()
        caches["limits"].clear()
        self.assertEqual(self.sync().status, "synced")
        self.ada = User.objects.create_user("ada", email="ada@example.test", first_name="Ada")
        self.bob = User.objects.create_user("bob", email="bob@example.test", first_name="Bob")
        self.admin = User.objects.create_superuser("root", email="root@example.test")
        for person in (self.ada, self.bob):
            Educator.objects.create(user=person, series=C)
        self.client.force_login(self.ada)
        self.bobs_browser = self.client_class()
        self.bobs_browser.force_login(self.bob)
        self.mine = self.download("ada")

    def download(self, name, client=None):
        """Download the course as the site shows it and unpack it somewhere of one's own.
        The site notes which version that person now has."""
        answer = (client or self.client).post(FILES)
        self.assertEqual(answer.status_code, 200)
        folder = self.tmp / name / C
        shutil.rmtree(folder.parent, ignore_errors=True)
        with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
            archive.extractall(folder.parent)
        return folder

    def add(self, folder, name, text):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text("utf-8") if path.exists() else ""
        path.write_text(before + text, "utf-8", newline="\n")

    def drop_line(self, folder, name, starting):
        """Remove the first line of a file that starts with `starting`; returns it."""
        path = folder / name
        lines = path.read_text("utf-8").split("\n")
        gone = next(line for line in lines if line.strip().startswith(starting))
        lines.remove(gone)
        path.write_text("\n".join(lines), "utf-8", newline="\n")
        return gone.strip()

    def hand_in(self, folder, client=None, confirm=False):
        upload = SimpleUploadedFile("example.zip", handin.pack(folder), "application/zip")
        data = {"zip": upload, **({"removals": "yes"} if confirm else {})}
        return (client or self.client).post(UPLOAD, data)

    def written(self):
        """Ada adds a card to a deck and writes a new guide."""
        self.add(self.mine, DECK, NEW_CARD)
        self.add(self.mine, "content/guides/a-second-look.md", NEW_GUIDE)

    def refused(self, answer, heading):
        self.assertEqual(answer.status_code, 200)
        self.assertContains(answer, heading)
        self.assertFalse(HandIn.objects.exists())
        self.assertFalse((handins.root() / C).exists())


class TakingInTests(HandInCase):
    def test_additions_are_on_the_site_at_once(self):
        cards = Card.objects.count()
        before = handin.content_version(self.course)
        self.written()
        with mock.patch("desk.views.notify") as notify, self.captureOnCommitCallbacks(execute=True):
            answer = self.hand_in(self.mine)
        record = HandIn.objects.get()
        self.assertRedirects(
            answer, f"{PAGE}?handed={record.pk}#hand-in", fetch_redirect_response=False
        )
        self.assertEqual((record.user, record.status, record.slug), (self.ada, "live", C))
        self.assertEqual(record.new_files, ["content/guides/a-second-look.md"])
        self.assertEqual((record.grown, record.shrunk), ({DECK: 3}, {}))
        self.assertEqual(record.summary, "1 new file, 3 lines added")
        self.assertEqual((record.base_version, record.removes), (before, False))
        # Live: the database holds it, read from a folder of the site's own.
        self.assertEqual(Card.objects.count(), cards + 1)
        self.assertTrue(Document.objects.filter(slug="a-second-look").exists())
        self.assertEqual(live.folder(C), handins.root() / C)
        self.assertNotIn("lsq-extra", self.read(DECK))  # the owner's files are not touched
        # The owner is told that something arrived, not by whom.
        told = " ".join([*notify.call_args.args, *map(str, notify.call_args.kwargs.values())])
        self.assertIn("Hand-in on the site", told)
        self.assertNotIn("Ada", told)
        self.assertEqual(notify.call_args.kwargs["level"], "success")

        page = self.client.get(f"{PAGE}?handed={record.pk}")
        self.assertContains(page, "Taken in: 1 new file, 3 lines added")
        self.assertContains(page, "The course, step by step")
        self.assertContains(page, "content/guides/a-second-look.md")
        self.assertContains(page, "· you")
        self.assertContains(page, 'git commit -m "example: handed in"')
        # A fellow educator of the course sees the same step, with the name.
        self.assertContains(self.bobs_browser.get(PAGE), "· Ada")

    def test_what_it_removes_is_shown_first_and_taken_in_once_confirmed(self):
        self.written()
        gone = self.drop_line(self.mine, SHEET, "- Unique when")
        answer = self.hand_in(self.mine)
        self.refused(answer, "this hand-in removes or rewrites what is on the site")
        self.assertContains(answer, f"{SHEET}: 1 line removed or rewritten")
        self.assertContains(answer, "It may change and remove what is there")
        self.assertFalse(Card.objects.filter(key="lsq-extra").exists())

        with mock.patch("desk.views.notify") as notify, self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.hand_in(self.mine, confirm=True).status_code, 302)
        record = HandIn.objects.get()
        self.assertEqual(
            (record.shrunk, record.lines_removed, record.removes), ({SHEET: 1}, 1, True)
        )
        self.assertIn("1 line removed or rewritten", record.summary)
        self.assertNotIn(gone, (live.folder(C) / SHEET).read_text("utf-8").split("\n"))
        self.assertEqual(notify.call_args.kwargs["level"], "warning")  # worth a look sooner
        self.assertContains(self.client.get(PAGE), "1 line removed or rewritten")

    def test_a_file_and_the_ids_in_it_can_go_but_are_named_first(self):
        deck = handin.files_of(self.mine)[DECK].decode()
        ids = sorted(handin._ids({DECK: deck.encode()}))
        (self.mine / DECK).unlink()
        # Other files link to those cards, so they would have to change as well; here
        # the point is what the page says before anything is taken in.
        answer = self.hand_in(self.mine)
        self.refused(answer, "removes or rewrites what is on the site")
        self.assertContains(answer, f"{DECK}: the whole file")
        self.assertContains(answer, f"{len(ids)} ids disappear")
        self.assertContains(answer, ids[0])

    def test_a_guide_can_be_rewritten_and_another_removed(self):
        self.add(self.mine, "content/guides/a-second-look.md", NEW_GUIDE)
        self.assertEqual(self.hand_in(self.mine).status_code, 302)
        guide = self.mine / "content/guides/a-second-look.md"
        guide.write_text(NEW_GUIDE.replace("Something more.", "Something better."), "utf-8")
        self.assertEqual(self.hand_in(self.mine, confirm=True).status_code, 302)
        self.assertIn("Something better.", Document.objects.get(slug="a-second-look").body_html)
        guide.unlink()
        self.assertEqual(self.hand_in(self.mine, confirm=True).status_code, 302)
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())
        newest = HandIn.objects.first()
        self.assertEqual(newest.removed_files, ["content/guides/a-second-look.md"])
        self.assertEqual(HandIn.objects.count(), 3)

    def test_what_is_the_owners_in_course_yaml_cannot_be_changed_even_if_confirmed(self):
        course = self.mine / "course.yaml"
        course.write_text(course.read_text("utf-8").replace("code: EX101", "code: MINE"), "utf-8")
        answer = self.hand_in(self.mine, confirm=True)
        self.refused(answer, "it changes what is not yours in course.yaml")
        self.assertContains(answer, "`code` was &#x27;EX101&#x27; and is now &#x27;MINE&#x27;")

    def test_content_the_site_would_refuse_is_shown_with_its_report(self):
        self.add(self.mine, DECK, "  - id: lsq-bad\n    q: Q?\n    a: A.\n    topic: nope\n")
        answer = self.hand_in(self.mine)
        self.refused(answer, "check found errors")
        self.assertContains(answer, "no topic named `nope` in this course")
        self.assertContains(answer, f"{DECK}:")
        self.assertFalse(Card.objects.filter(key="lsq-bad").exists())

    def test_only_a_zip_of_this_course_from_its_educator(self):
        self.written()
        self.refused(
            self.client.post(UPLOAD, {"zip": SimpleUploadedFile("x.zip", b"not a zip")}),
            "not the zip",
        )
        self.refused(self.client.post(UPLOAD, {}), "No file arrived")
        other = self.tmp / "other" / "other-course"
        shutil.copytree(self.mine, other)
        self.refused(self.hand_in(other), "This zip is the course `other-course`")
        with mock.patch("desk.handins.MAX_UPLOAD", 10):
            self.refused(self.hand_in(self.mine), "The file is larger than")
        self.refused(self.hand_in(self.download("same")), "exactly what the site already has")
        # Someone who does not write this course has no such address at all.
        carol = self.client_class()
        carol.force_login(User.objects.create_user("carol", email="carol@example.test"))
        self.assertEqual(self.hand_in(self.mine, carol).status_code, 404)
        self.assertEqual(carol.post(FILES).status_code, 404)
        self.assertFalse(HandIn.objects.exists())

    def test_the_site_must_know_where_someone_started(self):
        Educator.objects.filter(user=self.ada).update(synced_version="")
        self.written()
        answer = self.hand_in(self.mine)
        self.refused(answer, "the site does not know which version you started from")
        self.assertContains(answer, "Download the course files")

    def test_one_at_a_time_per_course(self):
        self.written()
        handins.root().mkdir(parents=True)
        (handins.root() / f".lock-{C}").write_text("", "utf-8")
        self.refused(self.hand_in(self.mine), "Someone is handing in this course right now")


class MovedOnTests(HandInCase):
    """Two educators of one course. The second to hand in built on what is no longer the
    latest, and is told how to catch up."""

    def setUp(self):
        super().setUp()
        self.bobs = self.download("bob", self.bobs_browser)
        self.written()
        self.assertEqual(self.hand_in(self.mine).status_code, 302)

    def test_work_built_on_an_older_version_is_refused_as_a_conflict(self):
        self.add(self.bobs, SHEET, "\nOne more line from Bob.\n")
        for confirm in (False, True):  # ticking the box does not make it his to undo
            answer = self.hand_in(self.bobs, self.bobs_browser, confirm=confirm)
            self.assertContains(answer, "the course changed on the site after you took your copy")
            self.assertContains(answer, "git stash")
            self.assertContains(answer, "uv run tools/unpack.py example.zip")
            self.assertContains(answer, "git stash pop")
        self.assertEqual(HandIn.objects.count(), 1)
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())

    def test_he_was_told_when_it_changed(self):
        (note,) = Notification.objects.filter(user=self.bob)
        self.assertIn("changed on the site", note.title)
        self.assertIn("Download the course files again", note.body)
        self.assertEqual(note.url, f"{PAGE}#files")
        self.assertFalse(Notification.objects.filter(user=self.ada).exists())  # she did it
        self.assertIn("Hand-in on the site", Notification.objects.get(user=self.admin).title)

    def test_on_top_of_the_latest_it_goes_in_and_nothing_of_the_first_is_lost(self):
        self.bobs = self.download("bob", self.bobs_browser)  # Ada's work is in it now
        self.add(self.bobs, SHEET, "\nOne more line from Bob.\n")
        self.assertEqual(self.hand_in(self.bobs, self.bobs_browser).status_code, 302)
        self.assertEqual(HandIn.objects.count(), 2)
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())
        self.assertIn("One more line from Bob.", (live.folder(C) / SHEET).read_text("utf-8"))
        # And now it is Ada whose copy is behind.
        self.add(self.mine, SHEET, "\nA line from Ada.\n")
        self.assertContains(self.hand_in(self.mine), "the course changed on the site")

    def test_the_download_is_what_the_site_shows_and_is_a_post(self):
        self.assertEqual(self.client.get(FILES).status_code, 405)
        answer = self.bobs_browser.post(FILES)
        with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
            self.assertIn(f"{C}/content/guides/a-second-look.md", archive.namelist())
            self.assertIn("lsq-extra", archive.read(f"{C}/{DECK}").decode())
            self.assertEqual([n for n in archive.namelist() if ".handin" in n], [])
        role = Educator.objects.get(user=self.bob, series=C)
        self.assertEqual(role.synced_version, HandIn.objects.get().version)


class HistoryTests(HandInCase):
    """Every hand-in keeps the whole course as it stood. That is the backup."""

    def setUp(self):
        super().setUp()
        self.written()
        self.hand_in(self.mine)
        self.add(self.mine, SHEET, "\nA second step.\n")
        self.hand_in(self.mine)
        self.second, self.first = HandIn.objects.all()  # newest first

    def zip_of(self, record, client=None):
        answer = (client or self.client).get(reverse("desk:handin_zip", args=[record.pk]))
        self.assertEqual(answer.status_code, 200)
        return answer.content

    def test_each_step_can_be_downloaded_by_those_who_write_the_course(self):
        _slug, files = handin.unzip(self.zip_of(self.first))
        self.assertIn("lsq-extra", files[DECK].decode())
        self.assertNotIn("A second step.", files[SHEET].decode())
        self.assertEqual(handin.version_of(files), self.first.version)
        self.assertContains(self.client.get(PAGE), "Download the course as it was after this step")
        self.zip_of(self.second, self.bobs_browser)
        carol = self.client_class()
        carol.force_login(User.objects.create_user("carol"))
        self.assertEqual(
            carol.get(reverse("desk:handin_zip", args=[self.first.pk])).status_code, 404
        )

    def test_an_earlier_version_is_brought_back_by_handing_it_in(self):
        older = self.tmp / "older"
        with zipfile.ZipFile(io.BytesIO(self.zip_of(self.first))) as archive:
            archive.extractall(older)
        answer = self.hand_in(older / C)
        self.assertContains(
            answer, "removes or rewrites what is on the site"
        )  # it takes a line away
        self.assertEqual(self.hand_in(older / C, confirm=True).status_code, 302)
        newest = HandIn.objects.first()
        self.assertEqual((newest.version, HandIn.objects.count()), (self.first.version, 3))
        self.assertNotIn("A second step.", (live.folder(C) / SHEET).read_text("utf-8"))

    def test_the_newest_step_can_be_taken_back_by_whoever_handed_it_in(self):
        back = lambda record: reverse("desk:take_back", args=[record.pk])  # noqa: E731
        self.assertEqual(self.client.post(back(self.first)).status_code, 404)  # not the newest
        self.assertEqual(self.bobs_browser.post(back(self.second)).status_code, 404)  # not his
        self.assertEqual(self.client.get(back(self.second)).status_code, 405)
        page = self.client.get(PAGE)
        self.assertContains(page, back(self.second))
        self.assertNotContains(page, back(self.first))
        Notification.objects.all().delete()

        self.assertRedirects(
            self.client.post(back(self.second)),
            f"{PAGE}?taken_back=1#history",
            fetch_redirect_response=False,
        )
        self.second.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual((self.second.status, self.first.status), ("withdrawn", "live"))
        self.assertNotIn("A second step.", (live.folder(C) / SHEET).read_text("utf-8"))
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())  # the first step stays
        self.assertContains(self.client.get(f"{PAGE}?taken_back=1"), "Taken back.")
        self.assertIn("taken back", Notification.objects.get(user=self.bob).title)
        self.assertIn("taken back by its educator", Notification.objects.get(user=self.admin).title)
        # Her copy is now ahead of the site, so she starts from a download again.
        self.assertContains(self.hand_in(self.mine), "the course changed on the site")


class OwnerTests(HandInCase):
    """What the owner sees of it: the sync report, the admin, their own course files."""

    def setUp(self):
        super().setUp()
        self.written()
        self.gone = self.drop_line(self.mine, SHEET, "- Unique when")
        self.assertEqual(self.hand_in(self.mine, confirm=True).status_code, 302)
        self.record = HandIn.objects.get()
        self.LIST = reverse("admin:desk_handin_changelist")

    def run_sync(self, *args):
        out = io.StringIO()
        call_command("sync_content", *args, stdout=out)
        return out.getvalue()

    def saved_zip(self):
        self.client.force_login(self.admin)
        answer = self.client.get(reverse("desk:handin_zip", args=[self.record.pk]))
        self.assertEqual(answer.status_code, 200)
        saved = self.tmp / "example-version.zip"
        saved.write_bytes(answer.content)
        return str(saved)

    def take_in(self, *args):
        out = io.StringIO()
        call_command("import_course", self.saved_zip(), *args, stdout=out)
        return out.getvalue()

    def test_a_publish_without_the_hand_in_does_not_lose_it(self):
        for args in ((), ("--check",)):
            with self.subTest(args=args):
                text = self.run_sync(*args)
                self.assertIn("shown as handed in by its educators", text)
                self.assertIn("lack 1 hand-in", text)
        self.assertEqual(HandIn.objects.get().status, "live")
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())

    def test_untouched_files_simply_become_the_educators_version(self):
        self.assertContains(self.client_class().get(self.LIST), "", status_code=302)
        text = self.take_in()
        self.assertIn(
            "example: taken in. 1 new file, 3 lines added, 1 line removed or rewritten", text
        )
        self.assertIn(f"gone: {SHEET}: 1 line removed or rewritten", text)
        self.assertEqual(handin.content_version(self.course), self.record.version)
        self.assertEqual((self.course / ".merged").read_text("utf-8").strip(), self.record.version)

        text = self.run_sync()  # what a publish does on the server
        self.assertNotIn("shown as handed in", text)
        record = HandIn.objects.get()
        self.assertEqual(record.status, "merged")
        self.assertIsNotNone(record.settled_at)
        self.assertFalse((handins.root() / C).exists())
        self.assertEqual(live.folder(C), self.course)
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())
        self.client.force_login(self.ada)
        self.assertContains(self.client.get(PAGE), "In the published files")

    def test_the_owners_own_changes_are_kept_and_the_hand_in_still_counts_as_in(self):
        self.append("content/reference/formulas.yaml", "\n")  # nothing that matters
        self.append("content/guides/choosing-a-method.md", "\nThe owner's own line.\n")
        text = self.take_in("--mine")
        self.assertIn("lsq-extra", self.read(DECK))
        self.assertIn("The owner's own line.", self.read("content/guides/choosing-a-method.md"))
        self.assertIn("left as they are", text)
        # The site lets go of its copy because the files say which hand-in they include,
        # though they are no longer the same files.
        self.run_sync()
        self.assertEqual(HandIn.objects.get().status, "merged")
        self.assertEqual(live.folder(C), self.course)

    def test_where_both_changed_a_file_the_owner_decides(self):
        self.write(SHEET, self.read(SHEET) + "\nThe owner added this.\n")
        with self.assertRaises(CommandError) as raised:
            self.take_in()
        self.assertIn("You changed this course yourself", str(raised.exception))
        self.assertIn(SHEET, str(raised.exception))
        self.assertNotIn("lsq-extra", self.read(DECK))
        self.assertFalse((self.course / "content/guides/a-second-look.md").exists())
        self.take_in("--theirs")
        self.assertNotIn("The owner added this.", self.read(SHEET))
        self.assertEqual(handin.content_version(self.course), self.record.version)

    def test_taking_it_off_the_site_puts_the_course_back(self):
        self.client.force_login(self.admin)
        Notification.objects.all().delete()
        chosen = {"action": "take_off", "_selected_action": [self.record.pk]}
        # Mail is sent from a thread; here it is delivered on the spot, into Django's
        # test outbox, so that the test can look at it.
        with mock.patch("desk.mail._dispatch", side_effect=desk_mail.deliver):
            self.assertRedirects(self.client.post(self.LIST, chosen), self.LIST)
        self.assertEqual(HandIn.objects.get().status, "withdrawn")
        self.assertFalse(Card.objects.filter(key="lsq-extra").exists())
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())
        self.assertIn(self.gone, (live.folder(C) / SHEET).read_text("utf-8"))
        self.assertFalse((handins.root() / C).exists())
        self.assertEqual([message.to for message in outbox.outbox], [["ada@example.test"]])
        told = {note.user: note.title for note in Notification.objects.all()}
        self.assertEqual(set(told), {self.ada, self.bob})
        self.assertIn("taken back", told[self.ada])

    def test_the_sites_copy_is_rebuilt_from_the_database(self):
        shutil.rmtree(handins.root())
        self.run_sync()
        self.assertTrue((handins.root() / C / DECK).is_file())
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())

    def test_none_of_the_owners_side_is_open_to_others(self):
        self.assertEqual(self.client.get(self.LIST).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.LIST), "1 line removed or rewritten")
        self.assertEqual(self.client.get(reverse("admin:desk_handin_add")).status_code, 403)
        delete = reverse("admin:desk_handin_delete", args=[self.record.pk])
        self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 403)

    def test_leaving_the_site_keeps_what_was_done_without_the_name(self):
        self.ada.delete()
        self.assertIsNone(HandIn.objects.get().user)
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())
        self.assertContains(self.bobs_browser.get(PAGE), "a former educator")


class EditedOnTheSiteTests(HandInCase):
    """An edit made on the site (handins.change) is a hand-in without the zip: the same
    checks, the same history, and saves in one sitting are one step."""

    def setUp(self):
        super().setUp()
        self.subject = Course.objects.get(slug=C)

    def change(self, user, name, text, *, replace=False):
        """Add `text` to a file of the course on the site (or make it the whole file)."""

        def edit(files):
            before = b"" if replace else files.get(name, b"")
            files[name] = before + text.encode()
            return files

        return handins.change(user, self.subject, edit)

    def refused(self, kind, user, name, text, **options):
        with self.assertRaises(handins.Refused) as caught:
            self.change(user, name, text, **options)
        self.assertEqual(caught.exception.kind, kind, msg=caught.exception.lines)
        return caught.exception.lines

    def test_an_edit_is_live_at_once_and_is_a_step_in_the_history(self):
        cards, before = Card.objects.count(), handin.content_version(self.course)
        step, fresh = self.change(self.ada, DECK, NEW_CARD)
        self.assertTrue(fresh)
        self.assertEqual(
            (step.via, step.user, step.status, step.saves), ("site", self.ada, "live", 1)
        )
        self.assertEqual((step.base_version, step.grown), (before, {DECK: 3}))
        self.assertEqual(Card.objects.count(), cards + 1)
        self.assertEqual(live.folder(C), handins.root() / C)
        self.assertNotIn("lsq-extra", self.read(DECK))  # the owner's files are not touched
        # The archive is the whole course after it, as for any hand-in.
        _slug, kept = handin.unzip(bytes(step.archive))
        self.assertIn(b"lsq-extra", kept[DECK])
        self.assertEqual(handin.version_of(kept), step.version)
        # The other educator and the owner hear of it; never who, never what.
        note = Notification.objects.get(user=self.bob)
        self.assertEqual((note.title, note.url), (f"{TITLE} changed on the site", f"{PAGE}#files"))
        self.assertNotIn("Ada", note.title + note.body)
        self.assertIn("Edited on the site", Notification.objects.get(user=self.admin).title)
        self.assertFalse(Notification.objects.filter(user=self.ada).exists())

    def test_saves_in_one_sitting_are_one_step(self):
        first, _fresh = self.change(self.ada, DECK, NEW_CARD)
        again, fresh = self.change(self.ada, "content/guides/a-second-look.md", NEW_GUIDE)
        self.assertFalse(fresh)
        self.assertEqual(again.pk, first.pk)
        step = HandIn.objects.get()
        self.assertEqual((step.saves, step.base_version), (2, first.base_version))
        self.assertIsNotNone(step.updated_at)
        # What the step says it did is everything since it began.
        self.assertEqual(step.summary, "1 new file, 3 lines added")
        self.assertEqual(step.new_files, ["content/guides/a-second-look.md"])
        self.assertTrue(Document.objects.filter(slug="a-second-look").exists())
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())
        self.assertEqual(handin.version_of(handin.unzip(bytes(step.archive))[1]), step.version)
        self.assertEqual(Notification.objects.filter(user=self.bob).count(), 1)  # told once

    def test_a_pause_begins_a_new_step(self):
        first, _fresh = self.change(self.ada, DECK, NEW_CARD)
        long_ago = timezone.now() - handins.SESSION - handins.SESSION
        HandIn.objects.filter(pk=first.pk).update(created_at=long_ago)
        second, fresh = self.change(self.ada, SHEET, "\nOne more line.\n")
        self.assertTrue(fresh)
        self.assertEqual(second.base_version, first.version)
        self.assertEqual(HandIn.objects.count(), 2)

    def test_someone_else_in_between_begins_a_new_step(self):
        self.change(self.ada, DECK, NEW_CARD)
        self.change(self.bob, SHEET, "\nOne more line from Bob.\n")
        _step, fresh = self.change(self.ada, SHEET, "\nAnd one from Ada.\n")
        self.assertTrue(fresh)
        self.assertEqual(HandIn.objects.count(), 3)
        sheet = (live.folder(C) / SHEET).read_text("utf-8")
        self.assertIn("One more line from Bob.", sheet)
        self.assertIn("And one from Ada.", sheet)

    def test_what_the_site_would_not_accept_changes_nothing(self):
        lines = self.refused("invalid", self.ada, SHEET, "\nA <b>bold</b> claim.\n")
        self.assertIn("raw HTML", " ".join(lines))
        self.assertFalse(HandIn.objects.exists())
        self.assertFalse((handins.root() / C).exists())
        self.assertEqual(self.refused("nothing", self.ada, SHEET, ""), ["Nothing was changed."])

    def test_only_the_files_of_a_course_can_be_written(self):
        for name in (
            "source/slides.md",
            "content/../../settings.py",
            "content/x.exe",
            "content/.hidden.md",
        ):
            with self.subTest(name=name):
                self.refused("bad", self.ada, name, "x")
        self.refused("bad", self.ada, "content/figure.svg", "<svg><script>1</script></svg>")
        self.assertFalse(HandIn.objects.exists())

    def test_what_is_the_owners_stays_the_owners_but_staff_act_for_the_owner(self):
        yaml = self.read("course.yaml").replace(f'title: "{TITLE}"', "title: Renamed")
        lines = self.refused("owner", self.ada, "course.yaml", yaml, replace=True)
        self.assertIn("title", " ".join(lines))
        self.assertEqual(Course.objects.get(slug=C).title, TITLE)
        self.change(self.admin, "course.yaml", yaml, replace=True)
        self.assertEqual(Course.objects.get(slug=C).title, "Renamed")
        self.refused("bad", self.client_user("eve"), SHEET, "\nNot hers to write.\n")

    def client_user(self, name):
        return User.objects.create_user(name, email=f"{name}@example.test")

    def test_a_copy_made_with_the_kit_is_behind_after_an_edit_on_the_site(self):
        self.change(self.bob, SHEET, "\nOne more line from Bob.\n")
        self.written()
        self.assertContains(self.hand_in(self.mine), "the course changed on the site")
        self.assertContains(self.client.get(PAGE), "Not what the site has")
        # With the course fetched again her work goes in, on top of his, as a step of its own.
        self.mine = self.download("ada")
        self.written()
        self.assertEqual(self.hand_in(self.mine).status_code, 302)
        self.assertEqual([row.via for row in HandIn.objects.order_by("pk")], ["site", "kit"])
        self.assertIn("One more line from Bob.", (live.folder(C) / SHEET).read_text("utf-8"))

    def test_an_edit_after_a_zip_is_a_step_of_its_own(self):
        self.written()
        self.assertEqual(self.hand_in(self.mine).status_code, 302)
        _step, fresh = self.change(self.ada, SHEET, "\nA line typed on the site.\n")
        self.assertTrue(fresh)
        self.assertEqual(HandIn.objects.count(), 2)

    def test_an_edit_can_be_taken_back(self):
        cards = Card.objects.count()
        step, _fresh = self.change(self.ada, DECK, NEW_CARD)
        self.change(self.ada, SHEET, "\nOne more line.\n")
        handins.withdraw(HandIn.objects.get(pk=step.pk), by=self.admin)
        self.assertEqual(Card.objects.count(), cards)
        self.assertEqual(live.folder(C), self.course)
        self.assertEqual(HandIn.objects.get().status, "withdrawn")

    def test_changed_and_changed_back_is_said_so(self):
        sheet = self.read(SHEET)
        self.change(self.ada, SHEET, "\nA slip.\n")
        step, fresh = self.change(self.ada, SHEET, sheet, replace=True)
        self.assertFalse(fresh)
        self.assertEqual(
            (step.version, step.summary), (step.base_version, "changed, and changed back")
        )
        self.assertEqual((step.lines_added, step.lines_removed), (0, 0))

    def test_a_file_removed_in_the_same_sitting_is_still_in_the_step_before(self):
        guide = "content/guides/a-second-look.md"
        written, _fresh = self.change(self.ada, guide, NEW_GUIDE)

        def remove(files):
            del files[guide]
            return files

        removal, fresh = handins.change(self.ada, self.subject, remove)
        self.assertTrue(fresh)  # moments later, and still a step of its own
        self.assertEqual((removal.removed_files, removal.base_version), ([guide], written.version))
        self.assertEqual(handins.files_at(C, written.version)[guide], NEW_GUIDE.encode())
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())
        # What is saved next goes on with the step that removed it.
        _step, fresh = self.change(self.ada, SHEET, "\nOne more line.\n")
        self.assertFalse(fresh)
        self.assertEqual(HandIn.objects.count(), 2)

    def test_a_page_asks_for_one_course_without_opening_a_single_archive(self):
        self.change(self.ada, DECK, NEW_CARD)
        self.change(self.bob, SHEET, "\nOne more line from Bob.\n")
        with self.assertNumQueries(1), mock.patch.object(handin, "unzip") as unzip:
            self.assertEqual(live.folder(C), handins.root() / C)
        unzip.assert_not_called()
        # The folder is only a working copy: gone, it comes back from the newest step.
        shutil.rmtree(handins.root() / C)
        self.assertIn("One more line from Bob.", (live.folder(C) / SHEET).read_text("utf-8"))
        self.assertIn("lsq-extra", (live.folder(C) / DECK).read_text("utf-8"))
