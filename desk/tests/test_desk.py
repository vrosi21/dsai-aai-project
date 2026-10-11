"""The desk: asking and reporting, applying to write a course, the educator's role, and
the owner's switches for a course.

Courses are made as bare rows here. These pages only need a course to exist; what is in
it is tested with the example course elsewhere.
"""

import io
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Enrollment
from catalog.models import Course
from desk.models import CourseSetting, Educator, EducatorApplication, Request
from desk.switches import Switches

User = get_user_model()
ASK, APPLY, EDUCATORS = "/ask/", "/educators/apply/", "/educators/"
NOTIFY = "desk.views.notify"
OPEN_MIDDLEWARE = [name for name in settings.MIDDLEWARE if "LoginRequired" not in name]


def course(slug, title, **fields):
    fields.setdefault("series", slug)
    fields.setdefault("code", slug.upper())
    return Course.objects.create(slug=slug, title=title, **fields)


class DeskTestCase(TestCase):
    """Three courses: one open and unwritten, one the owner writes, one not opened yet."""

    def setUp(self):
        cache.clear()
        caches["limits"].clear()  # the rate limiter counts across tests otherwise
        self.rl = course("rl", "Reinforcement Learning", semester=5)
        self.aai = course("aai", "Applied AI", semester=5, has_educator=True)
        self.nlp = course("nlp", "Natural Language Processing", semester=6, enabled=False)
        self.ada = User.objects.create_user(
            "ada", email="ada@example.test", first_name="Ada", last_name="Lovelace"
        )
        self.bob = User.objects.create_user("bob", email="bob@example.test")
        self.admin = User.objects.create_superuser("root", email="root@example.test")
        self.client.force_login(self.ada)

    def application(self, **changes):
        form = {
            "series": "rl",
            "name": "Ada Lovelace",
            "phone": "+387 61 123 456",
            "year": "2",
            "github": "ada-l",
            "tools": "paid",
            "note": "I had a 10 in it.",
        }
        return {**form, **changes}


