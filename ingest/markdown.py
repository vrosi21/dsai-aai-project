"""Markdown -> HTML for everything authors write.

One dialect for lecture notes and for the short Markdown strings inside YAML (card answers,
solutions, formula notes), so maths, code and callouts look identical everywhere.

Raw HTML in the source is switched off: it comes out as visible text, never as markup. That
is what makes it safe for templates to print the stored HTML unescaped — every tag in it was
produced by this module.

Maths is not rendered here. `$…$` becomes <span class="math inline"> holding the LaTeX source,
and KaTeX typesets it in the browser; the fragments are collected so sync can verify them.
"""

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.amsmath import amsmath_plugin
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.container import container_plugin
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from .refs import RefError

# name -> (default title, collapsed by default)
CALLOUTS = {
    "note": ("Note", False),
    "tip": ("Tip", False),
    "warning": ("Warning", False),
    "pitfall": ("Pitfall", False),
    "definition": ("Definition", False),
    "theorem": ("Theorem", False),
    "example": ("Example", False),
    "paper": ("Write this on paper", False),
    "proof": ("Proof", True),
    "recall": ("Recall", True),
    "solution": ("Solution", True),
}

IMAGE_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
MATH_TOKENS = {"math_inline", "math_inline_double", "math_block", "math_block_label", "amsmath"}
EXTERNAL_SCHEMES = {"http", "https", "mailto"}

_HTML_TAG = re.compile(
    r"</?(?:div|span|br|p|b|i|u|sub|sup|table|tr|td|th|img|a|details|summary|center|font|"
    r"h[1-6]|ul|ol|li|em|strong|code|pre|hr|blockquote)\b[^>]*>",
    re.I,
)
_STRAY_DOLLAR = re.compile(r"\$[A-Za-z\\{(]|[A-Za-z})]\$")
_CALLOUT_LINE = re.compile(r"^:{3,}\s*([A-Za-z][\w-]*)")
_FORMATTER = HtmlFormatter(nowrap=True)


def _highlight(code, lang, _attrs):
    lang = (lang or "").strip().lower()
    lexer = None
    if lang:
        try:
            lexer = get_lexer_by_name(lang)
        except ClassNotFound:
            lexer = None
    body = pygments_highlight(code, lexer, _FORMATTER) if lexer else html.escape(code)
    label = f' data-lang="{html.escape(lang)}"' if lang else ""
    return f'<pre class="code"{label}><code>{body}</code></pre>\n'


def _render_math(_self, tokens, idx, _options, _env):
    token = tokens[idx]
    tex = html.escape(token.content.strip())
    if token.type == "math_inline":
        return f'<span class="math inline">{tex}</span>'
    if token.type == "math_inline_double":  # $$…$$ written inside a paragraph
        return f'<span class="math block">{tex}</span>'
    return f'<div class="math block">{tex}</div>\n'


def _render_table(self, tokens, idx, options, env):
    # A box of its own around every table: wider than the page, a table scrolls sideways
    # inside it (prose.css) instead of squeezing its columns or widening the page.
    tag = self.renderToken(tokens, idx, options, env)
    if tokens[idx].nesting == 1:
        return '<div class="table-box">' + tag
    return tag + "</div>\n"


def _render_callout(_self, tokens, idx, _options, _env):
    token = tokens[idx]
    kind = token.type.removeprefix("container_").rsplit("_", 1)[0]
    default_title, collapsed = CALLOUTS[kind]
    if token.nesting == -1:
        return "</div></details>\n" if collapsed else "</div></aside>\n"
    title = token.info.strip().split(None, 1)
    title = _MD.renderInline(title[1]) if len(title) > 1 else html.escape(default_title)
    if collapsed:
        return (
            f'<details class="callout callout--{kind}">'
            f'<summary class="callout__title">{title}</summary><div class="callout__body">\n'
        )
    return (
        f'<aside class="callout callout--{kind}">'
        f'<p class="callout__title">{title}</p><div class="callout__body">\n'
    )


def cloze_plugin(md):
    """`{{text}}` -> <span class="cloze">text</span>, with the inside parsed as usual.

    Braces are matched by depth so LaTeX survives: {{$x^{y^{2}}$}} is one blank."""

    def cloze(state, silent):
        src, start, maximum = state.src, state.pos, state.posMax
        if not src.startswith("{{", start):
            return False
        depth, pos, end = 0, start + 2, -1
        while pos < maximum:
            char = src[pos]
            if char == "\\":
                pos += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                if depth:
                    depth -= 1
                elif src.startswith("}}", pos):
                    end = pos
                    break
                else:
                    return False
            pos += 1
        if end <= start + 2:
            return False
        if not silent:
            token = state.push("cloze_open", "span", 1)
            token.attrSet("class", "cloze")
            state.pos, state.posMax = start + 2, end
            state.md.inline.tokenize(state)
            state.push("cloze_close", "span", -1)
        state.pos, state.posMax = end + 2, maximum
        return True

    md.inline.ruler.before("emphasis", "cloze", cloze)


