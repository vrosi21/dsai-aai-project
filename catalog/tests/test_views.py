"""Every page renders. The example course is synced into the test database first, so these
tests exercise the real path: files -> importer -> models -> views -> templates."""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Enrollment
from catalog.models import Course
from catalog.views.home import dashboard_groups
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


def sign_in(test):
    """Every page needs a signed-in person. force_login skips the sign-in form and puts
    a session for this user straight into the test client.

    The person has already chosen every course there is at this moment, so the dashboard
    shows them; choosing itself is tested in test_mine.py."""
    user = get_user_model().objects.create_user("reader", email="reader@example.test")
    Enrollment.objects.replace(user, Course.objects.values_list("series", flat=True))
    test.client.force_login(user)
    return user


class DashboardGroupTests(SimpleTestCase):
    """How courses are sorted into sections. The function only reads attributes, so
    unsaved Course objects are enough and no database is needed (SimpleTestCase)."""

    @staticmethod
    def course(slug, semester=None, year="2026-27", status="active", order=0):
        return Course(
            slug=slug, title=slug, code=slug, semester=semester, year=year, status=status,
            order=order,
        )  # fmt: skip

    def sections(self, *courses):
        groups, archived = dashboard_groups(list(courses))
        shown = [(g["title"], [c.slug for c in g["courses"]]) for g in groups]
        return shown, [c.slug for c in archived]

    def test_current_semesters_first_then_finished_ones(self):
        shown, archived = self.sections(
            self.course("math", 3),
            self.course("old-b", 4, "2025-26", "archived", order=2),
            self.course("old-a", 4, "2025-26", "archived", order=1),
            self.course("rl", 5, order=2),
            self.course("aai", 5, order=1),
        )
        self.assertEqual(
            shown,
            [
                ("Semester V", ["aai", "rl"]),
                ("Semester III", ["math"]),
                ("Semester IV", ["old-a", "old-b"]),
            ],
        )
        self.assertEqual(archived, [])

    def test_without_a_semester_the_year_is_the_section(self):
        shown, archived = self.sections(
            self.course("new", year="2026-27"),
            self.course("bare", year=""),
            self.course("done", year="2024-25", status="archived"),
        )
        self.assertEqual(shown, [("2026/27", ["new"]), ("Courses", ["bare"])])
        self.assertEqual(archived, ["done"])

    def test_section_note_names_the_years_it_spans(self):
        groups, _ = dashboard_groups(
            [self.course("a", 5, "2026-27"), self.course("b", 5, "2025-26")]
        )
        self.assertEqual(groups[0]["note"], "2025/26 and 2026/27")


