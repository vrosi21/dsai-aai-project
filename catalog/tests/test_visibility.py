"""Public, unlisted and private: what the owner keeps to themselves, and what they hide.

The owner's private material is written in a course's private/ folder; anything else can be
switched by the owner on its page. Everyone else sees what is public in lists, and opens
what is unlisted by its address; what is private does not exist for them."""

import io
import tarfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from catalog import visibility
from catalog.models import Card, Course, Deck, Document, Visibility
from desk.models import Visibility as Switch
from editor import source
from ingest import handin
from ingest.management.commands import export_authoring
from ingest.management.commands.publish import pack
from ingest.tests.base import CourseTestCase

NOTES = """\
---
title: My own notes on least squares
topics: [lsq]
summary: What I worked out for the lab.
---

## My derivation

Kept to myself. It builds on [the lecture](lecture:01-least-squares).
"""

DECK = """\
title: My cards
topic: lsq
cards:
  - id: mine-1
    q: |-
      My question?
    a: |-
      My answer.
    hook: |-
      My secret hook.
"""


class VisibilityCase(CourseTestCase):
    def setUp(self):
        super().setUp()
        users = get_user_model().objects
        self.owner = users.create_user("owner", email="owner@example.test", is_staff=True)
        self.student = users.create_user("student", email="student@example.test")
        self.write("private/guides/my-notes.md", NOTES)
        self.write("private/decks/mine.yaml", DECK)
        report = self.sync()
        self.assertTrue(report.ok, report.format())
        self.course_row = Course.objects.get(slug="example")

    def as_student(self):
        self.client.force_login(self.student)

    def as_owner(self):
        self.client.force_login(self.owner)

    def page(self, url):
        return self.client.get(url)

    def notes_url(self):
        return reverse("catalog:guide", args=["example", "my-notes"])

    def switch(self, ref, state, user=None):
        self.client.force_login(user or self.owner)
        return self.client.post(
            reverse("desk:visibility", args=["example"]), {"ref": ref, "state": state}
        )


class PrivateFolderTests(VisibilityCase):
    def test_what_is_in_private_is_private(self):
        notes = Document.objects.get(slug="my-notes")
        deck = Deck.objects.get(slug="mine")
        self.assertTrue(notes.from_private and deck.from_private)
        self.assertEqual((notes.visibility, deck.visibility), ("private", "private"))
        self.assertFalse(Document.objects.get(slug="01-least-squares").from_private)

    def test_only_the_owner_sees_it(self):
        self.as_student()
        self.assertEqual(self.page(self.notes_url()).status_code, 404)
        self.assertEqual(
            self.page(reverse("catalog:deck", args=["example", "mine"])).status_code, 404
        )
        for url in (
            reverse("catalog:course", args=["example"]),
            reverse("catalog:guides", args=["example"]),
            reverse("catalog:cards", args=["example"]),
            reverse("catalog:review", args=["example"]),
            reverse("catalog:memory", args=["example"]),
            reverse("catalog:search") + "?q=my",
        ):
            with self.subTest(url=url):
                page = self.page(url)
                self.assertEqual(page.status_code, 200)
                for word in ("My own notes", "My cards", "My question", "My secret hook"):
                    self.assertNotContains(page, word)

        self.as_owner()
        page = self.page(self.notes_url())
        self.assertContains(page, "Kept to myself")
        self.assertContains(page, "private folder")  # the switch is locked for it
        self.assertContains(self.page(reverse("catalog:guides", args=["example"])), "My own notes")
        self.assertContains(
            self.page(reverse("catalog:memory", args=["example"])), "My secret hook"
        )

    def test_counts_and_the_manifest_leave_it_out_for_everyone_else(self):
        from catalog.nav import course_data

        course = self.course_row
        theirs, mine = course_data(course, self.student), course_data(course, self.owner)
        self.assertNotIn("mine-1", [c["id"] for c in theirs["manifest"]["cards"]])
        self.assertIn("mine-1", [c["id"] for c in mine["manifest"]["cards"]])
        self.assertEqual(mine["counts"]["decks"], theirs["counts"]["decks"] + 1)
        self.assertEqual(mine["counts"]["guides"], theirs["counts"]["guides"] + 1)

    def test_it_never_travels_to_an_educator(self):
        names = set(handin.files_of(self.course))
        self.assertFalse([name for name in names if name.startswith("private/")])
        with self.assertRaises(handin.HandInError):
            handin.check({**handin.files_of(self.course), "private/guides/x.md": NOTES.encode()})
        self.assertFalse(source.plain("private/guides/my-notes.md"))

    def test_the_editor_does_not_offer_it(self):
        self.as_owner()
        page = self.page(reverse("editor:overview", args=["example"]))
        self.assertEqual(page.status_code, 200)
        # The owner's own navigation names the notes; the editor offers no way to edit
        # them, since they are not among the course's files.
        self.assertNotContains(page, reverse("editor:guide", args=["example", "my-notes"]))
        self.assertContains(page, reverse("editor:guide", args=["example", "choosing-a-method"]))

    def test_publish_sends_it_and_the_kit_never_has_it(self):
        archive, _count = pack({"example": self.course})
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertIn("example/private/guides/my-notes.md", tar.getnames())
        target = self.tmp / "kit" / "example"
        export_authoring.copy(self.course, target)
        self.assertFalse((target / "private").exists())

    def test_a_course_as_handed_in_keeps_the_owners_private_material(self):
        # The site reads a course educators changed from a copy of course.yaml and
        # content/ (desk/handins.py); private/ is then taken from the owner's folder.
        copy = self.tmp / "handins" / "example"
        for name, data in handin.files_of(self.course).items():
            (copy / name).parent.mkdir(parents=True, exist_ok=True)
            (copy / name).write_bytes(data)
        from ingest.sync import sync_course

        report = sync_course(copy, force=True)
        self.assertTrue(report.ok, report.format())
        self.assertTrue(Deck.objects.get(slug="mine").from_private)


