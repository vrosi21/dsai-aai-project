"""Every page renders. The example course is synced into the test database first, so these
tests exercise the real path: files -> importer -> models -> views -> templates."""

import os
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Course
from ingest.tests.base import CourseTestCase

C = "example"

# (url name, extra args) for every page inside the example course.
COURSE_PAGES = [
    ("course", []),
    ("topic", ["lsq"]),
    ("lectures", []),
    ("lecture", ["01-least-squares"]),
    ("labs", []),
    ("lab", ["01-fit-a-line"]),
    ("homework_list", []),
    ("homework", ["01-normal-equations"]),
    ("guides", []),
    ("guide", ["choosing-a-method"]),
    ("cards", []),
    ("review", []),
    ("deck", ["least-squares"]),
    ("problems", []),
    ("problem_set", ["fitting-practice"]),
    ("exams", []),
    ("assessment", ["midterm"]),
    ("assessment", ["comprehensive"]),
    ("paper", ["midterm", "past-2025"]),
    ("paper", ["midterm", "mock-1"]),
    ("formulas", []),
    ("glossary", []),
    ("cheatsheet", []),
    ("memory", []),
    ("palace", ["gd-loop"]),
]


class PageTests(CourseTestCase):
    def setUp(self):
        super().setUp()
        report = self.sync()
        self.assertEqual(report.status, "synced", msg=report.format())

    def get(self, name, *args, **query):
        # The test client calls the view directly: no server, no browser.
        return self.client.get(reverse(f"catalog:{name}", args=args), query)

    def test_global_pages(self):
        for name in ("home", "search", "settings"):
            with self.subTest(page=name):
                self.assertEqual(self.get(name).status_code, 200)

    def test_every_course_page_renders(self):
        for name, args in COURSE_PAGES:
            with self.subTest(page=name, args=args):
                response = self.get(name, C, *args)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Example 101: Fitting a Line")

    def test_unknown_things_are_404(self):
        self.assertEqual(self.get("course", "nope").status_code, 404)
        self.assertEqual(self.get("lecture", C, "nope").status_code, 404)
        self.assertEqual(self.get("deck", C, "nope").status_code, 404)
        self.assertEqual(self.get("paper", C, "midterm", "nope").status_code, 404)
        self.assertEqual(self.get("review", C, scope="nope").status_code, 404)

    def test_home_lists_the_course_and_embeds_its_manifest(self):
        response = self.get("home")
        self.assertContains(response, "EX101")
        self.assertContains(response, 'id="all-manifests"')
        manifest = response.context["manifests"][0]
        self.assertEqual(len(manifest["cards"]), 10)
        self.assertEqual(
            {exam["slug"] for exam in manifest["exams"]}, {"midterm", "final", "comprehensive"}
        )

    def test_navigation_only_offers_what_exists(self):
        response = self.get("course", C)
        labels = [item["label"] for group in response.context["nav"] for item in group["items"]]
        self.assertIn("Flashcards", labels)
        Course.objects.get(slug=C).decks.all().delete()
        Course.objects.filter(slug=C).update(content_hash="changed")  # new cache key
        response = self.get("course", C)
        labels = [item["label"] for group in response.context["nav"] for item in group["items"]]
        self.assertNotIn("Flashcards", labels)

    def test_lecture_page(self):
        response = self.get("lecture", C, "01-least-squares")
        self.assertContains(response, '<span class="math inline">')
        self.assertContains(response, "If you remember nothing else")
        self.assertEqual(response.context["next"].slug, "02-gradient-descent")
        self.assertIsNone(response.context["previous"])
        self.assertEqual([deck.slug for deck in response.context["decks"]], ["least-squares"])

    def test_deck_page_passes_cards_as_data(self):
        response = self.get("deck", C, "least-squares", card="lsq-derive")
        player = response.context["player"]
        self.assertEqual(len(player["cards"]), 5)
        self.assertEqual(player["focus"], "lsq-derive")
        self.assertContains(response, 'id="player-data"')

    def test_review_can_be_narrowed_to_an_exam(self):
        everything = self.get("review", C).context["player"]["cards"]
        midterm = self.get("review", C, scope="midterm").context["player"]["cards"]
        self.assertEqual(len(everything), 10)
        self.assertEqual({card["topic"] for card in midterm}, {"lsq"})

    def test_exam_hub_is_derived_from_scope(self):
        context = self.get("assessment", C, "midterm").context
        self.assertEqual([topic.key for topic in context["topics"]], ["lsq"])
        self.assertEqual(context["n_cards"], 5)
        self.assertEqual(context["n_formulas"], 3)
        self.assertEqual(len(context["past_papers"]), 1)
        self.assertEqual(len(context["mock_papers"]), 1)
        comprehensive = self.get("assessment", C, "comprehensive").context
        self.assertEqual(comprehensive["n_cards"], 10)

    def test_mock_opens_in_sit_mode_and_past_paper_in_study_mode(self):
        self.assertEqual(self.get("paper", C, "midterm", "mock-1").context["paper"]["mode"], "sit")
        self.assertEqual(
            self.get("paper", C, "midterm", "past-2025").context["paper"]["mode"], "study"
        )
        forced = self.get("paper", C, "midterm", "past-2025", mode="sit")
        self.assertEqual(forced.context["paper"]["mode"], "sit")

    def test_formulas_can_be_filtered_by_topic(self):
        self.assertEqual(len(self.get("formulas", C).context["sections"]), 2)
        self.assertEqual(len(self.get("formulas", C, topic="gd").context["sections"]), 1)

    def test_search_finds_text_and_highlights_it(self):
        response = self.get("search", q="normal equations")
        self.assertGreater(response.context["total"], 3)
        self.assertContains(response, "<mark>")
        kinds = {row["kind"] for row in response.context["page"].paginator.object_list}
        self.assertTrue({"Lecture", "Flashcard", "Formula"} <= kinds)
        self.assertEqual(self.get("search", q="zzzz").context["total"], 0)

    def test_search_query_is_escaped(self):
        response = self.get("search", q="<script>alert(1)</script>")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_page_queries_stay_bounded(self):
        """A guard against N+1 queries: the count must not grow with the content."""
        self.get("course", C)  # warm the per-course cache
        with self.assertNumQueries(6):
            self.get("course", C)
        with self.assertNumQueries(3):
            self.get("deck", C, "least-squares")

    @override_settings(DEBUG=True)
    def test_notice_when_files_are_newer_than_the_last_sync(self):
        notice = "changed after the last sync"
        self.assertNotContains(self.get("course", C), notice)
        # Pretend the sync happened an hour ago, then save a file without changing it.
        Course.objects.update(synced_at=timezone.now() - timedelta(hours=1))
        os.utime(self.course / "content/decks/least-squares.yaml")
        self.assertContains(self.get("course", C), notice)
        self.assertEqual(self.sync().status, "unchanged")
        self.assertNotContains(self.get("course", C), notice)

    def test_notice_is_a_development_aid_only(self):
        Course.objects.update(synced_at=timezone.now() - timedelta(hours=1))
        os.utime(self.course / "content/decks/least-squares.yaml")
        self.assertFalse(self.get("course", C).context["stale"])  # tests run with DEBUG off


class SourceFileTests(CourseTestCase):
    def setUp(self):
        super().setUp()
        (self.course / "source/lectures").mkdir(parents=True)
        (self.course / "source/lectures/L1.pdf").write_bytes(b"%PDF-1.4 test")
        self.sync()

    def url(self, path):
        return reverse("catalog:source", args=[C, path])

    @override_settings(SERVE_SOURCE_FILES=True)
    def test_serves_a_file_under_source(self):
        response = self.client.get(self.url("lectures/L1.pdf"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")

    @override_settings(SERVE_SOURCE_FILES=True)
    def test_cannot_escape_the_source_folder(self):
        self.assertEqual(self.client.get(self.url("../course.yaml")).status_code, 404)
        self.assertEqual(
            self.client.get(self.url("../content/decks/least-squares.yaml")).status_code, 404
        )
        self.assertEqual(self.client.get(self.url("lectures/missing.pdf")).status_code, 404)

    @override_settings(SERVE_SOURCE_FILES=False)
    def test_is_off_unless_enabled(self):
        self.assertEqual(self.client.get(self.url("lectures/L1.pdf")).status_code, 404)
