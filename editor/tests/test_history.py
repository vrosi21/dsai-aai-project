"""The history of a course in the editor: what a step changed, bringing a version back,
and the versions of one file. How versions are kept is tested with the hand-ins
(desk/tests/test_handins.py, KeptTests)."""

from django.test import SimpleTestCase

from catalog.models import Course, Document
from desk import blobs, handins
from desk.models import HandIn
from editor import history
from ingest import handin

from .test_editor import DECK, EDIT, GUIDE, LECTURE, SHEET, TITLE, C, EditorCase

HISTORY = f"{EDIT}history/"
NEW = "content/guides/a-second-look.md"


class CompareTests(SimpleTestCase):
    def test_lines_that_went_and_came_with_their_numbers(self):
        before = {"a.md": b"one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\n"}
        after = {"a.md": b"one\ntwo\nthree\nfour\nFIVE\nsix\nseven\neight\nnine\nten\n"}
        (change,) = history.compare(before, after)
        self.assertEqual((change.state, change.added, change.removed), ("changed", 2, 1))
        (hunk,) = change.hunks
        shown = [(line.kind, line.old, line.new, line.text) for line in hunk]
        self.assertEqual(shown[0], ("same", 2, 2, "two"))  # three lines of context
        self.assertIn(("del", 5, None, "five"), shown)
        self.assertIn(("add", None, 5, "FIVE"), shown)
        self.assertEqual(shown[-1], ("add", None, 10, "ten"))

    def test_changes_far_apart_are_shown_apart(self):
        old = [f"line {n}" for n in range(60)]
        new = ["first", *old[1:50], "CHANGED", *old[51:]]
        (change,) = history.compare(
            {"a.md": "\n".join(old).encode()}, {"a.md": "\n".join(new).encode()}
        )
        self.assertEqual(len(change.hunks), 2)
        self.assertEqual(change.hunks[1][3].text, "line 50")

    def test_new_removed_and_not_text(self):
        before = {"gone.md": b"was\n", "same.md": b"x\n", "pic.png": b"\x89PNG\xff\x00one"}
        after = {"new.md": b"is\nhere\n", "same.md": b"x\n", "pic.png": b"\x89PNG\xff\x00two"}
        found = {change.name: change for change in history.compare(before, after)}
        self.assertEqual(sorted(found), ["gone.md", "new.md", "pic.png"])
        self.assertEqual((found["new.md"].state, found["new.md"].added), ("new", 2))
        self.assertEqual((found["gone.md"].state, found["gone.md"].removed), ("removed", 1))
        self.assertFalse(found["pic.png"].is_text)
        self.assertEqual(history.summary(list(found.values())), history.summary(found.values()))
        self.assertEqual(history.compare(after, after), [])

    def test_a_long_comparison_is_cut_and_says_by_how_much(self):
        old = "\n".join(f"old {n}" for n in range(900)).encode()
        new = "\n".join(f"new {n}" for n in range(900)).encode()
        (change,) = history.compare({"a.md": old}, {"a.md": new})
        self.assertEqual((change.added, change.removed), (900, 900))
        self.assertEqual(sum(len(hunk) for hunk in change.hunks), history.FILE_LINES)
        self.assertEqual(change.cut, 1800 - history.FILE_LINES)


class HistoryCase(EditorCase):
    def setUp(self):
        super().setUp()
        self.subject = Course.objects.get(slug=C)
        self.bobs = self.client_class()
        self.bobs.force_login(self.bob)

    def save(self, user, name, text, *, replace=False):
        def edit(files):
            files[name] = (b"" if replace else files.get(name, b"")) + text.encode()
            return files

        return handins.change(user, self.subject, edit)[0]


