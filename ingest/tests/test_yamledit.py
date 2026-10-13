"""Writing course files (ingest/yamledit.py): whatever it writes, the importer must read
back as the same data, and must accept without a complaint."""

import datetime as dt

import yaml
from django.conf import settings
from django.test import SimpleTestCase

from ingest import yamledit
from ingest.report import Report
from ingest.yamlio import load_yaml, split_front_matter

LATEX = "$$ \\hat\\theta = (X^\\top X)^{-1} X^\\top y $$"

CARD = {
    "id": "lsq-01",
    "q": "What does least squares minimise?",
    "a": LATEX,
    "note": "Two lines.\n\n  An indented one, kept as it is.",
    "steps": [
        {"do": "Write the residual.", "why": "It is what is minimised:\n$r = y - X\\theta$"},
        {"do": "Differentiate."},
    ],
    "hy": True,
    "topic": "lsq",
}

# Strings YAML would read as something else if they were written bare.
AWKWARD = [
    "yes",
    "no",
    "null",
    "~",
    "12",
    "1.5",
    "2026-10-13",
    "2026-27",
    "a: b",
    "- a dash first",
    "# not a comment",
    "'single' and \"double\"",
    "ends with a colon:",
    " a space first",
    "a space last ",
    "@at",
    "*star",
    "&and",
    "[bracket",
    "{brace",
    "100%",
    "naïve — čćžšđ",
    "back\\slash",
    "tab\there",
    "line one\nline two",
    "  starts indented\nand goes on",
    "ends in blank lines\n\n\n",
    "blank line with spaces inside\n   \nafter it",
]


