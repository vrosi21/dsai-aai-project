"""Academic years: which a course can have (catalog/editions.py), adding one in the editor
(editor.whole.years), and a year that began on the site reaching the owner's course files
(desk.handins.begin, import_course)."""

import datetime as dt
import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from catalog import editions
from catalog.models import Card, Course, Document
from desk import handins
from desk.models import HandIn
from ingest import handin, live
from ingest.sync import sync_all

from .test_editor import EDIT, TITLE, C, EditorCase

YEARS = f"{EDIT}years/"
TODAY = dt.date(2026, 10, 13)
NEW = "example-2025"  # the address a year 2025-26 of `example` gets


class CalendarTests(SimpleTestCase):
    def test_the_year_turns_in_october(self):
        self.assertEqual(editions.current_year(dt.date(2026, 9, 30)), "2025-26")
        self.assertEqual(editions.current_year(dt.date(2026, 10, 1)), "2026-27")
        self.assertEqual(editions.current_year(dt.date(2027, 1, 15)), "2026-27")
        self.assertEqual(editions.current_year(dt.date(2099, 12, 31)), "2099-00")

    def test_the_years_there_can_be_follow_from_the_date_and_one_setting(self):
        self.assertEqual(editions.years(TODAY), ["2026-27", "2025-26", "2024-25"])
        self.assertEqual(editions.years(dt.date(2027, 10, 1))[0], "2027-28")
        with override_settings(FIRST_ACADEMIC_YEAR="2026-27"):
            self.assertEqual(editions.years(TODAY), ["2026-27"])
        with override_settings(ACADEMIC_YEAR_STARTS=9):
            self.assertEqual(editions.current_year(dt.date(2026, 9, 1)), "2026-27")

    def test_a_year_with_something_in_it_comes_before_an_empty_newer_one(self):
        def course(slug, year, has_content):
            return Course(
                slug=slug, title=slug, code=slug, year=year, series="x", has_content=has_content
            )

        old, new = course("x", "2025-26", True), course("x-2026", "2026-27", False)
        self.assertEqual(editions.latest([old, new]), [old])
        new.has_content = True
        self.assertEqual(editions.latest([old, new]), [new])
        old.has_content = new.has_content = False
        self.assertEqual(editions.latest([old, new]), [new])  # neither: the newest