class AskTests(DeskTestCase):
    def test_the_form_knows_the_course_and_the_page_it_was_opened_from(self):
        page = self.client.get(ASK, {"course": "rl", "kind": "bug", "from": "/courses/rl/cards/"})
        form = page.context["form"]
        self.assertEqual(form.initial["series"], "rl")
        self.assertEqual(form.initial["kind"], "bug")
        self.assertContains(page, 'value="/courses/rl/cards/"')
        self.assertContains(page, 'value="bug" checked')

    def test_only_open_courses_can_be_asked_about(self):
        offered = [
            value for value, _ in self.client.get(ASK).context["form"].fields["series"].choices
        ]
        self.assertEqual(offered, ["", "aai", "rl"])
        # The form accepts what it offers and nothing else, even if posted by hand.
        answer = self.client.post(ASK, {"kind": "content", "series": "nlp", "message": "Please."})
        self.assertEqual(answer.status_code, 200)
        self.assertFalse(Request.objects.exists())

    def test_sending_stores_it_tells_the_owner_and_shows_it_back(self):
        # The owner is told only after the row is safely stored (transaction.on_commit).
        # A test runs inside a transaction that never commits, so Django offers
        # captureOnCommitCallbacks to run what would have run.
        with mock.patch(NOTIFY) as notify, self.captureOnCommitCallbacks(execute=True):
            answer = self.client.post(
                ASK,
                {"kind": "content", "series": "rl", "message": "Lecture 3, please.", "page": "/x/"},
            )
        self.assertRedirects(answer, f"{ASK}?sent=1")
        sent = Request.objects.get()
        self.assertEqual(
            (sent.user, sent.kind, sent.series, sent.status, sent.page),
            (self.ada, "content", "rl", "new", "/x/"),
        )
        notify.assert_called_once()
        title, message = notify.call_args.args
        self.assertEqual(title, "New request: content (Reinforcement Learning)")
        self.assertEqual(notify.call_args.kwargs["dedupe_key"], f"dsai-request-{sent.pk}")
        self.assertTrue(
            notify.call_args.kwargs["url"].endswith(f"/admin/desk/request/{sent.pk}/change/")
        )
        # Nothing about the person or what they wrote leaves the site.
        told = " ".join([title, message, *map(str, notify.call_args.kwargs.values())])
        for private in ("Ada", "ada@example.test", "Lecture 3"):
            self.assertNotIn(private, told)

        page = self.client.get(f"{ASK}?sent=1")
        self.assertContains(page, "Sent, thank you")
        self.assertContains(page, "Lecture 3, please.")
        self.assertContains(page, "Reinforcement Learning")

    def test_a_request_for_content_names_its_course(self):
        answer = self.client.post(ASK, {"kind": "content", "series": "", "message": "More."})
        self.assertContains(answer, "Say which course the content is for.")
        self.assertFalse(Request.objects.exists())
        # An idea needs none.
        self.assertRedirects(
            self.client.post(ASK, {"kind": "idea", "series": "", "message": "A dark mode."}),
            f"{ASK}?sent=1",
        )

    def test_one_person_cannot_fill_the_list(self):
        for number in range(10):
            Request.objects.create(user=self.ada, kind="idea", message=f"Idea {number}")
        with mock.patch(NOTIFY) as notify, self.captureOnCommitCallbacks(execute=True):
            answer = self.client.post(ASK, {"kind": "idea", "message": "One more."})
        self.assertContains(answer, "You have sent a lot today.")
        self.assertEqual(Request.objects.count(), 10)
        notify.assert_not_called()
        # Someone else is not held up by it.
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.post(ASK, {"kind": "idea", "message": "Mine."}).status_code, 302
        )

    def test_the_answer_shows_to_the_person_who_asked_and_to_nobody_else(self):
        Request.objects.create(
            user=self.ada, kind="bug", series="rl", message="Card 4 is wrong.",
            status="done", answer="Fixed, thank you.",
        )  # fmt: skip
        self.assertContains(self.client.get(ASK), "Fixed, thank you.")
        self.client.force_login(self.bob)
        page = self.client.get(ASK)
        self.assertNotContains(page, "Card 4 is wrong.")
        self.assertNotContains(page, "Fixed, thank you.")

    def test_the_open_site_has_nobody_to_ask(self):
        self.client.logout()
        self.assertEqual(self.client.get(ASK).status_code, 302)  # to the sign-in
        with override_settings(REQUIRE_LOGIN=False, MIDDLEWARE=OPEN_MIDDLEWARE):
            # A client reads the middleware list at its first request, so a new one.
            visitor = self.client_class()
            self.assertRedirects(visitor.get(ASK), "/")
            self.assertRedirects(visitor.post(ASK, {"kind": "idea", "message": "x"}), "/")
            self.assertNotContains(visitor.get("/"), "Ask or report")
        self.assertFalse(Request.objects.exists())


