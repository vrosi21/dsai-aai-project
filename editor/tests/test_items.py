"""The editor's forms for content kept in YAML files: cards, questions, formulas, terms
and hooks (editor/schema.py, editor/items.py, editor/shelves.py). The example course is
the course being edited."""

import datetime as dt

from django.http import QueryDict
from django.test import SimpleTestCase

from catalog.models import Card, Deck, Formula, Hook, Question, Term
from desk.models import HandIn
from editor import items, schema
from ingest import schemas

from .test_editor import DECK, EDIT, EditorCase

CARDS = f"{EDIT}cards/"
LSQ = f"{CARDS}least-squares/"
SET = "content/problems/fitting-practice.yaml"
FORMULAS = "content/reference/formulas.yaml"
GLOSSARY = "content/reference/glossary.yaml"
HOOKS = "content/memory/hooks.yaml"


def sent(fields):
    """What a browser would send back for these fields, untouched."""
    data = {}
    for item in fields:
        node = item.node
        if node.fixed:
            continue
        if node.kind == "group":
            data |= sent(item.children)
        elif node.kind == "list":
            for row in item.rows:
                data |= sent(row)
        elif node.kind == "flag":
            if item.value:
                data[item.name] = "yes"
        elif node.kind in schema.MANY:
            if item.value:
                data[item.name] = list(item.value)
        elif isinstance(item.value, dt.date):
            data[item.name] = item.value.isoformat()
        else:
            data[item.name] = item.shown
    return data


def post(**fields):
    """A submitted form: names to one value or to several."""
    query = QueryDict(mutable=True)
    for name, value in fields.items():
        query.setlist(name, value if isinstance(value, list) else [value])
    return query


class DescribeTests(SimpleTestCase):
    """A schema read as a form."""

    def kinds(self, model, **options):
        return {node.key: node.kind for node in schema.describe(model, **options)}

    def test_each_field_is_drawn_as_what_the_schema_says_it_is(self):
        card = self.kinds(schemas.CardSpec)
        self.assertEqual(
            card,
            {
                "id": "id", "shape": "choice", "q": "markdown", "a": "markdown",
                "note": "markdown", "hook": "markdown", "steps": "list", "cloze": "cloze",
                "hy": "flag", "topic": "topic",
            },
        )  # fmt: skip
        question = self.kinds(schemas.QuestionSpec)
        self.assertEqual((question["type"], question["correct"]), ("choice", "truth"))
        self.assertEqual((question["points"], question["choices"]), ("decimal", "list"))
        exam = {node.key: node for node in schema.describe(schemas.AssessmentFile)}
        self.assertEqual((exam["scope"].kind, exam["includes"].kind), ("group", "exams"))
        self.assertEqual([n.kind for n in exam["scope"].children], ["numbers", "topics"])
        self.assertEqual((exam["date"].kind, exam["kind"].required), ("date", True))
        phases = {n.key: n for n in exam["plan"].children}["phases"]
        tasks = {n.key: n for n in phases.children}["tasks"]
        self.assertEqual(
            (phases.entry, tasks.entry, tasks.children[-1].kind), ("phase", "task", "refs")
        )
        # A field the schema calls by another name in the file goes by that name.
        self.assertIn("for", self.kinds(schemas.HookSpec))
        self.assertEqual(self.kinds(schemas.TermSpec)["see"], "ids")

    def test_a_part_of_a_schema(self):
        deck = self.kinds(schemas.DeckFile, without=("cards",))
        self.assertEqual(list(deck), ["title", "description", "topic", "lecture"])
        self.assertEqual(deck["lecture"], "lecture")

    def test_what_the_course_has_is_offered(self):
        course = schema.Course(topics=[("lsq", "Least squares")], lectures=[("01-a", "A")])
        nodes = {n.key: n for n in schema.describe(schemas.DeckFile, course)}
        self.assertEqual(nodes["topic"].choices, [("lsq", "Least squares")])
        self.assertEqual(nodes["lecture"].choices, [("01-a", "A")])