@mock.patch("catalog.editions.timezone.localdate", return_value=TODAY)
class AddYearTests(EditorCase):
    def test_the_course_page_offers_it_to_those_who_write_the_course(self, _today):
        self.assertTrue(Course.objects.get(slug=C).has_content)  # noted by the sync
        page = self.client.get(f"/courses/{C}/")
        self.assertContains(page, f'href="{YEARS}"')
        self.client.force_login(self.eve)
        self.assertNotContains(self.client.get(f"/courses/{C}/"), "Add a year")
        self.assertEqual(self.client.get(YEARS).status_code, 404)

    def test_the_years_it_does_not_have_are_listed(self, _today):
        page = self.client.get(YEARS)
        self.assertEqual([value for value, _label in page.context["years"]], ["2025-26", "2024-25"])
        self.assertContains(page, "from 2024/25 to 2026/27")

    def test_an_empty_year_is_a_course_of_its_own_in_the_same_series(self, _today):
        answer = self.client.post(YEARS, {"year": "2025-26", "copy": ""})
        self.assertRedirects(answer, f"/edit/{NEW}/?saved=year", fetch_redirect_response=False)
        made = Course.objects.get(slug=NEW)
        self.assertEqual(
            (made.series, made.year, made.title, made.code), (C, "2025-26", TITLE, "EX101")
        )
        self.assertEqual((made.hue, made.semester, made.has_content), (200, 1, False))
        self.assertEqual(list(made.topics.values_list("key", flat=True)), ["lsq", "gd"])
        # It is kept the way any change is: a first step, with nothing before it.
        step = HandIn.objects.get()
        self.assertEqual(
            (step.slug, step.series, step.base_version, step.via), (NEW, C, "", "site")
        )
        self.assertEqual(step.new_files, ["course.yaml"])
        self.assertEqual(live.folder(NEW), handins.root() / NEW)
        self.assertFalse((self.tmp / "courses" / NEW).exists())  # no folder of the owner's
        text = (live.folder(NEW) / "course.yaml").read_text("utf-8")
        head = f"title: '{TITLE}'\ncode: EX101\nyear: 2025-26\nseries: example\nsemester: 1\n"
        self.assertTrue(text.startswith(head), msg=text[:200])
        # Its pages work, and both years offer each other.
        self.assertContains(self.client.get(answer["Location"]), "This is the new year: 2025/26.")
        page = self.client.get(f"/courses/{NEW}/")
        self.assertContains(page, f'href="/courses/{C}/"')
        self.assertContains(self.client.get(f"/courses/{C}/"), f'href="/courses/{NEW}/"')
        # The dashboard keeps opening the year that has something in it.
        self.assertEqual([c.slug for c in editions.latest(list(Course.objects.all()))], [C])
        # And 2025-26 is no longer on offer.
        self.assertEqual([v for v, _l in self.client.get(YEARS).context["years"]], ["2024-25"])

    def test_a_year_can_begin_as_a_copy_and_is_then_changed_on_its_own(self, _today):
        answer = self.client.post(YEARS, {"year": "2024-25", "copy": C})
        self.assertEqual(answer.status_code, 302)
        old = Course.objects.get(slug="example-2024")
        self.assertTrue(old.has_content)
        self.assertEqual(old.documents.count(), Course.objects.get(slug=C).documents.count())
        self.assertTrue(Card.objects.filter(course=old, key="lsq-loss").exists())
        # The same ids, in another course: each year's progress is its own.
        self.assertEqual(Card.objects.filter(key="lsq-loss").count(), 2)
        url = "/edit/example-2024/guides/choosing-a-method/"
        self.client.post(url, {**self.form_of(url), "summary": "As it was taught in 2024."})
        self.assertEqual(
            Document.objects.get(course=old, slug="choosing-a-method").summary,
            "As it was taught in 2024.",
        )
        self.assertNotEqual(
            Document.objects.get(course__slug=C, slug="choosing-a-method").summary,
            "As it was taught in 2024.",
        )
        self.assertEqual(HandIn.objects.filter(slug="example-2024").count(), 1)  # one sitting

    def test_only_a_year_that_is_missing_can_be_added(self, _today):
        for year in ("2026-27", "2023-24", "2027-28", "nonsense", ""):
            with self.subTest(year=year):
                answer = self.client.post(YEARS, {"year": year})
                self.assertContains(answer, "Choose one of the years listed.")
        self.assertEqual(Course.objects.count(), 1)
        self.assertFalse(HandIn.objects.exists())

    def test_a_course_that_has_every_year_says_so(self, _today):
        with override_settings(FIRST_ACADEMIC_YEAR="2026-27"):
            self.assertContains(self.client.get(YEARS), "exists in every year there is")
            self.assertNotContains(self.client.get(f"/courses/{C}/"), "Add a year")

    def test_a_full_sync_keeps_it_and_says_what_the_owner_is_to_do(self, _today):
        self.client.post(YEARS, {"year": "2025-26"})
        reports = {report.course: report for report in sync_all()}
        self.assertIn(NEW, reports)
        self.assertNotEqual(reports[NEW].status, "removed")
        self.assertIn("started on the site", " ".join(reports[NEW].notes))
        self.assertTrue(Course.objects.filter(slug=NEW).exists())

    def test_taking_the_first_step_back_takes_the_year_back(self, _today):
        self.client.post(YEARS, {"year": "2025-26"})
        handins.withdraw(HandIn.objects.get(), by=self.admin)
        self.assertFalse(Course.objects.filter(slug=NEW).exists())
        self.assertIsNone(live.folder(NEW))
        self.assertEqual([v for v, _l in self.client.get(YEARS).context["years"]][0], "2025-26")

    def test_it_reaches_the_owners_files_through_import_course(self, _today):
        self.client.post(YEARS, {"year": "2025-26", "copy": C})
        url = f"/edit/{NEW}/guides/choosing-a-method/"
        self.client.post(url, {**self.form_of(url), "summary": "Written on the site."})
        newest = HandIn.objects.filter(slug=NEW).order_by("-pk").first()
        self.client.force_login(self.admin)
        zipped = self.tmp / f"{NEW}.zip"
        zipped.write_bytes(self.client.get(f"/educators/hand-ins/{newest.pk}.zip").content)

        out = io.StringIO()
        call_command("import_course", str(zipped), "--check", stdout=out)
        self.assertIn("Its folder would be made at", out.getvalue())
        self.assertFalse((self.tmp / "courses" / NEW).exists())
        call_command("import_course", str(zipped), stdout=out)
        folder = self.tmp / "courses" / NEW  # beside the course it is a year of
        self.assertIn("a year started on the site", out.getvalue())
        self.assertEqual(handin.content_version(folder), newest.version)
        self.assertIn(
            "Written on the site.",
            (folder / "content/guides/choosing-a-method.md").read_text("utf-8"),
        )
        # Published, the site reads it from there and lets go of its own copy.
        sync_all()
        self.assertEqual(live.folder(NEW), folder)
        self.assertEqual(set(HandIn.objects.values_list("status", flat=True)), {"merged"})

    def test_a_zip_of_a_course_nobody_has_is_still_refused(self, _today):
        files = handin.files_of(self.course)
        zipped = self.tmp / "stranger.zip"
        zipped.write_bytes(handin.pack_files("stranger", files))
        with self.assertRaisesMessage(CommandError, "not create a course"):
            call_command("import_course", str(zipped), stdout=io.StringIO())
        files["course.yaml"] = files["course.yaml"].replace(
            b"code: EX101", b"code: EX101\nseries: nobody"
        )
        zipped.write_bytes(handin.pack_files("stranger", files))
        with self.assertRaisesMessage(CommandError, "not create a course"):
            call_command("import_course", str(zipped), stdout=io.StringIO())
        self.assertFalse((self.tmp / "courses" / "stranger").exists())
