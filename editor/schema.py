"""Forms made from the content schemas.

What a card, a question or a glossary term may contain is written down once, in
ingest/schemas.py, as Pydantic models: the importer checks every file against them, and
the docs are generated from them. The editor's forms come from the same place. This
module reads a model and describes its fields (`describe`), reads a submitted form back
into the data that would stand in the file (`parse`), has the model check it
(`validate`), and lays values and errors over the fields for a template to draw (`bind`).

So there is no form class per content type, and no second set of rules to drift from the
first: a field added to a schema appears in its form, and what a form refuses is exactly
what the importer would refuse, in the importer's own words.

A form here is not a Django `Form`. Django's forms describe a flat set of fields, and
content is nested: a question has parts, a plan has phases that have tasks. The names of
the inputs carry the nesting instead (`steps-0-do`, `plan-phases-1-tasks-0-text`), which
`parse` reads back into dicts and lists.
"""

import datetime as dt
import re
import types
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import AfterValidator, BaseModel, StringConstraints, ValidationError
from pydantic_core import PydanticUndefined

from ingest import schemas, yamledit


@dataclass
class Node:
    """One field of a form."""

    key: str  # the key in the file (`for`, where the schema gives a field that name)
    kind: str  # see KINDS
    label: str
    help: str = ""
    required: bool = False
    choices: list = field(default_factory=list)  # (value, label)
    children: list = field(default_factory=list)  # of a group, or of one entry of a list
    entry: str = ""  # what one entry of a list is called: "step"
    when: tuple | None = None  # (key of a choice beside it, values): only then shown and read
    default: Any = None  # the value that need not be written
    fixed: bool = False  # shown but not changed here (an id, once it exists)
    pretend: bool = False  # a choice that only steers the form and is not written
    of: Any = None  # for such a choice: which option an item's data amounts to
    low: float | None = None
    high: float | None = None


# How a kind of field is drawn and read:
#   line      one line of text            text     a few plain lines
#   markdown  Markdown with LaTeX         cloze    the same, with {{blanks}}
#   id        an id                       choice   one of a few words
#   topic, lecture                        one of the course's, picked from a list
#   number    a whole number              decimal  a number
#   flag      yes or no, off by default   truth    yes, no, or not said
#   date
#   refs      lines of links              ids      lines of ids
#   topics, exams, numbers                several of the course's, ticked
#   group     fields that belong together list     entries that repeat, each a group
KINDS = {
    "line", "text", "markdown", "cloze", "id", "choice", "topic", "lecture", "number",
    "decimal", "flag", "truth", "date", "refs", "ids", "topics", "exams", "numbers",
    "group", "list",
}  # fmt: skip
TEXT = {"line", "text", "markdown", "cloze", "id"}
MANY = {"refs", "ids", "topics", "exams", "numbers"}

LABELS = {
    "q": "Question",
    "a": "Answer",
    "hy": "High-yield",
    "for": "What it brings back",
    "hook": "Mnemonic",
    "why": "Why",
    "do": "Step",
    "see": "See also",
    "refs": "Links",
    "links": "Links",
    "loci": "Stops",
    "kind": "Kind",
    "id": "Id",
    "includes": "Also covers",
    "format": "Format of the exam",
    "skip": "What to skip",
    "duration": "Minutes allowed",
    "negative": "Points lost for a wrong answer",
}
# What one entry of a list is called, by the key of the list.
ENTRIES = {
    "steps": "step",
    "parts": "part",
    "choices": "choice",
    "weights": "weight",
    "phases": "phase",
    "tasks": "task",
    "predictions": "prediction",
    "loci": "stop",
    "topics": "topic",
    "cards": "card",
    "questions": "question",
    "rows": "formula",
    "sections": "section",
    "terms": "term",
    "hooks": "hook",
}