class DumpTests(SimpleTestCase):
    def assertReadsBack(self, data):
        text = yamledit.dump(data)
        self.assertEqual(yaml.safe_load(text), yamledit.tidy(data), msg=text)
        self.assertTrue(yamledit.agrees(text, data))
        return text

    def assertImporterIsContent(self, text):
        """No tab, no anchor, no double-quoted backslash: the lints of yamlio.py."""
        report = Report(course="t")
        load_yaml(text, file="t.yaml", report=report)
        self.assertEqual([issue.message for issue in report.issues], [], msg=text)

    def test_a_card_is_written_the_way_the_guide_asks_for_it(self):
        text = self.assertReadsBack({"title": "Least squares", "cards": [CARD]})
        self.assertImporterIsContent(text)
        # Maths goes in a block, where a backslash is a backslash.
        self.assertIn(f"    a: |-\n      {LATEX}\n", text)
        self.assertIn("  - id: lsq-01\n    q: What does least squares minimise?\n", text)
        self.assertIn("      - do: Write the residual.\n        why: |-\n", text)
        self.assertIn("    hy: true\n", text)
        self.assertNotIn('"', text)

    def test_every_awkward_string_survives_as_a_value_and_as_a_list_item(self):
        for value in AWKWARD:
            with self.subTest(value=value):
                text = self.assertReadsBack({"key": value, "list": [value, "plain"]})
                self.assertImporterIsContent(text)

    def test_a_tab_becomes_spaces_and_other_control_characters_go(self):
        self.assertEqual(yamledit.clean("a\tb\x00c\x07"), "a    bc")
        self.assertEqual(yamledit.clean("one\r\ntwo\rthree"), "one\ntwo\nthree")

    def test_what_is_empty_is_left_out(self):
        data = {"title": "T", "summary": "", "topics": [], "due": None, "plan": {"skip": ""}}
        self.assertEqual(yamledit.dump(data), "title: T\n")
        self.assertEqual(yamledit.dump({"hy": False, "n": 0}), "hy: false\nn: 0\n")

    def test_dates_numbers_and_truth_values_keep_their_kind(self):
        data = {"date": dt.date(2026, 10, 13), "number": 3, "points": 2.5, "done": False}
        text = self.assertReadsBack(data)
        self.assertEqual(text, "date: 2026-10-13\nnumber: 3\npoints: 2.5\ndone: false\n")

    def test_an_item_can_be_written_at_any_depth(self):
        for depth in (0, 1, 3):
            text = yamledit.item(CARD, depth)
            self.assertTrue(text.startswith("  " * depth + "- id: lsq-01\n"))
            wrapped = "cards:\n" + text if depth else text
            loaded = yaml.safe_load(wrapped)
            found = loaded["cards"] if depth else loaded
            self.assertEqual(found, [yamledit.tidy(CARD)])

    def test_the_example_course_reads_back_whatever_file_is_rewritten(self):
        """Every YAML file of the example course, read and written again, is the same
        data: the writer covers everything the format uses."""
        files = sorted((settings.EXAMPLE_COURSE_ROOT / "example").rglob("*.yaml"))
        self.assertGreater(len(files), 8)
        for path in files:
            with self.subTest(file=path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                text = self.assertReadsBack(data)
                self.assertImporterIsContent(text)


class PageTests(SimpleTestCase):
    def test_a_page_is_front_matter_then_the_text(self):
        meta = {"title": "Least squares", "number": 1, "topics": ["lsq"], "summary": ""}
        text = yamledit.page(meta, "\r\n## Why\r\n\r\n$a \\le b$\r\n\r\n")
        self.assertEqual(
            text,
            "---\ntitle: Least squares\nnumber: 1\ntopics:\n  - lsq\n---\n\n## Why\n\n$a \\le b$\n",
        )
        front, body, line = split_front_matter(text)
        self.assertEqual(
            yaml.safe_load(front), {"title": "Least squares", "number": 1, "topics": ["lsq"]}
        )
        self.assertEqual((body.strip(), line), ("## Why\n\n$a \\le b$", 7))


EXAMPLE = settings.EXAMPLE_COURSE_ROOT / "example" / "content"
DECK = EXAMPLE / "decks" / "least-squares.yaml"
FORMULAS = EXAMPLE / "reference" / "formulas.yaml"
NEW_CARD = {
    "id": "lsq-new",
    "q": "Why $X^\\top$?",
    "a": "It projects.\n\nOnto the columns.",
    "hy": True,
}


class PartTests(SimpleTestCase):
    """Changing part of a file: that part's lines change and nothing else does."""

    def setUp(self):
        self.text = DECK.read_text(encoding="utf-8")
        self.data = yaml.safe_load(self.text)

    def card(self, text, card_id):
        """The lines of one card as they are in the file."""
        found = []
        for line in text.split("\n"):
            if line.startswith("  - id: "):
                if found:
                    break
                found = [line] if line == f"  - id: {card_id}" else []
            elif found:
                found.append(line)
        return "\n".join(found).rstrip("\n")

    def others_untouched(self, after, *changed):
        for entry in self.data["cards"]:
            if entry["id"] not in changed:
                with self.subTest(card=entry["id"]):
                    self.assertEqual(
                        self.card(after, entry["id"]), self.card(self.text, entry["id"])
                    )
        head = self.text.split("cards:")[0]
        self.assertTrue(after.startswith(head), msg="the comments and settings above the cards")

    def tidy_file(self, text):
        self.assertNotIn("\n\n\n", text)
        self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))

    def test_one_card_is_rewritten_and_the_rest_keeps_its_bytes(self):
        for position in (0, 2, len(self.data["cards"]) - 1):
            with self.subTest(position=position):
                old = self.data["cards"][position]
                new = {**old, "a": "A new answer, with $x^2$.\n\nAnd a second paragraph."}
                after = yamledit.replace_item(self.text, ("cards", position), new)
                loaded = yaml.safe_load(after)
                self.assertEqual(loaded["cards"][position], yamledit.tidy(new))
                self.assertEqual(len(loaded["cards"]), len(self.data["cards"]))
                self.others_untouched(after, old["id"])
                self.assertIn("    a: |-\n      A new answer, with $x^2$.\n\n      And a", after)
                self.tidy_file(after)

    def test_a_card_written_back_as_it_is_reads_the_same(self):
        for position, old in enumerate(self.data["cards"]):
            after = yamledit.replace_item(self.text, ("cards", position), old)
            self.assertEqual(yamledit.tidy(yaml.safe_load(after)), yamledit.tidy(self.data))

    def test_a_card_is_added_where_it_is_wanted_set_apart_like_the_others(self):
        count = len(self.data["cards"])
        for position, index in ((None, count), (0, 0), (2, 2), (99, count)):
            with self.subTest(position=position):
                after = yamledit.add_item(self.text, ("cards",), NEW_CARD, position)
                loaded = yaml.safe_load(after)
                self.assertEqual(len(loaded["cards"]), count + 1)
                self.assertEqual(loaded["cards"][index], yamledit.tidy(NEW_CARD))
                self.others_untouched(after)
                self.assertIn(
                    "\n  - id: lsq-new\n    q: |-\n      Why $X^\\top$?\n    a: |-\n", after
                )
                self.tidy_file(after)

    def test_a_card_is_taken_out_with_the_line_that_set_it_apart(self):
        count = len(self.data["cards"])
        for position in (0, 2, count - 1):
            with self.subTest(position=position):
                gone = self.data["cards"][position]["id"]
                after = yamledit.remove_item(self.text, ("cards", position))
                loaded = yaml.safe_load(after)
                self.assertNotIn(gone, [card["id"] for card in loaded["cards"]])
                self.assertEqual(len(loaded["cards"]), count - 1)
                self.others_untouched(after, gone)
                self.tidy_file(after)

    def test_a_list_inside_a_list(self):
        text = FORMULAS.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        row = {"id": "f-new", "name": "A new one", "formula": "$$a = b$$"}
        after = yamledit.add_item(text, ("sections", 0, "rows"), row)
        loaded = yaml.safe_load(after)
        self.assertEqual(loaded["sections"][0]["rows"][-1], row)
        self.assertEqual(loaded["sections"][1], data["sections"][1])
        self.assertIn(
            "      - id: f-new\n        name: A new one\n        formula: $$a = b$$\n\n  - title:",
            after,
        )
        after = yamledit.replace_item(text, ("sections", 1, "rows", 0), row)
        self.assertEqual(yaml.safe_load(after)["sections"][1]["rows"][0], row)
        self.assertEqual(yaml.safe_load(after)["sections"][0], data["sections"][0])
        after = yamledit.remove_item(text, ("sections", 0, "rows", 1))
        rows = yaml.safe_load(after)["sections"][0]["rows"]
        self.assertEqual([row["id"] for row in rows], ["f-loss", "f-residual"])
        self.assertTrue(after.startswith("# The formula sheet."))

    def test_a_comment_above_an_entry_stays_with_it(self):
        text = (
            "cards:\n  - id: a\n    q: |-\n      One\n      # not a comment: part of the text\n"
            "\n  # about b\n  - id: b\n    q: Two\n"
        )
        after = yamledit.replace_item(text, ("cards", 0), {"id": "a", "q": "Changed"})
        self.assertEqual(
            after, "cards:\n  - id: a\n    q: Changed\n\n  # about b\n  - id: b\n    q: Two\n"
        )
        after = yamledit.remove_item(text, ("cards", 0))
        self.assertEqual(after, "cards:\n  # about b\n  - id: b\n    q: Two\n")
        loaded = yaml.safe_load(yamledit.replace_item(text, ("cards", 1), {"id": "b", "q": "New"}))
        self.assertEqual(loaded["cards"][0]["q"], "One\n# not a comment: part of the text")

    def test_settings_above_a_list_are_changed_added_and_taken_out(self):
        after = yamledit.set_keys(self.text, (), {"title": "Least squares, again"}, before="cards")
        self.assertEqual(
            after, self.text.replace("title: Least squares\n", "title: Least squares, again\n")
        )
        after = yamledit.set_keys(self.text, (), {"description": "", "lecture": None}, "cards")
        self.assertEqual(
            after,
            self.text.replace("lecture: 01-least-squares\n", "").replace(
                "description: Loss, normal equations and when the solution is unique.\n", ""
            ),
        )
        bare = "# a deck\ntitle: T\n\ncards:\n  - id: a\n    q: Q\n    a: A\n"
        after = yamledit.set_keys(bare, (), {"topic": "lsq", "description": "Two\nlines."}, "cards")
        self.assertEqual(
            after,
            "# a deck\ntitle: T\ntopic: lsq\ndescription: |-\n  Two\n  lines.\n\ncards:\n"
            "  - id: a\n    q: Q\n    a: A\n",
        )
        self.assertEqual(yamledit.set_keys("title: T", (), {"topic": "x"}), "title: T\ntopic: x\n")

    def test_settings_of_an_entry_of_a_list(self):
        text = FORMULAS.read_text(encoding="utf-8")
        after = yamledit.set_keys(text, ("sections", 1), {"title": "Descent", "topic": ""}, "rows")
        self.assertEqual(
            after,
            text.replace("  - title: Gradient descent\n    topic: gd\n", "  - title: Descent\n"),
        )
        section = yaml.safe_load(after)["sections"][1]
        self.assertEqual((section["title"], "topic" in section), ("Descent", False))

    def test_a_place_that_is_not_there(self):
        for path in (("cards", 99), ("nope",), ("cards", 0, "q", "deeper"), ("title", 0)):
            with self.subTest(path=path), self.assertRaises(yamledit.Misplaced):
                yamledit.replace_item(self.text, path, NEW_CARD)

    def test_the_result_is_read_back_and_written_whole_if_it_is_not_what_was_meant(self):
        expected = {**self.data, "cards": [*self.data["cards"], NEW_CARD]}
        good = yamledit.changed(
            self.text, expected, lambda text: yamledit.add_item(text, ("cards",), NEW_CARD)
        )
        self.assertTrue(good.startswith("# A deck is one file."))  # the careful way worked

        def wrong(_text):
            return "cards: []\n"

        def lost(_text):
            raise yamledit.Misplaced("gone")

        for attempt in (wrong, lost):
            whole = yamledit.changed(self.text, expected, attempt)
            self.assertFalse(whole.startswith("#"))  # written anew: right, without comments
            self.assertEqual(yaml.safe_load(whole), yamledit.tidy(expected))

    def test_every_entry_of_every_list_in_the_example_course_can_be_rewritten(self):
        """Each item of each list file, written back through the door the editor uses,
        leaves the file meaning what it meant."""
        seen = 0
        for path in sorted(EXAMPLE.rglob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            keys = ("cards", "questions", "terms", "hooks", "loci")
            key = next((name for name in keys if name in data), None)
            if key is None:
                continue
            for position, entry in enumerate(data[key]):
                with self.subTest(file=path.name, position=position):
                    after = yamledit.replace_item(text, (key, position), entry)
                    self.assertEqual(yamledit.tidy(yaml.safe_load(after)), yamledit.tidy(data))
                    seen += 1
        self.assertGreater(seen, 25)