class ParseTests(SimpleTestCase):
    """A submitted form read back into what would stand in the file."""

    CARD = schema.describe(schemas.CardSpec)
    QUESTION = schema.describe(schemas.QuestionSpec)

    def test_a_basic_card_and_what_is_left_out(self):
        data = schema.parse(
            self.CARD,
            post(id="c-1", shape="basic", q="Why?\r\n", a="Because $a \\le b$.", note="", topic="",
                 cloze="{{not}} read: the card is not that kind"),
        )  # fmt: skip
        self.assertEqual(data, {"id": "c-1", "q": "Why?", "a": "Because $a \\le b$."})
        self.assertEqual(schema.validate(schemas.CardSpec, data), {})
        data = schema.parse(self.CARD, post(id="c-1", shape="basic", q="Why?", a="So.", hy="yes"))
        self.assertIs(data["hy"], True)

    def test_the_kind_of_card_decides_which_fields_are_read(self):
        sent_ = post(
            **{"id": "c-2", "shape": "cloze", "q": "gone", "a": "gone", "cloze": "A {{blank}}.",
               "steps-0-do": "gone"}
        )  # fmt: skip
        self.assertEqual(schema.parse(self.CARD, sent_), {"id": "c-2", "cloze": "A {{blank}}."})

    def test_entries_of_a_list_are_read_in_the_order_they_stand_on_the_page(self):
        sent_ = post(
            **{"id": "c-3", "shape": "steps", "q": "Derive it.",
               "steps-7-do": "First", "steps-7-why": "It is given.",
               "steps-2-do": "Second", "steps-2-why": "",
               "steps-5-do": "", "steps-5-why": ""}
        )  # fmt: skip
        data = schema.parse(self.CARD, sent_)
        self.assertEqual(data["steps"], [{"do": "First", "why": "It is given."}, {"do": "Second"}])
        self.assertEqual(schema.validate(schemas.CardSpec, data), {})

    def test_a_question_with_choices_and_one_that_is_true_or_false(self):
        sent_ = post(
            **{"id": "q-1", "type": "mcq", "q": "Which?", "points": "2", "correct": "true",
               "choices-0-text": "This", "choices-0-correct": "yes", "choices-0-why": "It is.",
               "choices-1-text": "That"}
        )  # fmt: skip
        data = schema.parse(self.QUESTION, sent_)
        self.assertEqual(
            data,
            {
                "id": "q-1", "type": "mcq", "points": 2, "q": "Which?",
                "choices": [{"text": "This", "correct": True, "why": "It is."}, {"text": "That"}],
            },
        )  # fmt: skip
        self.assertEqual(schema.validate(schemas.QuestionSpec, data), {})
        # "False" is an answer, not an empty field.
        data = schema.parse(self.QUESTION, post(id="q-2", type="tf", q="So?", correct="false"))
        self.assertEqual(data, {"id": "q-2", "type": "tf", "q": "So?", "correct": False})
        # What is the default is not written.
        data = schema.parse(
            self.QUESTION, post(id="q-3", type="open", q="So?", answer="Yes.", points="2.5")
        )
        self.assertEqual(data, {"id": "q-3", "points": 2.5, "q": "So?", "answer": "Yes."})

    def test_what_is_wrong_is_said_where_it_is_and_in_the_importers_words(self):
        data = schema.parse(self.QUESTION, post(id="Bad Id", type="open", q="", points="many"))
        errors = schema.validate(schemas.QuestionSpec, data)
        self.assertEqual(set(errors), {("id",), ("q",), ("points",)})
        self.assertEqual(errors[("q",)], ["`q` is required"])
        data = schema.parse(
            self.CARD, post(**{"id": "c", "shape": "steps", "q": "Q", "steps-0-do": "One"})
        )
        errors = schema.validate(schemas.CardSpec, data)
        self.assertIn("at least 2 steps", schema.general(errors)[0])
        data = schema.parse(
            self.CARD,
            post(**{"id": "c", "shape": "steps", "q": "Q", "steps-0-why": "x", "steps-1-do": "y"}),
        )
        self.assertIn(("steps", 0, "do"), schema.validate(schemas.CardSpec, data))

    def test_groups_dates_and_things_to_tick(self):
        course = schema.Course(topics=[("lsq", "L"), ("gd", "G")], numbers=[(1, "1"), (2, "2")])
        nodes = schema.describe(schemas.AssessmentFile, course)
        sent_ = post(
            **{"title": "Midterm", "kind": "midterm", "date": "2026-11-20",
               "scope-lectures": ["1", "2"], "scope-topics": ["gd"],
               "plan-intro": "Two evenings.", "plan-phases-0-label": "Day 1",
               "plan-phases-0-title": "Read", "plan-phases-0-tasks-3-text": "The deck.",
               "plan-phases-0-tasks-3-links": ["deck:least-squares", ""],
               "weights-0-topic": "lsq", "weights-0-weight": "5"}
        )  # fmt: skip
        data = schema.parse(nodes, sent_)
        self.assertEqual(data["date"], dt.date(2026, 11, 20))
        self.assertEqual(data["scope"], {"lectures": [1, 2], "topics": ["gd"]})
        self.assertEqual(
            data["plan"]["phases"],
            [
                {
                    "label": "Day 1",
                    "title": "Read",
                    "tasks": [{"text": "The deck.", "links": ["deck:least-squares"]}],
                }
            ],
        )
        self.assertEqual(data["weights"], [{"topic": "lsq", "weight": 5}])
        self.assertEqual(schema.validate(schemas.AssessmentFile, data), {})

    def test_a_form_that_comes_back_is_drawn_as_it_was_sent(self):
        sent_ = post(
            **{"id": "c", "shape": "cloze", "cloze": "no blank here", "steps-4-do": "kept out"}
        )
        data = schema.parse(self.CARD, sent_)
        errors = schema.validate(schemas.CardSpec, data)
        fields = {
            f.node.key: f
            for f in schema.bind(self.CARD, data, errors, chosen=schema.picked(self.CARD, sent_))
        }
        self.assertEqual((fields["shape"].value, fields["shape"].steers), ("cloze", True))
        self.assertEqual(fields["cloze"].value, "no blank here")
        self.assertIn("no {{blank}}", schema.general(errors)[0])
        # An item that exists says its own kind.
        fields = {
            f.node.key: f
            for f in schema.bind(
                self.CARD, {"id": "c", "q": "Q", "steps": [{"do": "a"}, {"do": "b"}]}
            )
        }
        self.assertEqual((fields["shape"].value, len(fields["steps"].rows)), ("steps", 2))
        self.assertEqual(fields["steps"].rows[1][0].name, "steps-1-do")
        self.assertEqual(fields["steps"].blank[0].name, "steps-__0__-do")