class KitTests(CourseTestCase):
    def test_the_public_kit_says_nothing_of_a_private_folder(self):
        from django.conf import settings

        base = settings.BASE_DIR
        paths = [base / "ingest" / name for name in export_authoring.RULES]
        for name in export_authoring.GUIDE:
            found = base / "docs" / name
            paths += (
                sorted(p for p in found.rglob("*") if p.is_file()) if found.is_dir() else [found]
            )
        paths += sorted(p for p in (base / "ingest" / "educator_kit").rglob("*") if p.is_file())
        for path in paths:
            if path.suffix in {".py", ".md", ".json", ".yaml", ".txt"}:
                with self.subTest(path=path.name):
                    self.assertNotIn("private/", path.read_text(encoding="utf-8"))


class PrivateFolderErrorTests(CourseTestCase):
    def test_a_name_is_used_once(self):
        self.write("private/lectures/01-least-squares.md", NOTES)
        self.assertProblem(self.check(), "content/ already has the lecture `01-least-squares`")

    def test_one_file_kinds_stay_in_content(self):
        self.write("private/reference/glossary.yaml", "terms: []\n")
        self.assertProblem(self.check(), "content/ only")

    def test_shared_content_may_not_lean_on_private_material(self):
        # The educators' copy has no private/: a link from content/ to it would be broken
        # there. A link the other way is fine (the notes above link to a lecture).
        self.write("private/guides/my-notes.md", NOTES)
        self.append("content/guides/choosing-a-method.md", "\nSee [my notes](guide:my-notes).\n")
        problem = self.assertProblem(self.check(), "my-notes")
        self.assertIn("never the other way", problem.hint)

    def test_without_private_material_nothing_changes(self):
        report = self.sync()
        self.assertTrue(report.ok, report.format())
        self.assertFalse(Document.objects.filter(from_private=True).exists())
        self.assertEqual(set(Document.objects.values_list("visibility", flat=True)), {"public"})