def _build(*, cloze=False):
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "typographer": False, "highlight": _highlight},
    )
    md.enable(["table", "strikethrough", "linkify"])
    # Only link real URLs. Fuzzy matching would turn `train.py` and `README.md` into links,
    # because .py and .md are country domains.
    md.linkify.set({"fuzzy_link": False, "fuzzy_email": False, "fuzzy_ip": False})
    md.use(
        dollarmath_plugin,
        allow_labels=False,
        allow_space=True,
        allow_digits=False,  # keeps "$5 and $6" as money
        double_inline=True,
    )
    md.use(amsmath_plugin)
    md.use(deflist_plugin)
    md.use(footnote_plugin)
    md.use(tasklists_plugin)
    # An allow-list, so `{onclick=…}` cannot be attached to an image or link.
    md.use(attrs_plugin, allowed=["class", "id", "width", "height", "title"])
    md.use(anchors_plugin, min_level=2, max_level=4)
    for name in CALLOUTS:
        md.use(container_plugin, name=name, render=_render_callout)
    for rule in MATH_TOKENS:
        md.add_render_rule(rule, _render_math)
    md.add_render_rule("table_open", _render_table)
    md.add_render_rule("table_close", _render_table)
    if cloze:
        md.use(cloze_plugin)
    return md


_MD = _build()
_MD_CLOZE = _build(cloze=True)


@dataclass
class Rendered:
    html: str = ""
    text: str = ""
    toc: list = field(default_factory=list)
    words: int = 0


@dataclass
class MathFragment:
    tex: str
    display: bool
    file: str
    line: int
    where: str = ""


@dataclass
class _Where:
    file: str
    line: int
    where: str
    base_dir: Path | None