class PageTests(CourseTestCase):
    def setUp(self):
        super().setUp()
        report = self.sync()
        self.assertEqual(report.status, "synced", msg=report.format())
        self.user = sign_in(self)

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

    def test_home_puts_the_course_under_its_semester(self):
        response = self.get("home")
        self.assertEqual([g["title"] for g in response.context["groups"]], ["Semester I"])
        self.assertEqual(response.context["archived"]["courses"], [])
        self.assertContains(response, "Semester I")
        self.assertContains(self.get("course", C), "Semester I")

    def test_a_course_with_nothing_in_it_is_named_but_not_offered(self):
        """It gets no card, no place in the navigation and no row in Settings: each of
        those would promise something to study. It is named, and its name leads to its
        own page, which is where it can be asked for."""
        Course.objects.create(
            slug="rl", series="rl", title="Reinforcement Learning", code="RL", semester=1
        )
        Enrollment.objects.create(user=self.user, series="rl")  # chosen, and still empty
        response = self.get("home")
        group = response.context["groups"][0]
        self.assertEqual([c.slug for c in group["ready"]], [C])
        self.assertEqual([c.slug for c in group["waiting"]], ["rl"])
        self.assertContains(
            response,
            'Not written up yet: <a href="/courses/rl/"><b>Reinforcement Learning</b></a>.',
        )
        # And the way to ask for it is right there, with the course already filled in.
        self.assertContains(
            response,
            'Need it? <a class="link" href="/ask/?kind=content&amp;course=rl&amp;from=/">'
            "Ask for it</a>",
        )
        form = self.client.get("/ask/?kind=content&course=rl&from=/").context["form"]
        self.assertEqual(form.initial["series"], "rl")
        # With several, the form is where one is chosen.
        Course.objects.create(slug="aai", series="aai", title="Applied AI", code="AAI", semester=1)
        Enrollment.objects.create(user=self.user, series="aai")
        response = self.get("home")
        self.assertContains(
            response,
            'Need one of them? <a class="link" href="/ask/?kind=content&amp;from=/">Ask for it</a>',
        )
        self.assertNotContains(response, 'class="course-card tone" href="/courses/rl/"')
        self.assertEqual([c.slug for c in response.context["nav_courses"]], [])  # C is archived
        self.assertNotContains(self.get("settings"), "Reinforcement Learning")

    def test_the_navigation_lists_active_courses_that_have_content(self):
        Course.objects.filter(slug=C).update(status="active")
        Course.objects.create(slug="rl", title="Reinforcement Learning", code="RL")
        response = self.get("search")
        self.assertEqual([c.slug for c in response.context["nav_courses"]], [C])

    def test_instructions_for_authors_are_shown_to_staff_only(self):
        Course.objects.create(slug="rl", title="Reinforcement Learning", code="RL")
        for page in (self.get("course", "rl"), self.get("home"), self.get("settings")):
            self.assertNotContains(page, "sync_content")
            self.assertNotContains(page, "source/")
        self.assertContains(self.get("course", "rl"), "Nobody is writing this course yet")
        self.user.is_staff = True
        self.user.save()
        self.assertContains(self.get("course", "rl"), "sync_content rl")
        self.assertContains(self.get("settings"), "sync_content")

    def test_the_tour_is_on_every_page_and_offered_on_the_dashboard(self):
        # The dialog itself is driven by static/js/components/tour.js; what the server
        # decides is where it is offered unasked, and where the buttons to reopen it are.
        home, settings_page, course = self.get("home"), self.get("settings"), self.get("course", C)
        for page in (home, settings_page, course):
            self.assertContains(page, '<dialog class="dialog tour"')
        self.assertContains(home, 'x-data="tour(true)"')
        self.assertContains(settings_page, 'x-data="tour(false)"')
        self.assertContains(course, 'x-data="tour(false)"')
        self.assertContains(home, "How it works</button>")
        self.assertContains(settings_page, "Show the tour</button>")
        # Each button dispatches the event the dialog listens for.
        self.assertContains(home, "@click=\"$dispatch('tour')\"")
        self.assertContains(settings_page, "@click=\"$dispatch('tour')\"")

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
        """A guard against N+1 queries: the count must not grow with the content.

        Two of each are for the request itself: the session, then the signed-in user;
        one is the owner's switches (is this course open?), one asks whether this person
        writes the course (they are offered to edit it; staff are not asked), and one is
        the bell at the top (this person's notifications). The course's front
        page has three more: its years, whether this person has it on their dashboard,
        and what this person has been shown (tour, news). A course whose file does not
        say that someone writes it costs one more there: the list of educators."""
        self.get("course", C)  # warm the per-course cache
        with self.assertNumQueries(14):
            self.get("course", C)
        with self.assertNumQueries(8):
            self.get("deck", C, "least-squares")

    def test_home_is_the_dashboard_once_signed_in(self):
        response = self.get("home")
        self.assertTemplateUsed(response, "catalog/home.html")
        # What the progress store in the browser needs to know whose data this is.
        self.assertContains(response, f'data-user="{self.user.pk}"')
        self.assertContains(response, 'data-sync="/api/progress/sync/"')

    def test_course_counts_everything_that_can_be_rated(self):
        counts = self.get("course", C).context["counts"]
        self.assertEqual(counts["reading"], 5)  # 2 lectures, a lab, a homework, a guide

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
        sign_in(self)

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