class StepsTests(HistoryCase):
    def test_the_history_lists_every_step_newest_first(self):
        self.assertContains(self.client.get(HISTORY), "Nothing has been changed on the site")
        first = self.save(self.ada, NEW, "---\ntitle: A second look\n---\n\nText.\n")
        second = self.save(self.bob, SHEET, "\nOne more line from Bob.\n")
        page = self.client.get(HISTORY)
        self.assertEqual([step.pk for step in page.context["steps"]], [second.pk, first.pk])
        self.assertContains(page, f'href="{HISTORY}{first.pk}/"')
        self.assertContains(page, "1 new file")
        self.assertContains(page, "· you ·")
        self.assertContains(page, "· Bob ·")
        self.assertContains(page, "what the site shows", count=1)

    def test_a_step_shows_what_it_changed_line_by_line(self):
        self.save(self.ada, SHEET, "\nOne more line.\n")
        step = self.save(self.ada, NEW, "---\ntitle: A second look\n---\n\nText.\n")
        page = self.client.get(f"{HISTORY}{step.pk}/")
        changes = {change.name: change for change in page.context["changes"]}
        self.assertEqual(sorted(changes), sorted([SHEET, NEW]))
        self.assertEqual((changes[NEW].state, changes[SHEET].added), ("new", 2))
        self.assertContains(page, "One more line.")
        self.assertContains(page, '<td class="diff__sign">+</td>')
        self.assertContains(page, "This is the version the site shows now.")
        self.assertNotContains(page, "Bring this version back")
        # The two saves of the sitting, each with what it changed.
        self.assertEqual(len(page.context["saves"]), 2)
        self.assertContains(page, f"{HISTORY}file/version/?name=content/reference/cheatsheet.md")
        self.assertContains(page, f"/educators/hand-ins/{step.pk}.zip")

    def test_a_zip_that_was_handed_in_is_compared_the_same_way(self):
        files = handin.files_of(self.course)
        files[DECK] += b"  - id: lsq-extra\n    q: One more?\n    a: Yes.\n"
        record = HandIn.objects.create(
            user=self.bob,
            series=C,
            slug=C,
            base_version=handin.content_version(self.course),
            version=handin.version_of(files),
            files=blobs.put(files),
            before=blobs.put(handin.files_of(self.course)),
        )
        page = self.client.get(f"{HISTORY}{record.pk}/")
        (change,) = page.context["changes"]
        self.assertEqual((change.name, change.added, change.removed), (DECK, 3, 0))

    def test_a_step_whose_beginning_is_not_on_record_says_so(self):
        files = handin.files_of(self.course)
        old = HandIn.objects.create(
            user=self.bob,
            series=C,
            slug=C,
            base_version="f" * 64,
            version=handin.version_of(files),
            archive=handin.pack_files(C, files),
            status=HandIn.Status.MERGED,
        )
        page = self.client.get(f"{HISTORY}{old.pk}/")
        self.assertIsNone(page.context["changes"])
        self.assertContains(page, "is not on record")

    def test_a_step_of_another_course_is_not_here(self):
        other = HandIn.objects.create(user=self.bob, series="rl", slug="rl", version="a" * 64)
        self.assertEqual(self.client.get(f"{HISTORY}{other.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"{HISTORY}{other.pk}/restore/").status_code, 404)


class RestoreTests(HistoryCase):
    def setUp(self):
        super().setUp()
        self.first = self.save(self.ada, NEW, "---\ntitle: A second look\n---\n\nText.\n")
        self.second = self.save(self.bob, SHEET, "\nOne more line from Bob.\n")

    def test_bringing_a_version_back_is_a_new_step(self):
        url = f"{HISTORY}{self.first.pk}/restore/"
        page = self.client.get(f"{HISTORY}{self.first.pk}/")
        self.assertContains(page, "Bring this version back")
        # It has to be meant.
        answer = self.client.post(url)
        self.assertRedirects(
            answer, f"{HISTORY}{self.first.pk}/?unsure=1#restore", fetch_redirect_response=False
        )
        self.assertEqual(HandIn.objects.count(), 2)
        answer = self.client.post(url, {"sure": "yes"})
        self.assertRedirects(
            answer, f"{HISTORY}?restored={self.first.pk}", fetch_redirect_response=False
        )
        made = HandIn.objects.order_by("pk").last()
        self.assertEqual(HandIn.objects.count(), 3)
        self.assertEqual(
            (made.version, made.base_version), (self.first.version, self.second.version)
        )
        self.assertEqual(made.shrunk, {SHEET: 2})
        self.assertNotIn("One more line from Bob.", self.on_site(SHEET))
        self.assertTrue(Document.objects.filter(slug="a-second-look").exists())
        # What it replaced is the step before it, and can be brought back in turn.
        self.assertIn(b"One more line from Bob.", handins.kept(self.second)[SHEET])
        self.assertContains(self.client.get(answer["Location"]), "Brought back.")

    def test_the_version_that_is_there_cannot_be_brought_back(self):
        url = f"{HISTORY}{self.second.pk}/restore/"
        answer = self.client.post(url, {"sure": "yes"})
        self.assertRedirects(answer, f"{HISTORY}?restored=same", fetch_redirect_response=False)
        self.assertEqual(HandIn.objects.count(), 2)

    def test_a_version_with_other_settings_of_the_owner_is_the_owners_to_bring_back(self):
        self.client.force_login(self.admin)
        raw = f"{EDIT}file/?name=course.yaml"
        seen = self.client.get(raw).context["form"].initial["seen"]
        renamed = self.on_site("course.yaml").replace(f'title: "{TITLE}"', "title: Renamed")
        self.assertEqual(self.client.post(raw, {"text": renamed, "seen": seen}).status_code, 302)
        self.client.force_login(self.ada)
        answer = self.client.post(f"{HISTORY}{self.first.pk}/restore/", {"sure": "yes"})
        self.assertContains(answer, "only the owner of the site may change")
        self.assertEqual(Course.objects.get(slug=C).title, "Renamed")
        self.client.force_login(self.admin)
        answer = self.client.post(f"{HISTORY}{self.first.pk}/restore/", {"sure": "yes"})
        self.assertEqual(answer.status_code, 302)
        self.assertEqual(Course.objects.get(slug=C).title, TITLE)


class FileVersionsTests(HistoryCase):
    VERSIONS = f"{HISTORY}file/?name="
    VERSION = f"{HISTORY}file/version/?name="

    def test_a_page_that_was_removed_is_found_and_put_back(self):
        text = "---\ntitle: A second look\n---\n\nWorth keeping.\n"
        self.save(self.ada, NEW, text)
        url = f"{EDIT}guides/a-second-look/delete/"
        seen = self.client.get(url).context["page"].seen
        self.client.post(url, {"seen": seen})
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())

        listed = self.client.get(self.VERSIONS + NEW)
        removed, written = listed.context["versions"]
        self.assertEqual((removed.sha, removed.current), (None, True))
        self.assertContains(listed, "This file is not in the course now.")
        page = self.client.get(f"{self.VERSION}{NEW}&sha={written.sha}")
        self.assertContains(page, "Worth keeping.")
        self.assertContains(page, "The file comes back into the course.")
        answer = self.client.post(f"{self.VERSION}{NEW}&sha={written.sha}", {"seen": ""})
        self.assertRedirects(answer, f"{self.VERSIONS}{NEW}&put=1", fetch_redirect_response=False)
        self.assertEqual(self.on_site(NEW), text)
        self.assertTrue(Document.objects.filter(slug="a-second-look").exists())

    def test_a_text_replaced_within_one_sitting_is_still_there(self):
        url = f"{EDIT}guides/choosing-a-method/"
        self.client.post(url, {**self.form_of(url), "summary": "A first thought."})
        self.client.post(url, {**self.form_of(url), "summary": "A second thought."})
        self.assertEqual(HandIn.objects.get().saves, 2)
        listed = self.client.get(self.VERSIONS + GUIDE)
        second, first, published = listed.context["versions"]
        self.assertTrue(second.current)
        self.assertIsNone(published.step)  # as it was before anything was changed here
        page = self.client.get(f"{self.VERSION}{GUIDE}&sha={first.sha}")
        self.assertContains(page, "A first thought.")
        change = page.context["change"]
        self.assertEqual((change.added, change.removed), (1, 1))
        seen = page.context["seen"]
        self.client.post(f"{self.VERSION}{GUIDE}&sha={first.sha}", {"seen": seen})
        self.assertIn("A first thought.", self.on_site(GUIDE))
        # And the published one, from before anyone touched it.
        page = self.client.get(f"{self.VERSION}{GUIDE}&sha={published.sha}")
        self.assertEqual(page.context["text"], self.read(GUIDE))

    def test_put_back_only_against_what_was_shown(self):
        first = self.save(self.ada, SHEET, "\nA first line.\n")
        url = f"{self.VERSION}{SHEET}&sha={first.before[SHEET]}"
        seen = self.client.get(url).context["seen"]
        self.save(self.bob, SHEET, "\nBob, meanwhile.\n")
        answer = self.client.post(url, {"seen": seen})
        self.assertContains(answer, "changed on the site a moment ago")
        self.assertIn("Bob, meanwhile.", self.on_site(SHEET))
        self.assertEqual(self.client.post(url, {"seen": answer.context["seen"]}).status_code, 302)
        self.assertEqual(self.on_site(SHEET), self.read(SHEET))

    def test_what_would_break_the_course_is_not_put_back(self):
        """An old version of a page may link to what is gone by now."""
        self.save(self.ada, NEW, "---\ntitle: A second look\n---\n\nText.\n")
        linking = self.save(self.ada, SHEET, "\nSee [](guide:a-second-look).\n")
        self.client.post(
            f"{self.VERSION}{SHEET}&sha={linking.before[SHEET]}",
            {"seen": linking.files[SHEET]},
        )
        url = f"{EDIT}guides/a-second-look/delete/"
        self.client.post(url, {"seen": self.client.get(url).context["page"].seen})
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())
        steps = HandIn.objects.count()
        current = self.client.get(f"{self.VERSION}{SHEET}&sha={linking.files[SHEET]}")
        answer = self.client.post(
            f"{self.VERSION}{SHEET}&sha={linking.files[SHEET]}", {"seen": current.context["seen"]}
        )
        self.assertContains(answer, "Not saved: the site&#x27;s check found errors.")
        self.assertContains(answer, "no guide named `a-second-look`")
        self.assertEqual(HandIn.objects.count(), steps)

    def test_a_hash_alone_opens_nothing(self):
        step = self.save(self.ada, SHEET, "\nOne more line.\n")
        # The bytes of another file of the course, asked for under this file's name.
        self.assertEqual(
            self.client.get(f"{self.VERSION}{SHEET}&sha={step.files[DECK]}").status_code, 404
        )
        stray = blobs.put({"x": b"of some other course"})["x"]
        for name in (SHEET, DECK, "content/guides/nope.md", "../x", ""):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get(f"{self.VERSION}{name}&sha={stray}").status_code, 404
                )
        self.assertEqual(self.client.get(f"{self.VERSION}{SHEET}&sha=").status_code, 404)
        self.assertEqual(self.client.get(self.VERSIONS + "content/guides/nope.md").status_code, 404)

    def test_a_file_nothing_has_changed_says_so(self):
        page = self.client.get(self.VERSIONS + LECTURE)
        self.assertContains(page, "as it was published")
        self.assertEqual(page.context["versions"], [])

    def test_the_forms_lead_to_the_versions(self):
        page = self.client.get(f"{EDIT}guides/choosing-a-method/")
        self.assertContains(page, f"{HISTORY}file/?name={GUIDE}")
        page = self.client.get(f"{EDIT}file/?name={DECK}")
        self.assertContains(page, f"{HISTORY}file/?name={DECK}")


class OthersTests(HistoryCase):
    def test_to_anyone_else_there_is_no_history(self):
        step = self.save(self.ada, SHEET, "\nOne more line.\n")
        self.client.force_login(self.eve)
        for url in (
            HISTORY,
            f"{HISTORY}{step.pk}/",
            f"{HISTORY}file/?name={SHEET}",
            f"{HISTORY}file/version/?name={SHEET}&sha={step.files[SHEET]}",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        answer = self.client.post(f"{HISTORY}{step.pk}/restore/", {"sure": "yes"})
        self.assertEqual(answer.status_code, 404)
        answer = self.client.post(
            f"{HISTORY}file/version/?name={SHEET}&sha={step.before[SHEET]}", {"seen": ""}
        )
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(HandIn.objects.count(), 1)
