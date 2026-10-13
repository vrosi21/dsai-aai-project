"""The editor's forms for files that are one form each: an exam's page, a memory palace,
the course's own file; and an exam's papers, which are files of questions inside it
(editor/whole.py, and editor/shelves.py for the papers)."""

from catalog.models import Assessment, Course, Palace, Question, QuestionSet, Topic
from desk.models import HandIn

from .test_editor import EDIT, TITLE, C, EditorCase
from .test_items import sent

EXAMS = f"{EDIT}exams/"
MIDTERM = "content/exams/midterm/assessment.yaml"
PALACE = "content/memory/palaces/gd-loop.yaml"


class WholeCase(EditorCase):
    def form(self, url):
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        fields = [f for section in page.context["sections"] for f in section["fields"]]
        return {**sent(fields), "seen": page.context["seen"]}


class ExamTests(WholeCase):
    ABOUT = f"{EXAMS}midterm/about/"

    def test_every_field_of_an_exam_reads_back_as_it_is(self):
        """The largest form there is: a scope, weights, a plan of phases with tasks,
        predictions with links. Sent back untouched it must change nothing."""
        data = self.form(self.ABOUT)
        self.assertEqual((data["kind"], data["date"]), ("midterm", "2026-11-20"))
        self.assertEqual(data["scope-lectures"], ["1"])
        self.assertIn("plan-phases-0-tasks-0-text", data)
        answer = self.client.post(self.ABOUT, data)
        self.assertRedirects(answer, f"{EXAMS}midterm/?saved=same", fetch_redirect_response=False)
        self.assertFalse(HandIn.objects.exists())

    def test_only_what_was_changed_is_written(self):
        before = self.read(MIDTERM)
        data = self.form(self.ABOUT) | {"date": "2026-11-27", "weights-0-weight": "4"}
        answer = self.client.post(self.ABOUT, data)
        self.assertRedirects(answer, f"{EXAMS}midterm/?saved=1", fetch_redirect_response=False)
        after = self.on_site(MIDTERM)
        self.assertIn("date: 2026-11-27\n", after)
        self.assertIn("  - topic: lsq\n    weight: 4\n", after)
        # The comments at the top, the format and the whole plan: as their author wrote them.
        self.assertEqual(after.split("date:")[0], before.split("date:")[0])
        self.assertEqual(after.split("\nplan:")[1], before.split("\nplan:")[1])
        self.assertIn("lectures: [1]          # lecture numbers", after)
        exam = Assessment.objects.get(course__slug=C, slug="midterm")
        self.assertEqual((exam.date.isoformat(), exam.weights[0]["weight"]), ("2026-11-27", 4))

    def test_a_task_is_added_to_a_phase_and_a_wrong_link_is_refused(self):
        data = self.form(self.ABOUT)
        data["plan-phases-0-tasks-9-text"] = "Read [the guide](guide:choosing-a-method)."
        data["plan-phases-0-tasks-9-links"] = ["deck:gradient-descent"]
        self.assertEqual(self.client.post(self.ABOUT, data).status_code, 302)
        self.assertIn("- deck:gradient-descent\n", self.on_site(MIDTERM))
        self.assertIn("gradient-descent", str(Assessment.objects.get(slug="midterm").plan))
        data = self.form(self.ABOUT)
        data["plan-phases-0-tasks-9-text"] = "And one more."
        data["plan-phases-0-tasks-9-links"] = ["deck:no-such-deck"]
        answer = self.client.post(self.ABOUT, data)
        self.assertContains(answer, "no deck named `no-such-deck`")
        data = self.form(self.ABOUT) | {"title": "", "weights-0-weight": "9"}
        answer = self.client.post(self.ABOUT, data)
        fields = {f.node.key: f for f in answer.context["sections"][0]["fields"]}
        self.assertTrue(fields["title"].errors)
        self.assertTrue(fields["weights"].rows[0][1].errors)  # 1 to 5
        self.assertEqual(HandIn.objects.count(), 1)

    def test_a_new_exam_then_a_paper_then_a_question(self):
        data = {"address": "resit", "title": "Resit", "kind": "final", "scope-topics": ["gd"]}
        answer = self.client.post(f"{EXAMS}new/", data)
        self.assertRedirects(answer, f"{EXAMS}resit/?saved=new", fetch_redirect_response=False)
        self.assertEqual(
            self.on_site("content/exams/resit/assessment.yaml"),
            "title: Resit\nkind: final\nscope:\n  topics:\n    - gd\n",
        )
        page = self.client.get(f"{EXAMS}resit/")
        self.assertContains(page, "No papers yet")
        # A paper cannot be empty: it is made with its first question.
        url = f"{EXAMS}resit/papers/new/"
        paper = {
            "address": "mock-1", "title": "Mock final", "kind": "mock", "duration": "90",
            "marking-negative": "0.25",
            "first-id": "fin-m1", "first-type": "tf", "first-q": "GD always converges.",
            "first-correct": "false", "first-solution": "Not with too large a step.",
        }  # fmt: skip
        answer = self.client.post(url, paper)
        self.assertRedirects(
            answer, f"{EXAMS}resit/papers/mock-1/?saved=new", fetch_redirect_response=False
        )
        made = QuestionSet.objects.get(assessment__slug="resit", paper_slug="mock-1")
        self.assertEqual((made.kind, made.duration_min, made.negative_marking), ("mock", 90, 0.25))
        self.assertEqual(self.client.get(f"{EXAMS}resit/").context["rows"][0]["count"], 1)
        # Its questions are edited like those of a problem set.
        url = f"{EXAMS}resit/papers/mock-1/new/"
        question = {
            "id": "fin-m2",
            "type": "open",
            "q": "State the update rule.",
            "answer": "$w - \\eta g$",
        }
        self.assertEqual(self.client.post(url, question).status_code, 302)
        self.assertEqual(Question.objects.get(key="fin-m2").question_set, made)
        url = f"{EXAMS}resit/papers/mock-1/settings/"
        self.assertEqual(
            self.client.post(url, self.form(url) | {"duration": "120"}).status_code, 302
        )
        self.assertEqual(QuestionSet.objects.get(pk=made.pk).duration_min, 120)
        # The exam goes with everything in it, and says which ids that is.
        url = f"{EXAMS}resit/remove/"
        asked = self.client.get(url)
        self.assertContains(asked, "<code>fin-m1</code>")
        answer = self.client.post(url, {"seen": asked.context["seen"]})
        self.assertRedirects(answer, f"{EXAMS}?removed=1", fetch_redirect_response=False)
        self.assertFalse(Assessment.objects.filter(slug="resit").exists())
        self.assertFalse(Question.objects.filter(key__startswith="fin-").exists())
        self.assertFalse((self.on_site_folder() / "content/exams/resit").exists())

    def on_site_folder(self):
        from ingest import live

        return live.folder(C)

    def test_an_address_must_be_free_and_plain(self):
        for address, message in [
            ("midterm", "already"),
            ("Bad One", "lowercase"),
            ("new", "already"),
        ]:
            with self.subTest(address=address):
                data = {"address": address, "title": "T", "kind": "quiz"}
                self.assertContains(self.client.post(f"{EXAMS}new/", data), message)
        data = {
            "address": "past-2025",
            "title": "T",
            "kind": "past",
            "first-id": "x-1",
            "first-q": "Q",
            "first-answer": "A",
        }
        self.assertContains(self.client.post(f"{EXAMS}midterm/papers/new/", data), "already")
        self.assertFalse(HandIn.objects.exists())

    def test_a_paper_belongs_to_an_exam_that_exists(self):
        for url in (
            f"{EXAMS}nope/",
            f"{EXAMS}nope/about/",
            f"{EXAMS}nope/papers/",
            f"{EXAMS}nope/papers/new/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        page = self.client.get(f"{EXAMS}midterm/")
        self.assertEqual([row["name"] for row in page.context["rows"]], ["mock-1", "past-2025"])
        self.assertContains(self.client.get(EXAMS), "Midterm")


class PalaceTests(WholeCase):
    URL = f"{EDIT}palaces/gd-loop/"

    def test_a_palace_reads_back_as_it_is(self):
        answer = self.client.post(self.URL, self.form(self.URL))
        self.assertRedirects(answer, f"{self.URL}?saved=same", fetch_redirect_response=False)
        self.assertFalse(HandIn.objects.exists())
        self.assertContains(self.client.get(f"{self.URL}?saved=same"), "Nothing was changed")

    def test_a_stop_is_changed_added_and_moved(self):
        before = self.read(PALACE)
        data = self.form(self.URL)
        stops = len({name.split("-")[1] for name in data if name.startswith("loci-")})
        data["loci-0-image"] = "The door is gone. Only the note is left, $w$ dripping."
        new = {
            "loci-9-id": "office",
            "loci-9-place": "The office",
            "loci-9-image": "A crowd.",
            "loci-9-means": "**Repeat.**",
        }
        self.assertEqual(self.client.post(self.URL, data | new).status_code, 302)
        palace = Palace.objects.get(course__slug=C, slug="gd-loop")
        self.assertEqual(palace.loci.count(), stops + 1)
        after = self.on_site(PALACE)
        self.assertEqual(after.split("\nloci:")[0], before.split("\nloci:")[0])  # only the stops
        self.assertTrue(after.rstrip().endswith("means: '**Repeat.**'"))
        # Sent with the new stop first, it is first.
        data = self.form(self.URL)
        last = {name: value for name, value in data.items() if name.startswith(f"loci-{stops}-")}
        rest = {name: value for name, value in data.items() if name not in last}
        head = {name: value for name, value in rest.items() if not name.startswith("loci-")}
        tail = {name: value for name, value in rest.items() if name.startswith("loci-")}
        self.assertEqual(self.client.post(self.URL, head | last | tail).status_code, 302)
        self.assertEqual(Palace.objects.get(slug="gd-loop").loci.first().key, "office")

    def test_a_new_palace_needs_two_stops(self):
        url = f"{EDIT}palaces/new/"
        data = {
            "address": "the-fit", "title": "The fit", "loci-0-id": "a", "loci-0-place": "Door",
            "loci-0-image": "A line.", "loci-0-means": "The model.",
        }  # fmt: skip
        answer = self.client.post(url, data)
        fields = {f.node.key: f for f in answer.context["sections"][0]["fields"]}
        self.assertTrue(fields["loci"].errors)
        self.assertEqual(fields["loci"].rows[0][1].value, "Door")  # what was typed is there
        more = {
            "loci-1-id": "b",
            "loci-1-place": "Hall",
            "loci-1-image": "Dots.",
            "loci-1-means": "The data.",
        }
        answer = self.client.post(url, data | more)
        self.assertRedirects(
            answer, f"{EDIT}palaces/the-fit/?saved=new", fetch_redirect_response=False
        )
        self.assertEqual(Palace.objects.get(slug="the-fit").loci.count(), 2)
        self.assertContains(self.client.get(f"{EDIT}palaces/"), "The fit")
        url = f"{EDIT}palaces/the-fit/remove/"
        answer = self.client.post(url, {"seen": self.client.get(url).context["seen"]})
        self.assertRedirects(answer, f"{EDIT}palaces/?removed=1", fetch_redirect_response=False)
        self.assertFalse(Palace.objects.filter(slug="the-fit").exists())


class CourseFileTests(WholeCase):
    ABOUT = f"{EDIT}about/"
    SETTINGS = f"{EDIT}settings/"

    def test_the_description_and_topics_are_the_educators(self):
        before = self.read("course.yaml")
        data = self.form(self.ABOUT)
        self.assertNotIn("title", data)
        self.assertEqual(self.client.post(self.ABOUT, data).status_code, 302)
        self.assertFalse(HandIn.objects.exists())  # untouched: nothing
        data["description"] = "Two lectures on fitting a line."
        answer = self.client.post(self.ABOUT, data)
        self.assertRedirects(answer, f"{EDIT}?saved=about", fetch_redirect_response=False)
        after = self.on_site("course.yaml")
        self.assertIn("description: Two lectures on fitting a line.\n", after)
        self.assertEqual(after.split("description:")[0], before.split("description:")[0])
        self.assertEqual(after.split("\ntopics:")[1], before.split("\ntopics:")[1])
        self.assertEqual(Course.objects.get(slug=C).description, "Two lectures on fitting a line.")

    def test_a_topic_is_added_but_an_id_stays(self):
        data = self.form(self.ABOUT)
        new = {"topics-9-id": "reg", "topics-9-title": "Regularisation", "topics-9-short": "Reg"}
        self.assertEqual(self.client.post(self.ABOUT, data | new).status_code, 302)
        self.assertEqual(Topic.objects.get(course__slug=C, key="reg").title, "Regularisation")
        data = self.form(self.ABOUT) | {"topics-0-id": "renamed"}
        answer = self.client.post(self.ABOUT, data)
        self.assertContains(answer, "only the owner of the site may change")
        self.assertTrue(Topic.objects.filter(course__slug=C, key="lsq").exists())

    def test_the_rest_is_the_owners(self):
        self.assertEqual(self.client.get(self.SETTINGS).status_code, 404)
        self.assertEqual(self.client.post(self.SETTINGS, {"title": "Mine"}).status_code, 404)
        self.assertNotContains(self.client.get(EDIT), self.SETTINGS)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(EDIT), self.SETTINGS)
        data = self.form(self.SETTINGS)
        self.assertEqual((data["title"], data["hue"], data["semester"]), (TITLE, 200, 1))
        self.assertEqual(data["has_educator"], "yes")
        self.assertEqual(self.client.post(self.SETTINGS, data).status_code, 302)
        self.assertFalse(HandIn.objects.exists())
        before = self.read("course.yaml")
        answer = self.client.post(self.SETTINGS, data | {"hue": "120", "title": "Example 102"})
        self.assertRedirects(answer, f"{EDIT}?saved=settings", fetch_redirect_response=False)
        course = Course.objects.get(slug=C)
        self.assertEqual((course.title, course.hue), ("Example 102", 120))
        after = self.on_site("course.yaml")
        self.assertEqual(after.split("\ntopics:")[1], before.split("\ntopics:")[1])
        self.assertIn(
            "has_educator: true   # someone writes it", after
        )  # untouched lines keep theirs
        answer = self.client.post(self.SETTINGS, self.form(self.SETTINGS) | {"hue": "999"})
        self.assertTrue(
            {f.node.key: f for f in answer.context["sections"][0]["fields"]}["hue"].errors
        )

    def test_to_anyone_else_none_of_it_exists(self):
        self.client.force_login(self.eve)
        for url in (
            self.ABOUT,
            self.SETTINGS,
            EXAMS,
            f"{EXAMS}midterm/",
            f"{EXAMS}midterm/about/",
            f"{EXAMS}midterm/papers/mock-1/",
            f"{EDIT}palaces/",
            f"{EDIT}palaces/gd-loop/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)


class OfferedTests(WholeCase):
    """With editing switched on, each page of the course offers what it shows."""

    PAGES = {
        "": "about/",
        "cards/": "cards/",
        "cards/least-squares/": "cards/least-squares/",
        "problems/": "problems/",
        "problems/fitting-practice/": "problems/fitting-practice/",
        "exams/": "exams/",
        "exams/midterm/": "exams/midterm/",
        "exams/midterm/mock-1/": "exams/midterm/papers/mock-1/",
        "formulas/": "formulas/",
        "glossary/": "glossary/",
        "memory/": "palaces/",
        "memory/palaces/gd-loop/": "palaces/gd-loop/",
    }

    def test_every_page_leads_to_its_form(self):
        self.client.post(f"{EDIT}switch/", {"to": "on"})
        for page, form in self.PAGES.items():
            with self.subTest(page=page):
                answer = self.client.get(f"/courses/{C}/{page}")
                self.assertContains(answer, f'href="{EDIT}{form}"')
                self.assertEqual(self.client.get(f"{EDIT}{form}").status_code, 200)
        self.assertContains(self.client.get(f"/courses/{C}/memory/"), f'href="{EDIT}hooks/"')
        # All cards of the course at once is no deck: nothing of its own to offer.
        self.assertEqual(self.client.get(f"/courses/{C}/cards/review/").status_code, 200)

    def test_not_to_someone_who_does_not_write_the_course(self):
        self.client.force_login(self.eve)
        for page in self.PAGES:
            with self.subTest(page=page):
                answer = self.client.get(f"/courses/{C}/{page}")
                self.assertEqual(answer.status_code, 200)
                self.assertNotContains(answer, "/edit/")

    def test_the_editors_front_page_lists_every_kind(self):
        page = self.client.get(EDIT)
        for form in (
            "cards/",
            "problems/",
            "exams/",
            "formulas/",
            "glossary/",
            "hooks/",
            "palaces/",
        ):
            with self.subTest(form=form):
                self.assertContains(page, f'href="{EDIT}{form}"')
        self.assertContains(page, f'href="{EDIT}about/"')