class CardTests(EditorCase):
    URL = f"{LSQ}lsq-normal/"

    def form(self, url):
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        fields = [f for section in page.context["sections"] for f in section["fields"]]
        return {**sent(fields), "seen": page.context["seen"]}

    def test_the_form_is_filled_from_the_file_and_the_id_is_not_to_change(self):
        page = self.client.get(self.URL)
        fields = {f.node.key: f for f in page.context["sections"][0]["fields"]}
        self.assertEqual((fields["id"].value, fields["id"].node.fixed), ("lsq-normal", True))
        self.assertEqual((fields["shape"].value, fields["hy"].value), ("basic", True))
        self.assertEqual(fields["a"].value, "$$X^\\top X\\, w = X^\\top y$$")
        self.assertContains(page, "X^\\top X\\, w = X^\\top y")
        self.assertContains(page, 'name="id"', count=0)
        self.assertContains(page, 'value="lsq-normal" readonly')

    def test_saved_untouched_it_saves_nothing(self):
        answer = self.client.post(self.URL, self.form(self.URL))
        self.assertRedirects(answer, f"{LSQ}?saved=same", fetch_redirect_response=False)
        self.assertFalse(HandIn.objects.exists())

    def test_one_card_changes_and_the_rest_of_the_file_keeps_its_bytes(self):
        before = self.read(DECK)
        data = self.form(self.URL) | {"a": "$$X^\\top X\\, w = X^\\top y$$\r\n\r\nNothing else."}
        answer = self.client.post(self.URL, data)
        self.assertRedirects(answer, f"{LSQ}?saved=lsq-normal", fetch_redirect_response=False)
        after = self.on_site(DECK)
        start, end = before.index("  - id: lsq-normal"), before.index("  - id: lsq-derive")
        self.assertEqual(after[:start], before[:start])  # comments, settings, the card before
        self.assertEqual(after[after.index("  - id: lsq-derive") :], before[end:])
        self.assertIn(
            "    a: |-\n      $$X^\\top X\\, w = X^\\top y$$\n\n      Nothing else.\n", after
        )
        step = HandIn.objects.get()
        self.assertEqual((step.via, list(step.grown) + list(step.shrunk)), ("site", [DECK, DECK]))
        self.assertIn("Nothing else.", Card.objects.get(key="lsq-normal").back_html)
        self.assertEqual(self.read(DECK), before)  # the owner's file is not touched
        self.assertContains(self.client.get(answer["Location"]), "Saved <code>lsq-normal</code>")

    def test_a_new_card_goes_to_the_end_and_the_form_offers_the_next(self):
        url = f"{LSQ}new/"
        self.assertContains(self.client.get(url), "New card")
        data = {
            "id": "lsq-extra",
            "shape": "basic",
            "q": "One more?",
            "a": "Yes: $1 < 2$.",
            "again": "1",
        }
        answer = self.client.post(url, data)
        self.assertRedirects(answer, f"{url}?saved=lsq-extra", fetch_redirect_response=False)
        self.assertTrue(
            self.on_site(DECK).endswith(
                "\n\n  - id: lsq-extra\n    q: One more?\n    a: 'Yes: $1 < 2$.'\n"
            )
        )
        self.assertEqual(Card.objects.get(key="lsq-extra").deck.slug, "least-squares")
        self.assertEqual(HandIn.objects.get().grown, {DECK: 4})
        self.assertContains(self.client.get(LSQ), "lsq-extra")

    def test_a_card_can_become_another_kind(self):
        data = self.form(self.URL) | {
            "shape": "cloze",
            "cloze": "Hit both sides with {{$X^\\top$}}.",
        }
        self.assertEqual(self.client.post(self.URL, data).status_code, 302)
        card = Card.objects.get(key="lsq-normal")
        self.assertEqual(card.kind, "cloze")
        text = self.on_site(DECK)
        block = text[text.index("  - id: lsq-normal") : text.index("  - id: lsq-derive")]
        self.assertNotIn("    q:", block)
        self.assertIn("    hy: true\n", block)

    def test_a_derivation_keeps_its_steps_in_the_order_of_the_page(self):
        url = f"{LSQ}lsq-derive/"
        data = self.form(url)
        steps = sorted({name.split("-")[1] for name in data if name.startswith("steps-")})
        self.assertGreater(len(steps), 2)
        first = {name: value for name, value in data.items() if name.startswith("steps-0-")}
        rest = {name: value for name, value in data.items() if not name.startswith("steps-0-")}
        # The first step is sent last: it was moved to the end.
        self.assertEqual(self.client.post(url, rest | first).status_code, 302)
        card = Card.objects.get(key="lsq-derive")
        self.assertEqual(len(card.steps), len(steps))
        self.assertIn(
            first["steps-0-do"].split("\n")[0][:20],
            self.on_site(DECK).split("lsq-residual")[0].rsplit("- do:", 1)[1],
        )

    def test_what_the_schema_refuses_is_said_at_the_field(self):
        answer = self.client.post(self.URL, self.form(self.URL) | {"a": "  "})
        self.assertEqual(answer.status_code, 200)
        self.assertContains(answer, "a card needs `q` and `a`")
        self.assertFalse(HandIn.objects.exists())
        answer = self.client.post(
            f"{LSQ}new/", {"id": "Not An Id", "shape": "basic", "q": "Q", "a": "A"}
        )
        fields = {f.node.key: f for f in answer.context["sections"][0]["fields"]}
        self.assertTrue(fields["id"].errors)
        self.assertEqual(fields["q"].value, "Q")  # what was typed is still there

    def test_what_the_course_refuses_is_reported_and_nothing_changes(self):
        for data, message in [
            ({"id": "gd-update", "shape": "basic", "q": "Q", "a": "A"}, "is already used"),
            ({"id": "lsq-loss", "shape": "basic", "q": "Q", "a": "A"}, "already"),
            (
                {"id": "lsq-x", "shape": "basic", "q": "Q", "a": "See [](card:nope)."},
                "no card named",
            ),
            ({"id": "new", "shape": "basic", "q": "Q", "a": "A"}, "used by the editor"),
        ]:
            with self.subTest(id=data["id"]):
                answer = self.client.post(f"{LSQ}new/", data)
                self.assertContains(answer, message)
        self.assertFalse(HandIn.objects.exists())

    def test_a_card_is_removed_after_asking_and_the_last_one_is_not(self):
        url = f"{self.URL}remove/"
        asked = self.client.get(url)
        self.assertContains(asked, "<code>lsq-normal</code>")
        answer = self.client.post(url, {"seen": asked.context["seen"]})
        self.assertRedirects(answer, f"{LSQ}?removed=lsq-normal", fetch_redirect_response=False)
        self.assertFalse(Card.objects.filter(key="lsq-normal").exists())
        self.assertNotIn("\n\n\n", self.on_site(DECK))
        self.assertEqual(self.client.get(self.URL).status_code, 404)
        # A card something links to stays: the course would not be valid without it.
        url = f"{LSQ}lsq-derive/remove/"
        answer = self.client.post(url, {"seen": self.client.get(url).context["seen"]})
        self.assertContains(answer, "still depends on it")
        self.assertTrue(Card.objects.filter(key="lsq-derive").exists())

    def test_two_people_on_one_deck(self):
        mine = self.form(self.URL)
        bobs = self.client_class()
        bobs.force_login(self.bob)
        other = f"{LSQ}lsq-loss/"
        page = bobs.get(other)
        theirs = sent(page.context["sections"][0]["fields"]) | {"seen": page.context["seen"]}
        self.assertEqual(bobs.post(other, theirs | {"note": "Bob was here."}).status_code, 302)
        # Another card of the same file: no conflict, and both changes are there.
        self.assertEqual(
            self.client.post(self.URL, mine | {"note": "Ada was here."}).status_code, 302
        )
        text = self.on_site(DECK)
        self.assertIn("Bob was here.", text)
        self.assertIn("Ada was here.", text)
        # The same card: the second to save is told, and may save theirs over it.
        page = bobs.get(self.URL)
        theirs = sent(page.context["sections"][0]["fields"]) | {"seen": page.context["seen"]}
        mine = self.form(self.URL)
        self.assertEqual(bobs.post(self.URL, theirs | {"note": "Bob again."}).status_code, 302)
        answer = self.client.post(self.URL, mine | {"note": "Ada again."})
        self.assertContains(answer, "changed on the site while you were editing")
        self.assertContains(answer, "Ada again.")
        over = answer.context["refused"]["now"]
        self.assertEqual(
            self.client.post(self.URL, mine | {"note": "Ada again.", "over": over}).status_code, 302
        )
        self.assertIn("Ada again.", self.on_site(DECK))


