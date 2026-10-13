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
