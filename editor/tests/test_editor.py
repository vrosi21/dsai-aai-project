"""Editing a course on the site (the editor app). The example course is the course being
edited; what an edit may and may not do to it is tested in desk/tests/test_handins.py."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import SimpleTestCase

from catalog.models import Card, Course, Document
from desk import handins
from desk.models import CourseSetting, Educator, HandIn
from editor import source
from editor.forms import suggest_name
from ingest import live
from ingest.tests.base import CourseTestCase

User = get_user_model()
C = "example"
EDIT = f"/edit/{C}/"
LECTURE = "content/lectures/01-least-squares.md"
GUIDE = "content/guides/choosing-a-method.md"
SHEET = "content/reference/cheatsheet.md"
DECK = "content/decks/least-squares.yaml"
TITLE = "Example 101: Fitting a Line"


class ComposeTests(SimpleTestCase):
    """What is written when a page is saved: only what changed changes."""

    FILE = "---\n# kept by its author\ntitle: T\ntopics: [b, a]\n---\n\n## One\n\ntext\n"

    def page(self):
        return source.Page("content/guides/t.md", self.FILE, {"title": "T", "topics": ["b", "a"]})

    def test_nothing_changed_is_the_same_file(self):
        meta = {"title": "T", "topics": ["b", "a"]}
        self.assertEqual(source.compose(self.page(), meta, "## One\r\n\r\ntext\r\n"), self.FILE)

    def test_a_new_text_keeps_the_front_matter_as_it_was_written(self):
        text = source.compose(self.page(), {"title": "T", "topics": ["b", "a"]}, "## Two\n")
        self.assertEqual(
            text, "---\n# kept by its author\ntitle: T\ntopics: [b, a]\n---\n\n## Two\n"
        )

    def test_new_front_matter_keeps_the_text_as_it_was(self):
        text = source.compose(self.page(), {"title": "New", "topics": ["b", "a"]}, "## One\n\ntext")
        self.assertEqual(text, "---\ntitle: New\ntopics:\n  - b\n  - a\n---\n\n## One\n\ntext\n")

    def test_a_page_that_was_not_there_is_written_whole(self):
        text = source.compose(source.Page("content/guides/n.md"), {"title": "N"}, "Hello $x$.")
        self.assertEqual(text, "---\ntitle: N\n---\n\nHello $x$.\n")

    def test_an_address_is_made_from_the_title_and_the_number(self):
        self.assertEqual(suggest_name("Fisher's criterion & PCA", 5), "05-fishers-criterion-pca")
        self.assertEqual(suggest_name("A  second -- look"), "a-second-look")
        self.assertEqual(suggest_name("???"), "")


class EditorCase(CourseTestCase):
    def setUp(self):
        super().setUp()
        caches["limits"].clear()
        self.assertEqual(self.sync().status, "synced")
        self.ada = User.objects.create_user("ada", email="ada@example.test", first_name="Ada")
        self.bob = User.objects.create_user("bob", email="bob@example.test", first_name="Bob")
        self.eve = User.objects.create_user("eve", email="eve@example.test")
        self.admin = User.objects.create_superuser("root", email="root@example.test")
        for person in (self.ada, self.bob):
            Educator.objects.create(user=person, series=C)
        self.client.force_login(self.ada)

    def on_site(self, name):
        return (live.folder(C) / name).read_text("utf-8")

    def form_of(self, url):
        """The values a browser would send back if the form at `url` were submitted
        untouched: what the form was filled with."""
        form = self.client.get(url).context["form"]
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if value not in (None, "", []):
                data[name] = value
        return data


class AccessTests(EditorCase):
    URLS = [
        EDIT,
        f"{EDIT}lectures/new/",
        f"{EDIT}lectures/01-least-squares/",
        f"{EDIT}lectures/01-least-squares/delete/",
        f"{EDIT}cheatsheet/",
        f"{EDIT}files/",
        f"{EDIT}file/?name=course.yaml",
    ]

    def test_the_people_who_write_the_course_and_staff_get_in(self):
        for person in (self.ada, self.admin):
            self.client.force_login(person)
            for url in self.URLS:
                with self.subTest(person=person.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_to_anyone_else_the_editor_does_not_exist(self):
        self.client.force_login(self.eve)
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"{EDIT}preview/", {"file": LECTURE}).status_code, 404)
        self.assertEqual(self.client.post(f"{EDIT}switch/", {"to": "on"}).status_code, 404)
        answer = self.client.post(f"{EDIT}lectures/01-least-squares/", {"title": "Hers now"})
        self.assertEqual(answer.status_code, 404)
        self.assertFalse(HandIn.objects.exists())

    def test_signed_out_it_asks_to_sign_in(self):
        self.client.logout()
        answer = self.client.get(EDIT)
        self.assertEqual(answer.status_code, 302)
        self.assertNotIn("/edit/", answer["Location"].split("?")[0])

    def test_an_educator_of_another_course_does_not_get_in(self):
        other = User.objects.create_user("zed", email="zed@example.test")
        Educator.objects.create(user=other, series="another-course")
        self.client.force_login(other)
        self.assertEqual(self.client.get(EDIT).status_code, 404)


class SwitchTests(EditorCase):
    PAGE = f"/courses/{C}/lectures/01-least-squares/"

    def test_a_course_page_offers_editing_to_those_who_write_it_only(self):
        page = self.client.get(self.PAGE)
        self.assertContains(page, f'action="{EDIT}switch/"')
        self.assertNotContains(page, "Edit this page")  # not until it is switched on
        self.client.force_login(self.eve)
        self.assertNotContains(self.client.get(self.PAGE), "switch/")

    def test_switched_on_each_page_says_what_it_offers(self):
        answer = self.client.post(f"{EDIT}switch/", {"to": "on", "next": self.PAGE})
        self.assertRedirects(answer, self.PAGE, fetch_redirect_response=False)
        self.assertContains(self.client.get(self.PAGE), f'href="{EDIT}lectures/01-least-squares/"')
        self.assertContains(self.client.get(f"/courses/{C}/guides/"), f'href="{EDIT}guides/new/"')
        self.assertContains(
            self.client.get(f"/courses/{C}/cheatsheet/"), f'href="{EDIT}cheatsheet/"'
        )
        self.assertContains(self.client.get(f"/courses/{C}/cards/"), f'href="{EDIT}"')
        self.client.post(f"{EDIT}switch/", {"to": "off", "next": self.PAGE})
        self.assertNotContains(self.client.get(self.PAGE), "Edit this page")

    def test_it_only_leads_back_to_this_site(self):
        answer = self.client.post(f"{EDIT}switch/", {"to": "on", "next": "https://evil.example/"})
        self.assertRedirects(answer, f"/courses/{C}/", fetch_redirect_response=False)

    def test_opening_the_editor_switches_editing_on(self):
        self.client.get(EDIT)
        self.assertContains(self.client.get(self.PAGE), "Edit this page")


class PageTests(EditorCase):
    URL = f"{EDIT}lectures/01-least-squares/"

    def test_the_form_is_filled_from_the_file(self):
        page = self.client.get(self.URL)
        form = page.context["form"]
        self.assertEqual(form.initial["title"], "Least squares")
        self.assertEqual((form.initial["number"], form.initial["topics"]), (1, ["lsq"]))
        self.assertEqual(len(form.initial["takeaways"]), 3)
        self.assertTrue(form.initial["body"].startswith("## The model"))
        self.assertNotIn("name", form.fields)  # an address is permanent
        self.assertNotIn("due", form.fields)  # a lecture has no deadline
        self.assertContains(page, "X^\\top X\\, w = X^\\top y")  # LaTeX reaches the box whole
        self.assertContains(page, 'name="takeaways"', count=4)  # three, and the blank row

    def test_saving_it_untouched_saves_nothing(self):
        answer = self.client.post(self.URL, self.form_of(self.URL))
        self.assertRedirects(answer, f"{self.URL}?saved=same", fetch_redirect_response=False)
        self.assertFalse(HandIn.objects.exists())
        self.assertContains(self.client.get(f"{self.URL}?saved=same"), "Nothing was changed")

    def test_a_change_to_the_text_is_live_and_leaves_the_rest_of_the_file_alone(self):
        before = self.read(LECTURE)
        data = self.form_of(self.URL)
        data["body"] = data["body"].replace("## The model", "## The model, again") + "\r\n\r\nNew."
        answer = self.client.post(self.URL, data)
        self.assertRedirects(answer, f"{self.URL}?saved=1", fetch_redirect_response=False)
        after = self.on_site(LECTURE)
        head = before.split("\n---\n")[0]
        self.assertTrue(after.startswith(head + "\n---\n"), msg=after[:400])
        self.assertIn("topics: [lsq]", after)  # as its author wrote it
        self.assertTrue(after.endswith("\n\nNew.\n"))
        self.assertNotIn("\r", after)
        step = HandIn.objects.get()
        self.assertEqual((step.via, step.user, list(step.shrunk)), ("site", self.ada, [LECTURE]))
        self.assertEqual(self.read(LECTURE), before)  # the owner's file is not touched
        self.assertContains(self.client.get(f"/courses/{C}/lectures/01-least-squares/"), "again")
        self.assertContains(self.client.get(f"{self.URL}?saved=1"), "Saved. It is on the site now.")

    def test_a_change_to_a_field_rewrites_the_front_matter_and_keeps_the_text(self):
        data = self.form_of(self.URL)
        data.update(title="Least squares, revisited", status="draft", topics=["lsq", "gd"])
        data["takeaways"] = [*data["takeaways"][:2], "A new one, with $x^2$."]
        self.assertEqual(self.client.post(self.URL, {**data, "view": "1"}).status_code, 302)
        after = self.on_site(LECTURE)
        self.assertIn("title: Least squares, revisited\nnumber: 1\ndate: 2026-10-05\n", after)
        self.assertIn("topics:\n  - lsq\n  - gd\n", after)
        self.assertIn("  - A new one, with $x^2$.\nstatus: draft\n---\n", after)
        # Maths with a backslash is written where a backslash is a backslash.
        self.assertIn(
            "  - |-\n    The loss is the sum of squared residuals, $L(w) = \\lVert", after
        )
        self.assertEqual(after.split("\n---\n", 1)[1], self.read(LECTURE).split("\n---\n", 1)[1])
        doc = Document.objects.get(course__slug=C, slug="01-least-squares")
        self.assertEqual((doc.title, doc.status), ("Least squares, revisited", "draft"))
        self.assertEqual(len(doc.takeaways), 3)

    def test_save_and_view_leads_to_the_page(self):
        data = {**self.form_of(self.URL), "summary": "Shorter.", "view": "1"}
        answer = self.client.post(self.URL, data)
        self.assertRedirects(
            answer, f"/courses/{C}/lectures/01-least-squares/", fetch_redirect_response=False
        )

    def test_what_the_site_would_not_show_is_not_saved_and_stays_in_the_form(self):
        data = self.form_of(self.URL)
        data["body"] += "\n\nA <b>bold</b> claim, and [a card](card:no-such-card).\n"
        answer = self.client.post(self.URL, data)
        self.assertContains(answer, "Not saved: the site&#x27;s check found errors.")
        self.assertContains(answer, "raw HTML")
        self.assertContains(answer, "no card named `no-such-card`")
        self.assertContains(answer, "A &lt;b&gt;bold&lt;/b&gt; claim")  # still in the box
        self.assertFalse(HandIn.objects.exists())
        self.assertFalse((handins.root() / C).exists())

    def test_a_field_is_checked_by_the_rules_of_the_course_files(self):
        data = {**self.form_of(self.URL), "title": "", "number": "1000"}
        answer = self.client.post(self.URL, data)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(set(answer.context["form"].errors), {"title", "number"})
        self.assertFalse(HandIn.objects.exists())

    def test_someone_else_changed_it_meanwhile(self):
        data = self.form_of(self.URL)  # Ada opens the page
        bobs = self.client_class()
        bobs.force_login(self.bob)
        theirs = self.form_of(self.URL) | {"summary": "Bob was here."}
        self.assertEqual(bobs.post(self.URL, theirs).status_code, 302)
        answer = self.client.post(self.URL, data | {"summary": "Ada was here."})
        self.assertContains(answer, "changed on the site while you were editing")
        self.assertContains(answer, "Ada was here.")  # what she typed is not lost
        self.assertIn("Bob was here.", self.on_site(LECTURE))
        self.assertEqual(HandIn.objects.count(), 1)
        # She has seen it and saves hers over it; his version stays in the history.
        now = answer.context["refused"]["now"]
        answer = self.client.post(self.URL, data | {"summary": "Ada was here.", "over": now})
        self.assertEqual(answer.status_code, 302)
        self.assertIn("Ada was here.", self.on_site(LECTURE))
        self.assertEqual(HandIn.objects.count(), 2)
        kept = handins.files_at(C, HandIn.objects.order_by("pk").first().version)
        self.assertIn(b"Bob was here.", kept[LECTURE])

    def test_what_a_kind_is_not_asked_for_is_kept(self):
        """The cheat sheet's form has no topics; a file that names some keeps them."""
        self.edit(SHEET, "title: Cheat sheet\n", "title: Cheat sheet\ntopics: [lsq]\n")
        self.assertEqual(self.sync().status, "synced")
        url = f"{EDIT}cheatsheet/"
        self.assertNotIn("topics", self.client.get(url).context["form"].fields)
        self.client.post(url, {**self.form_of(url), "title": "On one page"})
        self.assertIn("title: On one page\ntopics:\n  - lsq\n", self.on_site(SHEET))