class ApplyTests(DeskTestCase):
    def test_only_courses_nobody_writes_are_offered(self):
        page = self.client.get(APPLY, {"course": "rl"})
        offered = [value for value, _ in page.context["form"].fields["series"].choices]
        self.assertEqual(offered, ["", "rl"])  # not aai (written), not nlp (not open)
        self.assertEqual(page.context["form"].initial["series"], "rl")
        self.assertContains(page, 'value="Ada Lovelace"')  # the name comes from the account
        # Asking for one that is taken says so instead of failing.
        self.assertContains(self.client.get(APPLY, {"course": "aai"}), "already has someone")

    def test_applying_stores_it_and_tells_the_owner_without_the_details(self):
        with mock.patch(NOTIFY) as notify, self.captureOnCommitCallbacks(execute=True):
            answer = self.client.post(APPLY, self.application())
        self.assertRedirects(answer, f"{EDUCATORS}?applied=1")
        offer = EducatorApplication.objects.get()
        self.assertEqual(
            (offer.user, offer.series, offer.phone, offer.year, offer.github, offer.status),
            (self.ada, "rl", "+387 61 123 456", "2", "ada-l", "pending"),
        )
        notify.assert_called_once()
        told = " ".join([*notify.call_args.args, *map(str, notify.call_args.kwargs.values())])
        self.assertIn("Reinforcement Learning", told)
        for private in ("Ada", "387", "ada-l", "I had a 10"):
            self.assertNotIn(private, told)
        page = self.client.get(f"{EDUCATORS}?applied=1")
        self.assertContains(page, "Application sent")
        self.assertContains(page, "Waiting for an answer")
        # Not an educator yet: applying gives nothing by itself.
        self.assertFalse(Educator.objects.exists())
        self.assertNotContains(page, "You write this course")

    def test_what_is_typed_is_checked_and_tidied(self):
        bad = self.client.post(APPLY, self.application(phone="call me"))
        self.assertContains(bad, "Write the number with digits")
        self.assertContains(
            self.client.post(APPLY, self.application(github="not a name!")),
            "does not look like a GitHub user name",
        )
        self.assertFalse(EducatorApplication.objects.exists())
        # A pasted profile address is reduced to the name.
        self.client.post(
            APPLY, self.application(github="https://github.com/ada-l/", phone="061  123 456")
        )
        offer = EducatorApplication.objects.get()
        self.assertEqual((offer.github, offer.phone), ("ada-l", "061 123 456"))

    def test_applying_again_for_the_same_course_updates_the_application(self):
        self.client.post(APPLY, self.application())
        first = EducatorApplication.objects.get()
        first.decide(EducatorApplication.Status.DECLINED)
        self.assertContains(self.client.get(APPLY, {"course": "rl"}), "You have applied")
        self.client.post(APPLY, self.application(note="I have more time now."))
        again = EducatorApplication.objects.get()  # still one row
        self.assertEqual(
            (again.pk, again.note, again.status), (first.pk, "I have more time now.", "pending")
        )
        self.assertIsNone(again.decided_at)

    def test_a_course_that_is_taken_or_closed_cannot_be_applied_for(self):
        for series in ("aai", "nlp", "nothing"):
            with self.subTest(series=series):
                answer = self.client.post(APPLY, self.application(series=series))
                self.assertEqual(answer.status_code, 200)
        self.assertFalse(EducatorApplication.objects.exists())


class EducatorTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        Educator.objects.create(user=self.ada, series="rl")
        self.ask = Request.objects.create(
            user=self.bob, kind="content", series="rl", message="Lecture 5 is missing."
        )
        Request.objects.create(user=self.bob, kind="idea", series="rl", message="Make it purple.")
        Request.objects.create(user=self.bob, kind="bug", series="aai", message="AAI typo.")

    def test_an_educator_sees_their_course_and_how_to_hand_work_in(self):
        page = self.client.get(EDUCATORS)
        self.assertContains(page, "You write this course")
        self.assertContains(page, "courses/rl/course.yaml")
        self.assertContains(page, "Update the CMS for this course: rl")
        self.assertContains(page, "uv run tools/pack.py rl")
        self.assertContains(page, settings.EDUCATOR_KIT_URL)
        self.assertContains(page, 'href="/educators/courses/rl/files.zip"')
        # Their own course is no longer among those looking for someone.
        self.assertEqual([c.slug for c in page.context["seeking"]], [])
        # Others see none of it.
        self.client.force_login(self.bob)
        other = self.client.get(EDUCATORS)
        self.assertNotContains(other, "You write this course")
        self.assertNotContains(other, settings.EDUCATOR_KIT_URL)

    def test_an_educator_of_a_course_students_cannot_see_yet_still_has_it(self):
        Educator.objects.create(user=self.ada, series="nlp")
        page = self.client.get(EDUCATORS)
        self.assertContains(page, "Natural Language Processing")
        self.assertContains(page, 'href="/educators/courses/nlp/files.zip"')

    def test_they_can_download_the_files_of_their_course_and_only_theirs(self):
        """The zip is built from the course's folder, so this needs one on disk."""
        with tempfile.TemporaryDirectory() as scratch:
            folder = Path(scratch) / "rl"
            (folder / "content" / "decks").mkdir(parents=True)
            (folder / "source").mkdir()
            (folder / "course.yaml").write_text("title: Reinforcement Learning\n", "utf-8")
            (folder / "content" / "decks" / "mdp.yaml").write_text("title: MDPs\n", "utf-8")
            (folder / "source" / "L1.pdf").write_bytes(b"%PDF")
            with override_settings(CONTENT_ROOTS=[Path(scratch)]):
                answer = self.client.get("/educators/courses/rl/files.zip")
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer["Content-Type"], "application/zip")
                self.assertIn('filename="rl.zip"', answer["Content-Disposition"])
                with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
                    self.assertEqual(
                        sorted(archive.namelist()),
                        ["rl/content/decks/mdp.yaml", "rl/course.yaml"],
                    )
                # Another course is not theirs, and another person gets nothing at all.
                self.assertEqual(
                    self.client.get("/educators/courses/aai/files.zip").status_code, 404
                )
                self.client.force_login(self.bob)
                self.assertEqual(
                    self.client.get("/educators/courses/rl/files.zip").status_code, 404
                )
                self.client.force_login(self.admin)
                self.assertEqual(
                    self.client.get("/educators/courses/rl/files.zip").status_code, 200
                )

    def test_they_see_what_was_asked_about_it_but_not_by_whom(self):
        page = self.client.get(EDUCATORS)
        self.assertContains(page, "Lecture 5 is missing.")
        self.assertNotContains(page, "Make it purple.")  # an idea for the site is the owner's
        self.assertNotContains(page, "AAI typo.")  # another course
        self.assertNotContains(page, "bob")

    def test_they_can_answer_it_and_nobody_else_can(self):
        done = reverse("desk:resolve", args=[self.ask.pk])
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(done, {"answer": "No."}).status_code, 404)
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(done).status_code, 405)  # changing things needs a POST
        self.assertRedirects(self.client.post(done, {"answer": "Added today."}), EDUCATORS)
        self.ask.refresh_from_db()
        self.assertEqual((self.ask.status, self.ask.answer), ("done", "Added today."))
        self.assertNotContains(self.client.get(EDUCATORS), "Lecture 5 is missing.")
        # The person who asked reads the answer on their own page.
        self.client.force_login(self.bob)
        self.assertContains(self.client.get(ASK), "Added today.")

    def test_a_request_about_another_course_is_not_theirs_to_answer(self):
        other = Request.objects.get(series="aai")
        answer = self.client.post(reverse("desk:resolve", args=[other.pk]), {"answer": "Done."})
        self.assertEqual(answer.status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.status, "new")