@dataclass
class Switch:
    """A choice that decides which of a model's fields apply."""

    key: str
    shows: dict  # field key -> the values of the choice for which it is shown
    label: str = ""
    options: list = field(default_factory=list)  # only for a choice that is not a field
    of: Any = None  # only then: which option an existing item has


SWITCHES = {
    # A card's kind is not written anywhere: it follows from which fields the card has.
    schemas.CardSpec: Switch(
        key="shape",
        label="Kind of card",
        options=[
            ("basic", "Question and answer"),
            ("steps", "Derivation, one step at a time"),
            ("cloze", "Fill in the blanks"),
        ],
        shows={
            "q": ("basic", "steps"),
            "a": ("basic", "steps"),
            "steps": ("steps",),
            "cloze": ("cloze",),
        },
        of=lambda data: "cloze" if data.get("cloze") else "steps" if data.get("steps") else "basic",
    ),
    schemas.QuestionSpec: Switch(
        key="type", shows={"choices": ("mcq", "multi"), "correct": ("tf",)}
    ),
}
CHOICE_LABELS = {
    "open": "Open: worked on paper",
    "mcq": "Multiple choice: one right answer",
    "multi": "Multiple choice: several right answers",
    "tf": "True or false",
}


@dataclass
class Course:
    """What a form may point to in the course it belongs to."""

    topics: list = field(default_factory=list)  # (id, title)
    lectures: list = field(default_factory=list)  # (file name, title)
    numbers: list = field(default_factory=list)  # (lecture number, title)
    exams: list = field(default_factory=list)  # (folder name, title)


def _unwrap(annotation, metadata=()):
    """(the plain type, everything annotated on the way, whether None is allowed)."""
    metadata, optional = list(metadata), False
    while True:
        origin = get_origin(annotation)
        if origin is Annotated:
            annotation, *extra = get_args(annotation)
            metadata += extra
        elif origin in (Union, types.UnionType):
            options = [arg for arg in get_args(annotation) if arg is not type(None)]
            optional = optional or len(options) < len(get_args(annotation))
            annotation = options[0]
        else:
            return annotation, metadata, optional


def _string_kind(key, metadata):
    for item in metadata:
        if isinstance(item, AfterValidator):
            return "cloze" if key == "cloze" else "markdown"
        if isinstance(item, StringConstraints):
            if item.pattern:
                return {"topic": "topic", "lecture": "lecture"}.get(key, "id")
            if item.max_length:
                return "line"
    return "text"


def _bounds(metadata):
    low = next((m.ge for m in metadata if getattr(m, "ge", None) is not None), None)
    high = next((m.le for m in metadata if getattr(m, "le", None) is not None), None)
    return low, high


def _label(key):
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _node(key, info, course):
    annotation, metadata, optional = _unwrap(info.annotation, info.metadata)
    default = None if info.default in (PydanticUndefined, None) else info.default
    node = Node(
        key=key,
        kind="text",
        label=_label(key),
        help=(info.description or "").replace("`", ""),
        required=info.is_required(),
        default=default,
    )
    origin = get_origin(annotation)
    if origin is Literal:
        node.kind = "choice"
        node.choices = [
            (v, CHOICE_LABELS.get(v, str(v).capitalize())) for v in get_args(annotation)
        ]
    elif origin is list:
        inner, inner_meta, _ = _unwrap(get_args(annotation)[0])
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            node.kind, node.children = "list", describe(inner, course)
            node.entry = ENTRIES.get(key, "entry")
        elif inner is int:
            node.kind, node.choices = "numbers", course.numbers
        elif key == "topics":
            node.kind, node.choices = "topics", course.topics
        elif key == "includes":
            node.kind, node.choices = "exams", course.exams
        elif _string_kind(key, inner_meta) == "id":
            node.kind = "ids"
        else:
            node.kind = "refs"
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        node.kind, node.children = "group", describe(annotation, course)
        node.required = False
    elif annotation is bool:
        # Yes, no, or not said (a true/false question's answer); or a plain yes or no.
        node.kind = "truth" if optional and info.default is None else "flag"
        node.default = None if node.kind == "truth" else bool(default)
    elif annotation is int:
        node.kind = "number"
        node.low, node.high = _bounds(metadata)
    elif annotation is float:
        node.kind = "decimal"
        node.low, node.high = _bounds(metadata)
    elif annotation is dt.date:
        node.kind = "date"
    else:
        node.kind = _string_kind(key, metadata)
        if node.kind == "topic":
            node.choices = course.topics
        elif node.kind == "lecture":
            node.choices = course.lectures
    return node