class Renderer:
    """Renders Markdown for one course, reporting problems against the file they came from."""

    def __init__(self, *, report, resolver=None, assets=None, content_root=None):
        self.report = report
        self.resolver = resolver
        self.assets = assets
        self.content_root = Path(content_root).resolve() if content_root else None
        self.math: list[MathFragment] = []

    # -- public -------------------------------------------------------------------------

    def render(self, source, *, file, line=1, where="", base_dir=None, cloze=False, document=False):
        """Render a block of Markdown. `line` is where `source` starts inside `file`."""
        if not source or not source.strip():
            return Rendered()
        md = _MD_CLOZE if cloze else _MD
        at = _Where(file, line, where, Path(base_dir) if base_dir else None)
        self._lint_source(source, at)
        env = {}
        tokens = md.parse(source, env)
        text, toc = [], []
        for index, token in enumerate(tokens):
            token_line = at.line + (token.map[0] if token.map else 0)
            if token.type == "heading_open":
                level = int(token.tag[1])
                if document and level == 1:
                    self._error(
                        at,
                        token_line,
                        "a `#` heading: the page title comes from `title` in the front matter",
                        "start sections at `##`",
                    )
                if document and level in (2, 3):
                    toc.append(
                        {
                            "level": level,
                            "id": token.attrGet("id"),
                            "text": _plain(tokens[index + 1]),
                        }
                    )
            elif token.type in MATH_TOKENS:
                self._collect_math(token, at, token_line)
                text.append(token.content)
            elif token.type in ("fence", "code_block"):
                text.append(token.content)
            elif token.type == "inline":
                self._inline(token, at, token_line, text)
        plain = " ".join(" ".join(text).split())
        return Rendered(
            html=md.renderer.render(tokens, md.options, env),
            text=plain,
            toc=toc,
            words=len(plain.split()),
        )

    def inline(self, source, *, file, line=1, where=""):
        """Render one line of Markdown without the surrounding <p>."""
        if not source or not source.strip():
            return ""
        at = _Where(file, line, where, None)
        env = {}
        tokens = _MD.parseInline(source.strip(), env)
        for token in tokens:
            self._inline(token, at, line, [])
        return _MD.renderer.render(tokens, _MD.options, env)

    # -- internals ----------------------------------------------------------------------

    def _error(self, at, line, message, hint=""):
        self.report.error(at.file, message, line=line, where=at.where, hint=hint)

    def _warn(self, at, line, message, hint=""):
        self.report.warn(at.file, message, line=line, where=at.where, hint=hint)

    def _lint_source(self, source, at):
        in_fence = False
        for offset, raw in enumerate(source.split("\n")):
            stripped = raw.lstrip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = _CALLOUT_LINE.match(stripped)
            if match and match.group(1) not in CALLOUTS:
                self._error(
                    at,
                    at.line + offset,
                    f"`:::{match.group(1)}` is not a callout the site knows",
                    "use one of: " + ", ".join(CALLOUTS),
                )

    def _collect_math(self, token, at, line):
        tex = token.content.strip()
        if not tex:
            self._error(at, line, "empty maths: `$$` with nothing inside")
            return
        self.math.append(MathFragment(tex, token.type != "math_inline", at.file, line, at.where))

    def _inline(self, token, at, line, text):
        children = token.children or []
        index = 0
        while index < len(children):
            child = children[index]
            if child.type == "text":
                text.append(child.content)
                if _HTML_TAG.search(child.content):
                    self._error(
                        at,
                        line,
                        f"raw HTML ({_HTML_TAG.search(child.content).group(0)}) is shown as text, "
                        "not rendered",
                        "use Markdown: **bold**, $x_i$ for subscripts, a blank line for a break",
                    )
                elif _STRAY_DOLLAR.search(child.content):
                    self._warn(
                        at,
                        line,
                        "a `$` was left as plain text here",
                        "for maths check both `$` are present and not next to a digit; "
                        "write money as \\$5",
                    )
            elif child.type in ("softbreak", "hardbreak"):
                line += 1  # children carry no position; a paragraph's lines map to its breaks
            elif child.type == "code_inline":
                text.append(child.content)
            elif child.type in MATH_TOKENS:
                self._collect_math(child, at, line)
                text.append(child.content)
            elif child.type == "image":
                self._image(child, at, line)
            elif child.type == "link_open":
                label = self._link(child, at, line)
                # [](card:fisher-01) -> the link text becomes the target's own title.
                if label and index + 1 < len(children) and children[index + 1].type == "link_close":
                    filler = Token("text", "", 0)
                    filler.content = label
                    children.insert(index + 1, filler)
            index += 1

    def _link(self, token, at, line):
        href = unquote(token.attrGet("href") or "")
        if href.startswith("#"):
            return None
        scheme = href.split(":", 1)[0].lower() if ":" in href else ""
        if scheme in EXTERNAL_SCHEMES:
            if scheme != "mailto":
                token.attrSet("target", "_blank")
                token.attrSet("rel", "noopener noreferrer")
                token.attrJoin("class", "link-external")
            return None
        if self.resolver is None:
            return None
        try:
            url, label = self.resolver.resolve(href)
        except RefError as exc:
            self._error(at, line, str(exc), exc.hint)
            return None
        token.attrSet("href", url)
        token.attrJoin("class", "link-ref")
        return label

    def _image(self, token, at, line):
        src = unquote(token.attrGet("src") or "")
        token.attrSet("loading", "lazy")
        if re.match(r"^https?://", src, re.I):
            self._warn(
                at,
                line,
                f"image loaded from another site: {src}",
                "save it under an assets/ folder next to this file and link it by relative path",
            )
            return
        if at.base_dir is None or self.content_root is None or self.assets is None:
            return
        path = (at.base_dir / src).resolve()
        if not path.is_relative_to(self.content_root):
            self._error(at, line, f"image path leaves the course content folder: {src}")
        elif not path.is_file() and not self.assets.knows(path):
            self._error(
                at,
                line,
                f"image not found: {src}",
                "paths are relative to this file, e.g. assets/boundary.svg",
            )
        elif path.suffix.lower() not in IMAGE_SUFFIXES:
            self._error(
                at, line, f"unsupported image type: {src}", "use svg, png, jpg, webp or gif"
            )
        else:
            token.attrSet("src", self.assets.add(path))


def _plain(inline_token):
    parts = []
    for child in inline_token.children or []:
        if child.type in ("text", "code_inline") or child.type in MATH_TOKENS:
            parts.append(child.content)
    return " ".join("".join(parts).split())


# Stands in for a formula while a label is being built; never appears in real text.
_FORMULA = "\N{OBJECT REPLACEMENT CHARACTER}"
# Words a label should not end on once the formula that followed them is gone.
_DANGLING = frozenset(
    ["a", "an", "and", "are", "as", "at", "for", "from", "if", "in"]
    + ["is", "of", "on", "or", "that", "the", "to", "when", "where", "with"]
)


def strip_markdown(source):
    """A plain-text label for a Markdown string (link text, hook targets).

    Maths cannot be shown in a plain label. If enough words come before the first formula,
    the label stops there; otherwise each formula is replaced by an ellipsis."""
    if not source:
        return ""
    parts = []
    for token in _MD_CLOZE.parse(source, {}):
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type in ("text", "code_inline"):
                parts.append(child.content)
            elif child.type in ("softbreak", "hardbreak"):
                parts.append(" ")
            elif child.type in MATH_TOKENS:
                parts.append(_FORMULA)
        parts.append(" ")
    text = " ".join("".join(parts).split())
    head = text.split(_FORMULA, 1)[0].strip()
    if _FORMULA in text and len(head) >= 20:
        words = head.rstrip(" ,;:").split()
        while words and words[-1].strip(",;:").lower() in _DANGLING:
            words.pop()
        text = " ".join(words).rstrip(" ,;:")
    else:
        text = text.replace(_FORMULA, "…")
    return re.sub(r"\s+([.,;:?!])", r"\1", text).strip()