class NewAndRemoveTests(EditorCase):
    NEW = f"{EDIT}guides/new/"
    BODY = "## Why\n\nBecause $a \\le b$.\n\n:::tip\nTry it.\n:::\n"

    def test_a_new_guide_is_written_and_is_on_the_site(self):
        page = self.client.get(self.NEW)
        self.assertContains(page, "Write a new guide")
        self.assertIn("name", page.context["form"].fields)
        data = {"title": "A second look", "topics": ["gd"], "status": "complete", "body": self.BODY}
        answer = self.client.post(self.NEW, data)
        self.assertRedirects(
            answer, f"{EDIT}guides/a-second-look/?saved=1", fetch_redirect_response=False
        )
        self.assertEqual(
            self.on_site("content/guides/a-second-look.md"),
            "---\ntitle: A second look\ntopics:\n  - gd\n---\n\n" + self.BODY,
        )
        self.assertFalse((self.course / "content/guides/a-second-look.md").exists())
        doc = Document.objects.get(course__slug=C, slug="a-second-look")
        self.assertIn("callout", doc.body_html)
        self.assertEqual(HandIn.objects.get().new_files, ["content/guides/a-second-look.md"])
        self.assertContains(self.client.get(EDIT), "A second look")

    def test_a_lecture_takes_its_address_from_its_number_and_title(self):
        data = {"title": "Regularisation", "number": "3", "status": "draft", "body": "Soon."}
        answer = self.client.post(f"{EDIT}lectures/new/", data)
        self.assertRedirects(
            answer, f"{EDIT}lectures/03-regularisation/?saved=1", fetch_redirect_response=False
        )
        doc = Document.objects.get(course__slug=C, slug="03-regularisation")
        self.assertEqual((doc.number, doc.status), (3, "draft"))

    def test_an_address_must_be_free_and_plain(self):
        for name, message in [
            ("choosing-a-method", "exists already"),
            ("new", "exists already"),
            ("Has Spaces", "lowercase letters"),
            ("../../etc", "lowercase letters"),
        ]:
            with self.subTest(name=name):
                data = {"title": "T", "name": name, "status": "complete", "body": "x"}
                answer = self.client.post(self.NEW, data)
                self.assertContains(answer, message)
        self.assertFalse(HandIn.objects.exists())

    def test_a_page_is_removed_only_after_asking(self):
        data = {"title": "A second look", "status": "complete", "body": self.BODY}
        self.client.post(self.NEW, data)
        url = f"{EDIT}guides/a-second-look/delete/"
        asked = self.client.get(url)
        self.assertContains(asked, "Remove the page")
        self.assertTrue(
            Document.objects.filter(slug="a-second-look").exists()
        )  # a GET removes nothing
        answer = self.client.post(url, {"seen": asked.context["page"].seen})
        self.assertRedirects(answer, f"{EDIT}?deleted=1", fetch_redirect_response=False)
        self.assertFalse(Document.objects.filter(slug="a-second-look").exists())
        self.assertFalse((live.folder(C) / "content/guides/a-second-look.md").exists())
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_page_something_links_to_is_not_removed(self):
        url = f"{EDIT}lectures/01-least-squares/delete/"
        seen = self.client.get(url).context["page"].seen
        answer = self.client.post(url, {"seen": seen})
        self.assertContains(answer, "still depends on this page")
        self.assertContains(answer, "01-least-squares")
        self.assertTrue(Document.objects.filter(slug="01-least-squares").exists())
        self.assertFalse(HandIn.objects.exists())

    def test_a_course_without_a_cheat_sheet_can_be_given_one(self):
        (self.course / SHEET).unlink()
        self.assertEqual(self.sync().status, "synced")
        url = f"{EDIT}cheatsheet/"
        self.assertContains(self.client.get(url), "Write the cheat sheet")
        data = {"title": "On one page", "body": "## All of it\n\n$a = b$\n", "view": "1"}
        answer = self.client.post(url, data)
        self.assertRedirects(answer, f"/courses/{C}/cheatsheet/", fetch_redirect_response=False)
        self.assertContains(self.client.get(f"/courses/{C}/cheatsheet/"), "All of it")