def describe(model, course=None, *, only=None, without=()):
    """The fields of a model as form fields, in the schema's order. `only` and `without`
    name keys to keep or leave out (a deck's settings are its fields without `cards`)."""
    course = course or Course()
    nodes = []
    switch = SWITCHES.get(model)
    for name, info in model.model_fields.items():
        key = info.alias or name
        if key in without or (only is not None and key not in only):
            continue
        node = _node(key, info, course)
        if switch and key in switch.shows:
            node.when = (switch.key, switch.shows[key])
        nodes.append(node)
    if switch and switch.options and (only is None or switch.key in only):
        choice = Node(switch.key, "choice", switch.label, choices=switch.options, pretend=True)
        choice.of = switch.of
        nodes.insert(1 if nodes and nodes[0].key == "id" else 0, choice)
    return nodes


# ---- reading a submitted form --------------------------------------------------------------


def _number(text, kind):
    try:
        value = float(text)
    except ValueError:
        return text  # left as typed: the schema then says what is wrong with it
    if kind == "number":
        return int(value) if value.is_integer() else text
    return int(value) if value.is_integer() else value


def _scalar(node, text):
    text = text.strip() if node.kind != "text" else text
    if node.kind in ("markdown", "cloze", "text"):
        text = yamledit.clean(text)
    if text == "":
        return None
    if node.kind in ("number", "decimal"):
        return _number(text, node.kind)
    if node.kind == "truth":
        return {"true": True, "false": False}.get(text)
    if node.kind == "date":
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return text
    return text


def _positions(post, stem):
    """The entries of a list that were sent, in the order they stand on the page: the
    numbers in the inputs' names say which inputs belong together, not where they go."""
    pattern = re.compile(rf"^{re.escape(stem)}-(\d+)-")
    seen = []
    for name in post:
        match = pattern.match(name)
        if match and match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def parse(nodes, post, prefix=""):
    """The data a submitted form stands for, as it would be written to the file: what is
    empty, hidden by the form's steering choice, or the default is left out."""
    data = {}
    chosen = {node.key: post.get(prefix + node.key, "") for node in nodes if node.kind == "choice"}
    for node in nodes:
        name = prefix + node.key
        if node.pretend or node.fixed:
            continue
        if node.when and chosen.get(node.when[0]) not in node.when[1]:
            continue
        if node.kind == "group":
            value = parse(node.children, post, f"{name}-")
        elif node.kind == "list":
            rows = [parse(node.children, post, f"{name}-{i}-") for i in _positions(post, name)]
            value = [row for row in rows if row]
        elif node.kind in MANY:
            sent = post.getlist(name) if hasattr(post, "getlist") else post.get(name, [])
            value = [text.strip() for text in sent if text.strip()]
            if node.kind == "numbers":
                value = [_number(text, "number") for text in value]
        elif node.kind == "flag":
            value = name in post
            if value == node.default:
                continue
        else:
            value = _scalar(node, post.get(name, ""))
            if value is not None and value == node.default:
                continue
        if value not in (None, "", [], {}):
            data[node.key] = value
    return data