class DeckTests(EditorCase):
    def test_the_decks_and_one_deck_are_listed(self):
        page = self.client.get(CARDS)
        self.assertEqual(
            [row["name"] for row in page.context["rows"]], ["gradient-descent", "least-squares"]
        )
        self.assertContains(page, f'href="{LSQ}"')
        page = self.client.get(LSQ)
        self.assertEqual(
            [row["id"] for row in page.context["rows"]][:2], ["lsq-loss", "lsq-normal"]
        )
        self.assertContains(page, "Write the least-squares loss in matrix form")
        self.assertContains(page, f'href="{LSQ}lsq-loss/"')

    def test_a_new_deck_is_made_with_its_first_card(self):
        url = f"{CARDS}new/"
        self.assertContains(self.client.get(url), "Its first card")
        data = {
            "address": "regularisation", "title": "Regularisation", "topic": "lsq",
            "first-id": "reg-why", "first-shape": "basic", "first-q": "Why regularise?",
            "first-a": "To keep $\\lVert w \\rVert$ small.",
        }  # fmt: skip
        answer = self.client.post(url, data)
        self.assertRedirects(
            answer, f"{CARDS}regularisation/?saved=new", fetch_redirect_response=False
        )
        self.assertEqual(
            self.on_site("content/decks/regularisation.yaml"),
            "title: Regularisation\ntopic: lsq\ncards:\n  - id: reg-why\n    q: Why regularise?\n"
            "    a: |-\n      To keep $\\lVert w \\rVert$ small.\n",
        )
        self.assertEqual(Deck.objects.get(slug="regularisation").cards.count(), 1)
        # Its only card cannot be removed; the deck can.
        url = f"{CARDS}regularisation/reg-why/remove/"
        self.assertContains(self.client.get(url), "cannot be empty")
        seen = self.client.get(url).context["seen"]
        self.assertContains(self.client.post(url, {"seen": seen}), "cannot be empty")
        url = f"{CARDS}regularisation/remove/"
        answer = self.client.post(url, {"seen": self.client.get(url).context["seen"]})
        self.assertRedirects(answer, f"{CARDS}?removed=1", fetch_redirect_response=False)
        self.assertFalse(Deck.objects.filter(slug="regularisation").exists())

    def test_a_new_deck_needs_a_free_plain_address_and_a_whole_first_card(self):
        url = f"{CARDS}new/"
        good = {
            "title": "T",
            "first-id": "t-1",
            "first-shape": "basic",
            "first-q": "Q",
            "first-a": "A",
        }
        for address, message in [
            ("least-squares", "already"),
            ("Has Space", "lowercase"),
            ("new", "already"),
        ]:
            with self.subTest(address=address):
                self.assertContains(self.client.post(url, good | {"address": address}), message)
        answer = self.client.post(url, good | {"address": "fine", "first-a": "", "title": ""})
        head, first = (section["fields"] for section in answer.context["sections"])
        self.assertTrue({f.node.key: f for f in head}["title"].errors)
        self.assertContains(answer, "a card needs `q` and `a`")
        self.assertFalse(HandIn.objects.exists())

    def test_settings_change_their_own_lines_only(self):
        url = f"{LSQ}settings/"
        page = self.client.get(url)
        data = sent(page.context["sections"][0]["fields"]) | {"seen": page.context["seen"]}
        self.assertEqual(data["lecture"], "01-least-squares")
        before = self.read(DECK)
        answer = self.client.post(
            url, data | {"title": "Least squares, drilled", "description": ""}
        )
        self.assertRedirects(answer, f"{LSQ}?saved=settings", fetch_redirect_response=False)
        expected = before.replace(
            "title: Least squares\n", "title: Least squares, drilled\n"
        ).replace("description: Loss, normal equations and when the solution is unique.\n", "")
        self.assertEqual(self.on_site(DECK), expected)
        self.assertEqual(Deck.objects.get(slug="least-squares").title, "Least squares, drilled")
        answer = self.client.post(url, data | {"title": ""})
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(
            {f.node.key: f for f in answer.context["sections"][0]["fields"]}["title"].errors
        )

    def test_a_deck_something_links_to_is_not_removed(self):
        url = f"{LSQ}remove/"
        asked = self.client.get(url)
        self.assertContains(asked, "its 5 items")
        answer = self.client.post(url, {"seen": asked.context["seen"]})
        self.assertContains(answer, "still depends on it")
        self.assertTrue(Deck.objects.filter(slug="least-squares").exists())