class FileTests(EditorCase):
    URL = f"{EDIT}file/?name="

    def test_any_text_file_opens_as_it_is(self):
        page = self.client.get(self.URL + DECK)
        self.assertEqual(page.context["form"].initial["text"], self.read(DECK))
        listing = self.client.get(f"{EDIT}files/")
        self.assertContains(listing, f"{EDIT}file/?name=content/decks/least-squares.yaml")
        self.assertContains(listing, "residuals.svg")
        self.assertNotContains(listing, "name=content/lectures/assets/residuals.svg")

    def test_only_text_files_of_the_course(self):
        for name in (
            "content/lectures/assets/residuals.svg",
            "content/decks/nope.yaml",
            "../../manage.py",
            "/etc/passwd",
            "source/slides.md",
            "",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.URL + name).status_code, 404)

    def test_a_card_is_changed_as_text(self):
        url = self.URL + DECK
        text = self.read(DECK).replace("\n", "\r\n")  # as a browser sends a text box
        seen = self.client.get(url).context["form"].initial["seen"]
        changed = text + "  - id: lsq-extra\r\n    q: One more?\r\n    a: Yes.\r\n"
        answer = self.client.post(url, {"text": changed, "seen": seen})
        self.assertRedirects(answer, f"{url}&saved=1", fetch_redirect_response=False)
        self.assertTrue(Card.objects.filter(key="lsq-extra").exists())
        self.assertNotIn("\r", self.on_site(DECK))
        self.assertEqual(HandIn.objects.get().grown, {DECK: 3})
        # Sent again unchanged, it is nothing.
        seen = self.client.get(url).context["form"].initial["seen"]
        answer = self.client.post(url, {"text": changed, "seen": seen})
        self.assertRedirects(answer, f"{url}&saved=same", fetch_redirect_response=False)

    def test_broken_yaml_is_not_saved(self):
        url = self.URL + DECK
        seen = self.client.get(url).context["form"].initial["seen"]
        answer = self.client.post(url, {"text": "title: [unclosed\n", "seen": seen})
        self.assertContains(answer, "Not saved: the site&#x27;s check found errors.")
        self.assertContains(answer, "title: [unclosed")
        self.assertFalse(HandIn.objects.exists())

    def test_what_is_the_owners_in_course_yaml_is_the_owners(self):
        url = self.URL + "course.yaml"
        seen = self.client.get(url).context["form"].initial["seen"]
        renamed = self.read("course.yaml").replace(f'title: "{TITLE}"', "title: Renamed")
        answer = self.client.post(url, {"text": renamed, "seen": seen})
        self.assertContains(answer, "only the owner of the site may change")
        self.assertEqual(Course.objects.get(slug=C).title, TITLE)
        described = self.read("course.yaml").replace(
            "description: Least", "description: Mostly least"
        )
        self.assertEqual(self.client.post(url, {"text": described, "seen": seen}).status_code, 302)
        self.assertTrue(Course.objects.get(slug=C).description.startswith("Mostly least"))
        # Whoever runs the site may change the rest as well.
        self.client.force_login(self.admin)
        seen = self.client.get(url).context["form"].initial["seen"]
        renamed = self.on_site("course.yaml").replace(f'title: "{TITLE}"', "title: Renamed")
        self.assertEqual(self.client.post(url, {"text": renamed, "seen": seen}).status_code, 302)
        self.assertEqual(Course.objects.get(slug=C).title, "Renamed")