def validate(model, data):
    """Have the schema check `data`. Returns {place: [messages]}, empty when it is fine.
    A place is the path to a field, as (key, position, key, …); () is the item as a whole."""
    errors = {}
    try:
        model.model_validate(data)
    except ValidationError as failure:
        for error in failure.errors():
            place = tuple(part for part in error["loc"] if isinstance(part, (str, int)))
            message, hint = schemas.explain(error)
            message = message.removeprefix("Value error, ")
            errors.setdefault(place, []).append(f"{message}. {hint}" if hint else message)
    return errors


# ---- laying values and errors over the fields ----------------------------------------------


@dataclass
class Bound:
    """A field with its name on the page, its value and what is wrong with it."""

    node: Node
    name: str
    value: Any = None
    errors: list = field(default_factory=list)
    children: list = field(default_factory=list)  # a group's fields
    rows: list = field(default_factory=list)  # a list's entries, each a list of Bound
    blank: list = field(default_factory=list)  # a list's empty entry, to be copied
    mark: str = ""  # what stands for the position in the names of `blank`
    count: int = 0
    steers: bool = False  # a choice that fields beside it depend on

    @property
    def id(self):
        return f"f-{self.name}"

    @property
    def shown(self):
        """The value as an input holds it."""
        if self.value is None:
            return ""
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        return self.value


def bind(nodes, data=None, errors=None, prefix="", place=(), depth=0, chosen=None):
    """`nodes` with `data` in them and `errors` beside them. `chosen` says what was picked
    in a steering choice that is not part of the data ({name on the page: value}), when
    the form comes back from the browser; otherwise that follows from the data."""
    data, errors, chosen = data or {}, errors or {}, chosen or {}
    steering = {node.when[0] for node in nodes if node.when}
    bound = []
    for node in nodes:
        name, here = prefix + node.key, (*place, node.key)
        item = Bound(node, name, data.get(node.key), errors.get(here, []))
        item.steers = node.key in steering
        if node.pretend:
            item.value = chosen.get(name) or (node.of(data) if node.of else node.choices[0][0])
        elif item.value is None and node.default is not None and node.kind != "group":
            item.value = node.default
        if node.kind == "group":
            item.children = bind(
                node.children, data.get(node.key), errors, f"{name}-", here, depth, chosen
            )
        elif node.kind == "list":
            entries = data.get(node.key) or []
            item.rows = [
                bind(node.children, entry, errors, f"{name}-{i}-", (*here, i), depth + 1, chosen)
                for i, entry in enumerate(entries)
            ]
            item.mark = f"__{depth}__"
            item.blank = bind(node.children, None, None, f"{name}-{item.mark}-", (), depth + 1)
            item.count = len(entries)
        elif node.kind in MANY:
            item.value = [str(entry) for entry in data.get(node.key) or []]
        bound.append(item)
    return bound


def general(errors):
    """What is wrong with the item as a whole, not with one of its fields."""
    return errors.get((), [])


def unplaced(nodes, errors):
    """Errors about places the form has no field for: shown with the general ones, so
    that nothing the schema objects to goes unsaid."""
    known = {node.key for node in nodes}
    return [
        f"{'.'.join(map(str, place))}: {message}"
        for place, messages in errors.items()
        if place and place[0] not in known
        for message in messages
    ]


def picked(nodes, post, prefix=""):
    """What was picked in the steering choices that are not part of the data, by their
    names on the page: `bind` needs it to draw a form that came back as it was sent."""
    found = {}
    for node in nodes:
        name = prefix + node.key
        if node.pretend and post.get(name):
            found[name] = post[name]
        elif node.kind == "group":
            found |= picked(node.children, post, f"{name}-")
        elif node.kind == "list":
            for i, position in enumerate(_positions(post, name)):
                inner = picked(node.children, post, f"{name}-{position}-")
                # The form is drawn again with its entries numbered from nought.
                found |= {
                    key.replace(f"{name}-{position}-", f"{name}-{i}-", 1): value
                    for key, value in inner.items()
                }
    return found