class OtherShelvesTests(EditorCase):
    def form(self, url):
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        fields = [f for section in page.context["sections"] for f in section["fields"]]
        return {**sent(fields), "seen": page.context["seen"]}

    def test_a_question_of_each_kind_reads_back_as_it_is(self):
        for item in ("fit-p1", "fit-p2", "fit-p3", "fit-p4", "fit-p5"):
            with self.subTest(question=item):
                url = f"{EDIT}problems/fitting-practice/{item}/"
                answer = self.client.post(url, self.form(url))
                self.assertRedirects(
                    answer,
                    f"{EDIT}problems/fitting-practice/?saved=same",
                    fetch_redirect_response=False,
                )
        self.assertFalse(HandIn.objects.exists())

    def test_a_multiple_choice_question_is_changed(self):
        url = f"{EDIT}problems/fitting-practice/fit-p3/"
        data = self.form(url)
        self.assertEqual(data["type"], "mcq")
        right = next(name for name in data if name.endswith("-correct"))
        position = right.split("-")[1]
        other = next(p for p in ("0", "1", "2") if p != position)
        data.pop(right)
        data[f"choices-{other}-correct"] = "yes"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        question = Question.objects.get(key="fit-p3")
        self.assertTrue(question.choices[int(other)]["correct"])
        # Two right answers are not one right answer.
        data[right] = "yes"
        self.assertContains(
            self.client.post(url, data | {"seen": self.form(url)["seen"]}), "exactly one choice"
        )

    def test_a_true_or_false_question_keeps_false(self):
        url = f"{EDIT}problems/fitting-practice/fit-p5/"
        data = self.form(url)
        self.assertEqual((data["type"], data["correct"]), ("tf", "false"))
        self.assertEqual(self.client.post(url, data | {"title": "A statement"}).status_code, 302)
        text = self.on_site(SET)
        self.assertIn("    correct: false\n", text[text.index("  - id: fit-p5") :])

    def test_a_formula_in_a_section_of_the_sheet(self):
        url = f"{EDIT}formulas/0/f-normal/"
        before = self.read(FORMULAS)
        data = self.form(url) | {"note": "Solve it; do not invert."}
        answer = self.client.post(url, data)
        self.assertRedirects(
            answer, f"{EDIT}formulas/0/?saved=f-normal", fetch_redirect_response=False
        )
        after = self.on_site(FORMULAS)
        self.assertEqual(
            after.split("      - id: f-normal")[0], before.split("      - id: f-normal")[0]
        )
        self.assertEqual(
            after.split("  - title: Gradient descent")[1],
            before.split("  - title: Gradient descent")[1],
        )
        # A new one in the second section, and a new section.
        data = {"id": "f-new", "name": "A new one", "formula": "$$a = b$$"}
        self.assertEqual(self.client.post(f"{EDIT}formulas/1/new/", data).status_code, 302)
        self.assertEqual(Formula.objects.get(key="f-new").section.title, "Gradient descent")
        data = {
            "title": "Regularisation",
            "topic": "lsq",
            "first-id": "f-ridge",
            "first-name": "Ridge",
            "first-formula": "$$\\lambda \\lVert w \\rVert^2$$",
        }
        answer = self.client.post(f"{EDIT}formulas/new/", data)
        self.assertRedirects(answer, f"{EDIT}formulas/?saved=new", fetch_redirect_response=False)
        self.assertEqual(Formula.objects.get(key="f-ridge").section.title, "Regularisation")
        self.assertEqual(
            [row["title"] for row in self.client.get(f"{EDIT}formulas/").context["rows"]][-1],
            "Regularisation",
        )
        # The section's own settings, and taking a section out.
        url = f"{EDIT}formulas/2/settings/"
        self.assertEqual(
            self.client.post(url, self.form(url) | {"title": "Ridge"}).status_code, 302
        )
        self.assertIn("  - title: Ridge\n    topic: lsq\n", self.on_site(FORMULAS))
        url = f"{EDIT}formulas/2/remove/"
        self.assertEqual(
            self.client.post(url, {"seen": self.client.get(url).context["seen"]}).status_code, 302
        )
        self.assertFalse(Formula.objects.filter(key="f-ridge").exists())
        self.assertEqual(self.client.get(f"{EDIT}formulas/7/").status_code, 404)

    def test_a_term_and_the_first_term_of_a_course_without_a_glossary(self):
        url = f"{EDIT}glossary/g-residual/"
        data = self.form(url)
        self.assertEqual(data["see"], ["g-design-matrix"])
        self.assertEqual(
            self.client.post(url, data | {"definition": "What is left over."}).status_code, 302
        )
        self.assertIn("What is left over.", Term.objects.get(key="g-residual").definition_html)
        self.assertIn("    see:\n      - g-design-matrix\n", self.on_site(GLOSSARY))
        page = self.client.get(f"{EDIT}glossary/")
        self.assertContains(page, "Residual")

    def test_the_first_hook_of_a_course_makes_the_file(self):
        (self.course / HOOKS).unlink()
        self.assertEqual(self.sync().status, "synced")
        self.assertContains(self.client.get(f"{EDIT}hooks/"), "Write the first one.")
        data = {"id": "h-first", "for": "The loss", "kind": "rhyme", "hook": "Square it, sum it."}
        answer = self.client.post(f"{EDIT}hooks/new/", data)
        self.assertRedirects(answer, f"{EDIT}hooks/?saved=h-first", fetch_redirect_response=False)
        self.assertEqual(
            self.on_site(HOOKS),
            "hooks:\n  - id: h-first\n    for: The loss\n    kind: rhyme\n"
            "    hook: Square it, sum it.\n",
        )
        self.assertFalse((self.course / HOOKS).exists())  # the owner's files: still without it
        self.assertEqual(Hook.objects.get(key="h-first").target, "The loss")

    def test_a_hook_whose_field_is_called_for(self):
        url = f"{EDIT}hooks/h-singular/"
        data = self.form(url)
        self.assertEqual(data["for"], "What makes the normal equations singular")
        self.assertEqual(
            self.client.post(url, data | {"for": "When the normal equations fail"}).status_code, 302
        )
        self.assertIn("    for: When the normal equations fail\n", self.on_site(HOOKS))
        self.assertEqual(
            Hook.objects.get(key="h-singular").target, "When the normal equations fail"
        )

    def test_to_anyone_else_none_of_it_exists(self):
        self.client.force_login(self.eve)
        for url in (
            CARDS,
            LSQ,
            f"{LSQ}lsq-loss/",
            f"{LSQ}new/",
            f"{CARDS}new/",
            f"{EDIT}glossary/",
            f"{EDIT}formulas/0/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        answer = self.client.post(f"{LSQ}new/", {"id": "x-1", "shape": "basic", "q": "Q", "a": "A"})
        self.assertEqual(answer.status_code, 404)
        self.assertFalse(HandIn.objects.exists())


class FieldsPreviewTests(EditorCase):
    URL = f"{EDIT}preview/fields/"

    def ask(self, fields, file=DECK):
        return self.client.post(
            self.URL, {"file": file, "fields": fields}, content_type="application/json"
        )

    def test_each_text_is_rendered_as_the_site_would(self):
        shown = self.ask(
            [
                {"name": "q", "label": "Question", "text": "Why $a \\le b$?"},
                {"name": "cloze", "label": "Cloze", "text": "A {{blank}} here.", "cloze": True},
                {"name": "a", "label": "Answer", "text": "A <b>tag</b> and [](card:nope)."},
            ]
        ).json()["fields"]
        self.assertEqual([part["name"] for part in shown], ["q", "cloze", "a"])
        self.assertIn('<span class="math inline">a \\le b</span>', shown[0]["html"])
        self.assertIn("cloze", shown[1]["html"])
        self.assertEqual(shown[0]["problems"], [])
        found = {problem["message"][:20] for problem in shown[2]["problems"]}
        self.assertEqual(found, {"raw HTML (<b>) is sh", "no card named `nope`"})
        self.assertEqual({problem["where"] for problem in shown[2]["problems"]}, {"Answer"})

    def test_it_is_about_a_yaml_file_of_the_course_and_sent_as_json(self):
        self.assertEqual(self.ask([], file="../x.yaml").status_code, 404)
        self.assertEqual(self.ask([], file="content/lectures/01-least-squares.md").status_code, 404)
        self.assertEqual(self.client.post(self.URL, {"file": DECK}).status_code, 404)
        self.assertEqual(self.client.get(self.URL).status_code, 405)
        self.assertEqual(
            self.ask([{"name": "q", "label": "Q", "text": "word " * 90_000}]).status_code, 413
        )


class ItemsTests(SimpleTestCase):
    def test_two_layouts_of_the_same_item_are_the_same_item(self):
        one = {"id": "h", "for": "X", "hook": "Y", "kind": "phrase"}
        other = {"hook": "Y\n", "id": "h", "for": "X"}
        self.assertTrue(items.same(schemas.HookSpec, one, other))
        self.assertFalse(items.same(schemas.HookSpec, one, other | {"kind": "rhyme"}))
        self.assertEqual(
            items.fingerprint({"a": 1, "b": "x\n"}), items.fingerprint({"b": "x", "a": 1})
        )
        self.assertEqual(items.fingerprint(None), "")