class SwitchTests(DeskTestCase):
    """Each question starts from the course's own file and is overridden by the owner's
    switch, in both directions."""

    def test_the_course_file_decides_until_the_owner_does(self):
        gate = Switches()
        self.assertEqual(
            [gate.is_open(c) for c in (self.rl, self.aai, self.nlp)], [True, True, False]
        )
        self.assertEqual(
            [gate.has_educator(c) for c in (self.rl, self.aai, self.nlp)], [False, True, False]
        )
        CourseSetting.objects.create(series="nlp", enabled=True)
        CourseSetting.objects.create(series="rl", enabled=False)
        CourseSetting.objects.create(series="aai", has_educator=False)
        gate = Switches()
        self.assertEqual(
            [gate.is_open(c) for c in (self.rl, self.aai, self.nlp)], [False, True, True]
        )
        self.assertFalse(gate.has_educator(self.aai))

    def test_an_accepted_educator_counts_as_someone_writing_it(self):
        Educator.objects.create(user=self.ada, series="rl")
        self.assertTrue(Switches().has_educator(self.rl))

    def test_a_page_asks_the_database_once_however_many_courses_it_lists(self):
        gate = Switches()
        with self.assertNumQueries(2):  # the switches, then the educators
            for _ in range(5):
                for one in (self.rl, self.aai, self.nlp):
                    gate.is_open(one)
                    gate.has_educator(one)