class SwitchTests(VisibilityCase):
    def lecture_url(self):
        return reverse("catalog:lecture", args=["example", "01-least-squares"])

    def test_unlisted_opens_by_its_address_and_is_in_no_list(self):
        self.assertEqual(self.switch("lecture:01-least-squares", "unlisted").status_code, 302)
        self.assertEqual(Switch.objects.get().state, "unlisted")
        self.as_student()
        self.assertEqual(self.page(self.lecture_url()).status_code, 200)
        for url in (
            reverse("catalog:lectures", args=["example"]),
            reverse("catalog:search") + "?q=least",
        ):
            with self.subTest(url=url):
                self.assertNotContains(self.page(url), self.lecture_url() + '"')

    def test_private_is_not_there_for_anyone_else(self):
        self.switch("deck:least-squares", "private")
        self.as_student()
        self.assertEqual(
            self.page(reverse("catalog:deck", args=["example", "least-squares"])).status_code, 404
        )
        review = self.page(reverse("catalog:review", args=["example"]))
        self.assertNotContains(review, "lsq-loss")
        self.assertFalse(Card.objects.listed_for(self.student).filter(key="lsq-loss").exists())
        self.assertTrue(Card.objects.listed_for(self.owner).filter(key="lsq-loss").exists())

    def test_back_to_public_and_it_lasts_through_a_sync(self):
        self.switch("exam:final", "unlisted")
        self.sync(force=True)
        exam = self.course_row.assessments.get(slug="final")
        self.assertEqual(exam.visibility, "unlisted")
        self.assertEqual({p.visibility for p in exam.papers.all()} - {"unlisted"}, set())
        self.switch("exam:final", "public")
        self.assertFalse(Switch.objects.exists())
        self.assertEqual(self.course_row.assessments.get(slug="final").visibility, "public")

    def test_the_navigation_follows_at_once(self):
        from catalog.nav import course_data

        before = course_data(Course.objects.get(slug="example"), self.student)["counts"]["decks"]
        self.switch("deck:gradient-descent", "private")
        after = course_data(Course.objects.get(slug="example"), self.student)["counts"]["decks"]
        self.assertEqual(after, before - 1)

    def test_only_the_owner_switches(self):
        response = self.switch("lecture:01-least-squares", "private", user=self.student)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Switch.objects.exists())
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(reverse("desk:visibility", args=["example"])).status_code, 405
        )

    def test_deleting_a_switch_in_the_admin_makes_it_public_again(self):
        self.switch("deck:least-squares", "private")
        self.client.force_login(
            get_user_model().objects.create_superuser("root", email="root@example.test")
        )
        row = Switch.objects.get()
        url = reverse("admin:desk_visibility_delete", args=[row.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.assertEqual(Deck.objects.get(slug="least-squares").visibility, "public")

    def test_what_is_in_private_cannot_be_made_public(self):
        response = self.switch("guide:my-notes", "public")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Document.objects.get(slug="my-notes").visibility, "private")

    def test_nonsense_is_not_found(self):
        for ref, state in (
            ("deck:nosuch", "private"),
            ("deck:least-squares", "secret"),
            ("x", "public"),
        ):
            with self.subTest(ref=ref, state=state):
                self.assertEqual(self.switch(ref, state).status_code, 404)

    def test_every_kind_has_a_name_and_is_found_by_it(self):
        for item in visibility.items(self.course_row):
            with self.subTest(item=item):
                self.assertEqual(visibility.find(self.course_row, item.shown_ref).pk, item.pk)

    def test_the_owner_sees_the_switch_and_the_mark(self):
        self.switch("lecture:01-least-squares", "unlisted")
        self.as_owner()
        page = self.page(self.lecture_url())
        self.assertContains(page, 'name="state" value="unlisted" aria-pressed="true"')
        self.assertContains(self.page(reverse("catalog:lectures", args=["example"])), "Unlisted")
        self.as_student()
        self.assertNotContains(self.page(self.lecture_url()), 'name="state"')


class CommandTests(CourseTestCase):
    def test_sync_content_reads_the_private_folder(self):
        self.write("private/decks/mine.yaml", DECK)
        out = io.StringIO()
        with mock.patch("desk.duties.push", return_value=None), override_settings():
            call_command("sync_content", "example", stdout=out)
        cache.clear()
        self.assertTrue(Deck.objects.get(slug="mine").from_private)
        self.assertEqual(Deck.objects.get(slug="mine").visibility, Visibility.PRIVATE)
