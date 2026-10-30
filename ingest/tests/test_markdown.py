"""The Markdown dialect, one behaviour per test. SimpleTestCase: no database needed."""

from django.test import SimpleTestCase

from ingest.markdown import Renderer, strip_markdown
from ingest.report import Report


class MarkdownTests(SimpleTestCase):
    def setUp(self):
        self.report = Report("test")
        self.renderer = Renderer(report=self.report)

    def html(self, source, **options):
        return self.renderer.render(source, file="test.md", **options).html

    def test_inline_and_display_maths_are_left_for_katex(self):
        html = self.html("Inline $a^2$.\n\n$$\\frac{a}{b}$$")
        self.assertIn('<span class="math inline">a^2</span>', html)
        self.assertIn('<div class="math block">\\frac{a}{b}</div>', html)
        self.assertEqual([m.display for m in self.renderer.math], [False, True])

    def test_maths_is_escaped_not_interpreted(self):
        self.assertIn("a &lt; b", self.html("$a < b$"))

    def test_display_maths_inside_a_paragraph_stays_valid_html(self):
        self.assertIn(
            '<p>So <span class="math block">x</span> holds.</p>', self.html("So $$x$$ holds.")
        )

    def test_money_is_not_maths(self):
        html = self.html("It costs $5 and then $6.")
        self.assertNotIn("math", html)
        self.assertEqual(self.report.issues, [])

    def test_escaped_dollar(self):
        self.assertIn("costs $5", self.html("It costs \\$5 for $x$ items."))

    def test_file_names_are_not_turned_into_links(self):
        html = self.html("Run train.py and read README.md, see https://example.com/x.")
        self.assertEqual(html.count("<a "), 1)
        self.assertIn('href="https://example.com/x"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_typographic_replacements_are_off(self):
        self.assertIn("(c) Compare", self.html("(c) Compare the two."))

    def test_callouts(self):
        html = self.html(":::definition Residual\nThe gap.\n:::")
        self.assertIn('<aside class="callout callout--definition">', html)
        self.assertIn('<p class="callout__title">Residual</p>', html)

    def test_callout_default_title_and_maths_in_title(self):
        self.assertIn(">Write this on paper</p>", self.html(":::paper\nX\n:::"))
        self.assertIn(
            '<span class="math inline">x</span>', self.html(":::theorem About $x$\nY\n:::")
        )

    def test_collapsible_callouts(self):
        html = self.html(":::recall What is it?\nThe answer.\n:::")
        self.assertIn('<details class="callout callout--recall">', html)
        self.assertIn("<summary", html)

    def test_code_is_highlighted_on_the_server(self):
        html = self.html("```python\ndef f():\n    return 1\n```")
        self.assertIn('<pre class="code" data-lang="python">', html)
        self.assertIn('<span class="k">def</span>', html)

    def test_unknown_language_is_plain_text(self):
        html = self.html("```nosuchlang\na < b\n```")
        self.assertIn("a &lt; b", html)

    def test_raw_html_is_escaped_and_reported(self):
        html = self.html("A <script>alert(1)</script> and <br> break")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertTrue(self.report.errors)

    def test_attribute_allow_list(self):
        html = self.html('![alt](https://example.com/a.png){.adapt width=200 onclick="alert(1)"}')
        self.assertIn('class="adapt"', html)
        self.assertIn('width="200"', html)
        self.assertNotIn("onclick", html)

    def test_javascript_links_are_not_links(self):
        self.assertNotIn("<a", self.html("[x](javascript:alert(1))"))

    def test_tables_and_task_lists(self):
        html = self.html("| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n- [ ] open")
        self.assertIn('<div class="table-box"><table>', html)
        self.assertIn("</table>\n</div>", html)
        self.assertIn('type="checkbox"', html)

    def test_headings_get_ids_and_a_table_of_contents(self):
        rendered = self.renderer.render(
            "## First part\n\n### Detail\n\n## First part", file="t.md", document=True
        )
        self.assertEqual(
            [(e["level"], e["id"]) for e in rendered.toc],
            [(2, "first-part"), (3, "detail"), (2, "first-part-1")],
        )

    def test_line_numbers_follow_the_source(self):
        self.renderer.render(
            "First line.\n\nSecond paragraph\nwith <b>html</b> here.", file="t.md", line=10
        )
        self.assertEqual(self.report.errors[0].line, 13)

    def test_cloze_blanks(self):
        html = self.html("The {{first}} and {{$x^{y^{2}}$}}.", cloze=True)
        self.assertEqual(html.count('<span class="cloze">'), 2)
        self.assertIn('<span class="cloze"><span class="math inline">x^{y^{2}}</span></span>', html)

    def test_braces_are_ordinary_text_outside_cloze_fields(self):
        self.assertIn("{{not a blank}}", self.html("Jinja uses {{not a blank}}."))

    def test_inline_rendering_has_no_paragraph(self):
        self.assertEqual(
            self.renderer.inline("**Key** $x$", file="t.md"),
            '<strong>Key</strong> <span class="math inline">x</span>',
        )

    def test_label_stops_before_the_first_formula(self):
        label = strip_markdown(
            r"Compute **Fisher's** direction in integer form for $\mu_A$, $\Sigma_A$."
        )
        self.assertEqual(label, "Compute Fisher's direction in integer form")

    def test_short_label_keeps_a_placeholder_for_maths(self):
        self.assertEqual(strip_markdown(r"Invert a $2 \times 2$ matrix."), "Invert a … matrix.")

    def test_label_of_a_cloze_keeps_the_words(self):
        self.assertEqual(
            strip_markdown("A {{vertical}} gap between points."), "A vertical gap between points."
        )