class ClosedCourseTests(DeskTestCase):
    """A course the owner has not opened does not exist for students."""

    def test_it_is_nowhere_a_student_looks(self):
        Enrollment.objects.create(user=self.ada, series="rl")
        self.assertEqual(self.client.get("/courses/nlp/").status_code, 404)
        choose = self.client.get("/my-courses/")
        offered = [c.slug for group in choose.context["groups"] for c in group["courses"]]
        self.assertCountEqual(offered, ["rl", "aai"])
        self.assertNotContains(self.client.get("/"), "Natural Language Processing")
        self.assertNotContains(self.client.get("/search/"), "Natural Language Processing")
        # It cannot be put on a dashboard by posting its name either.
        self.client.post("/my-courses/change/", {"series": "nlp", "action": "add"})
        self.assertEqual(Enrollment.objects.series_of(self.ada), {"rl"})

    def test_the_owner_can_look_at_it_and_is_told_it_is_closed(self):
        self.client.force_login(self.admin)
        page = self.client.get("/courses/nlp/")
        self.assertContains(page, "Students cannot see this course yet")
        self.assertNotContains(self.client.get("/courses/rl/"), "Students cannot see this course")

    def test_opening_it_in_the_admin_makes_it_appear(self):
        self.client.force_login(self.admin)
        courses = reverse("admin:catalog_course_changelist")
        self.assertContains(self.client.get(courses), "No (course file)")
        answer = self.client.post(
            courses, {"action": "open_course", "_selected_action": [self.nlp.pk]}
        )
        self.assertRedirects(answer, courses)
        self.assertTrue(CourseSetting.objects.get(series="nlp").enabled)
        self.assertContains(self.client.get(courses), "Yes (set here)")
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get("/courses/nlp/").status_code, 200)

    def test_the_switches_survive_a_rebuilt_course_table(self):
        CourseSetting.objects.create(series="nlp", enabled=True)
        Course.objects.all().delete()  # what a sync from scratch does
        again = course("nlp", "Natural Language Processing", enabled=False)
        self.assertTrue(Switches().is_open(again))


class CoursePageTests(DeskTestCase):
    """What a course's own page says when it has nothing in it yet."""

    def test_an_unwritten_course_can_be_asked_for_or_taken_on(self):
        page = self.client.get("/courses/rl/")
        self.assertContains(page, "No content yet")
        self.assertContains(page, "Nobody is writing this course yet")
        self.assertContains(page, "/ask/?course=rl&amp;kind=content")
        self.assertContains(page, "/educators/apply/?course=rl")

    def test_a_course_someone_writes_only_offers_to_ask(self):
        page = self.client.get("/courses/aai/")
        self.assertContains(page, "This course is being written")
        self.assertContains(page, "/ask/?course=aai&amp;kind=content")
        self.assertNotContains(page, "Become its educator")
        # The same once an applicant has been accepted for the other one.
        Educator.objects.create(user=self.bob, series="rl")
        self.assertNotContains(self.client.get("/courses/rl/"), "Become its educator")

    def test_an_earlier_year_asks_for_nothing(self):
        course("rl-2027", "Reinforcement Learning", series="rl", year="2027-28", semester=5)
        Course.objects.filter(slug="rl").update(year="2026-27")
        old = self.client.get("/courses/rl/")
        self.assertContains(old, "Nothing was written up for this year")
        self.assertNotContains(old, "Become its educator")
        self.assertContains(self.client.get("/courses/rl-2027/"), "Become its educator")

    def test_the_menu_carries_the_way_to_ask_from_every_page(self):
        page = self.client.get("/courses/rl/")
        self.assertContains(page, "/ask/?course=rl&amp;from=/courses/rl/")
        self.assertContains(self.client.get("/settings/"), "/ask/?from=/settings/")
        self.assertContains(page, 'href="/educators/"')


class DeskAdminTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(APPLY, self.application())
        self.offer = EducatorApplication.objects.get()
        self.ask = Request.objects.create(
            user=self.ada, kind="bug", series="rl", message="Card 4 is wrong.", page="/courses/rl/"
        )
        self.client.force_login(self.admin)
        self.OFFERS = reverse("admin:desk_educatorapplication_changelist")
        self.REQUESTS = reverse("admin:desk_request_changelist")

    def test_requests_and_applications_are_listed_with_their_course(self):
        requests = self.client.get(self.REQUESTS)
        self.assertContains(requests, "Card 4 is wrong.")
        self.assertContains(requests, "RL · Reinforcement Learning")
        offers = self.client.get(self.OFFERS)
        self.assertContains(offers, "+387 61 123 456")
        self.assertContains(offers, "Ada Lovelace")

    def test_a_request_is_answered_but_what_was_written_stays_as_it_was(self):
        page = reverse("admin:desk_request_change", args=[self.ask.pk])
        self.assertContains(self.client.get(page), "Card 4 is wrong.")
        self.client.post(page, {"status": "done", "answer": "Fixed.", "message": "Tampered."})
        self.ask.refresh_from_db()
        self.assertEqual(
            (self.ask.status, self.ask.answer, self.ask.message),
            ("done", "Fixed.", "Card 4 is wrong."),
        )
        self.assertEqual(self.client.get(reverse("admin:desk_request_add")).status_code, 403)

    def test_requests_can_be_closed_from_the_list(self):
        self.client.post(
            self.REQUESTS, {"action": "mark_declined", "_selected_action": [self.ask.pk]}
        )
        self.ask.refresh_from_db()
        self.assertEqual(self.ask.status, "declined")

    def test_accepting_an_application_makes_the_person_the_educator(self):
        chosen = {"action": "accept", "_selected_action": [self.offer.pk]}
        self.assertRedirects(self.client.post(self.OFFERS, chosen), self.OFFERS)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, "accepted")
        self.assertIsNotNone(self.offer.decided_at)
        self.assertTrue(Educator.objects.filter(user=self.ada, series="rl").exists())
        # Twice is the same as once.
        self.client.post(self.OFFERS, chosen)
        self.assertEqual(Educator.objects.count(), 1)

        self.client.force_login(self.ada)
        page = self.client.get(EDUCATORS)
        self.assertContains(page, "You write this course")
        self.assertContains(page, "Accepted")
        self.assertNotContains(self.client.get("/courses/rl/"), "Become its educator")

    def test_taking_an_acceptance_back_takes_the_role_back(self):
        self.offer.decide(EducatorApplication.Status.ACCEPTED)
        self.client.post(self.OFFERS, {"action": "decline", "_selected_action": [self.offer.pk]})
        self.assertFalse(Educator.objects.exists())
        # The same through the form of one application.
        page = reverse("admin:desk_educatorapplication_change", args=[self.offer.pk])
        self.client.post(page, {"status": "accepted"})
        self.assertTrue(Educator.objects.filter(user=self.ada, series="rl").exists())
        self.client.post(page, {"status": "declined", "phone": "tampered"})
        self.offer.refresh_from_db()
        self.assertEqual((self.offer.status, self.offer.phone), ("declined", "+387 61 123 456"))
        self.assertFalse(Educator.objects.exists())

    def test_an_educator_can_be_added_by_hand_for_a_course_that_exists(self):
        add = reverse("admin:desk_educator_add")
        self.assertContains(self.client.get(add), "RL · Reinforcement Learning")
        self.client.post(add, {"user": self.bob.pk, "series": "nowhere"})
        self.assertFalse(Educator.objects.exists())
        self.client.post(add, {"user": self.bob.pk, "series": "aai"})
        self.assertTrue(Educator.objects.filter(user=self.bob, series="aai").exists())

    def test_the_course_list_says_who_writes_what_and_switches_it(self):
        courses = reverse("admin:catalog_course_changelist")
        page = self.client.get(courses)
        self.assertContains(page, "Nobody yet")
        self.assertContains(page, "Someone (course file)")
        self.offer.decide(EducatorApplication.Status.ACCEPTED)
        self.assertContains(self.client.get(courses), "ada@example.test")

        self.client.post(courses, {"action": "needs_writer", "_selected_action": [self.aai.pk]})
        self.assertFalse(CourseSetting.objects.get(series="aai").has_educator)
        self.assertContains(self.client.get(courses), "Nobody (set here)")
        self.client.post(courses, {"action": "follow_file", "_selected_action": [self.aai.pk]})
        self.assertFalse(CourseSetting.objects.filter(series="aai").exists())

    def test_deleting_a_person_takes_what_they_sent_along(self):
        Educator.objects.create(user=self.ada, series="rl")
        people = reverse("admin:auth_user_changelist")
        chosen = {"action": "delete_selected", "_selected_action": [self.ada.pk]}
        self.assertContains(self.client.post(people, chosen), "Are you sure")
        self.client.post(people, {**chosen, "post": "yes"})
        self.assertFalse(User.objects.filter(pk=self.ada.pk).exists())
        for model in (Request, EducatorApplication, Educator):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_none_of_it_is_open_to_other_people(self):
        self.client.force_login(self.ada)
        for path in (self.REQUESTS, self.OFFERS, reverse("admin:desk_educator_changelist")):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)
        self.client.post(self.OFFERS, {"action": "accept", "_selected_action": [self.offer.pk]})
        self.assertFalse(Educator.objects.exists())