class PreviewTests(EditorCase):
    URL = f"{EDIT}preview/"

    def test_it_is_what_the_site_would_show(self):
        body = "## Why\n\nSee [](card:lsq-loss) and $a \\le b$.\n\n:::tip Try\nIt.\n:::\n"
        answer = self.client.post(
            self.URL, {"file": LECTURE, "body": body, "takeaways": ["One $x$.", ""]}
        )
        shown = answer.json()
        self.assertIn('<h2 id="why">Why</h2>', shown["html"])
        self.assertIn(f'href="/courses/{C}/cards/least-squares/', shown["html"])
        self.assertIn('<span class="math inline">a \\le b</span>', shown["html"])
        self.assertIn("callout--tip", shown["html"])
        self.assertEqual(shown["takeaways"], ['One <span class="math inline">x</span>.'])
        self.assertEqual(shown["problems"], [])
        self.assertFalse(HandIn.objects.exists())

    def test_it_says_what_is_wrong_and_on_which_line(self):
        body = "# Too big\n\nfine\n\nA <i>tag</i> and [](lecture:99-nope).\n"
        shown = self.client.post(self.URL, {"file": LECTURE, "body": body}).json()
        found = {(problem["line"], problem["message"][:22]) for problem in shown["problems"]}
        self.assertIn((1, "a `#` heading: the pag"), found)
        self.assertIn((5, "raw HTML (<i>) is show"), found)
        self.assertIn((5, "no lecture named `99-n"), found)
        self.assertIn("&lt;i&gt;", shown["html"])  # shown as text, never as markup

    def test_an_image_of_the_course_keeps_its_address(self):
        body = "![The residuals](assets/residuals.svg)\n"
        shown = self.client.post(self.URL, {"file": LECTURE, "body": body}).json()
        self.assertRegex(shown["html"], r'src="/media/courses/example/[0-9a-f]{16}\.svg"')
        self.assertEqual(shown["problems"], [])

    def test_it_is_asked_with_a_post_about_a_page_of_the_course(self):
        self.assertEqual(self.client.get(self.URL).status_code, 405)
        for name in ("", "course.yaml", "../x.md", DECK):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(self.URL, {"file": name}).status_code, 404)

    def test_links_follow_the_course_as_it_changes(self):
        """What can be linked to is kept between previews, and looked up again once the
        course is another version."""
        body = "[](guide:a-second-look)\n"
        shown = self.client.post(self.URL, {"file": LECTURE, "body": body}).json()
        self.assertIn("no guide named", shown["problems"][0]["message"])
        data = {"title": "A second look", "status": "complete", "body": "Text."}
        self.client.post(f"{EDIT}guides/new/", data)
        shown = self.client.post(self.URL, {"file": LECTURE, "body": body}).json()
        self.assertEqual(shown["problems"], [])
        self.assertIn(">A second look</a>", shown["html"])


class OverviewTests(EditorCase):
    def test_it_lists_what_can_be_edited_and_what_was_changed(self):
        page = self.client.get(EDIT)
        self.assertContains(page, f'href="{EDIT}lectures/01-least-squares/"')
        self.assertContains(page, f'href="{EDIT}guides/new/"')
        self.assertContains(page, f'href="{EDIT}cheatsheet/"')
        self.assertContains(page, "Nothing has been changed on the site")
        url = f"{EDIT}guides/choosing-a-method/"
        self.client.post(url, {**self.form_of(url), "summary": "Shorter."})
        page = self.client.get(EDIT)
        self.assertContains(page, "edited on the site")
        self.assertContains(page, "· you ·")

    def test_the_educators_page_leads_here_and_says_how_a_step_came(self):
        url = f"{EDIT}guides/choosing-a-method/"
        self.client.post(url, {**self.form_of(url), "summary": "Shorter."})
        page = self.client.get(f"/educators/courses/{C}/")
        self.assertContains(page, f'href="{EDIT}"')
        self.assertContains(page, "edited on the site")
        self.assertContains(page, "Take it back")


class TellingTests(EditorCase):
    URL = f"{EDIT}guides/choosing-a-method/"

    def test_the_owner_is_told_once_per_step_and_never_who_or_what(self):
        with (
            mock.patch("editor.views.notify") as notify,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(self.URL, {**self.form_of(self.URL), "summary": "Shorter."})
            self.client.post(self.URL, {**self.form_of(self.URL), "summary": "Shorter still."})
        self.assertEqual(HandIn.objects.get().saves, 2)
        self.assertEqual(notify.call_count, 1)
        told = " ".join([*notify.call_args.args, *map(str, notify.call_args.kwargs.values())])
        self.assertIn(f"Edited on the site: {TITLE}", told)
        self.assertIn(f"/admin/desk/handin/{HandIn.objects.get().pk}/change/", told)
        for word in ("Ada", "ada@", "Shorter"):
            self.assertNotIn(word, told)

    def test_nobody_is_told_of_a_save_that_was_refused_or_made_by_the_owner(self):
        with (
            mock.patch("editor.views.notify") as notify,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(self.URL, {**self.form_of(self.URL), "body": "A <b>tag</b>."})
            self.client.force_login(self.admin)
            self.client.post(self.URL, {**self.form_of(self.URL), "summary": "The owner's."})
        self.assertEqual(HandIn.objects.get().user, self.admin)
        notify.assert_not_called()

    def test_the_admin_lists_how_each_step_came(self):
        self.client.post(self.URL, {**self.form_of(self.URL), "summary": "Shorter."})
        self.client.force_login(self.admin)
        page = self.client.get("/admin/desk/handin/")
        self.assertContains(page, "Edited on the site")
        step = HandIn.objects.get()
        self.assertContains(self.client.get(f"/admin/desk/handin/{step.pk}/change/"), GUIDE)


class ClosedCourseTests(EditorCase):
    """A course students cannot see yet is where most of the writing happens."""

    def setUp(self):
        super().setUp()
        CourseSetting.objects.create(series=C, enabled=False)

    def test_its_educators_see_it_and_can_edit_it(self):
        page = self.client.get(f"/courses/{C}/")
        self.assertContains(page, "because you write it")
        url = f"{EDIT}guides/choosing-a-method/"
        answer = self.client.post(url, {**self.form_of(url), "summary": "Shorter.", "view": "1"})
        self.assertEqual(answer.status_code, 302)
        self.assertContains(self.client.get(answer["Location"]), "Shorter.")

    def test_nobody_else_does(self):
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(f"/courses/{C}/").status_code, 404)
        self.assertEqual(self.client.get(EDIT).status_code, 404)


class LimitTests(EditorCase):
    def test_a_preview_is_of_a_text_not_of_a_book(self):
        long = "word " * 90_000
        answer = self.client.post(f"{EDIT}preview/", {"file": LECTURE, "body": long})
        self.assertEqual(answer.status_code, 413)
