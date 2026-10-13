"""The content that lives in YAML files: where each kind is kept, and how one piece of a
file is read and changed.

Most YAML files of a course have the same shape: a few settings, then a list of items.
A deck is a title and its cards; a problem set its questions; the glossary is nothing but
terms. `Shelf` describes one such kind (which file, which key holds the list, which
schema an item answers to), and everything else here works on any of them. The formula
sheet fits too, one level down: each of its sections is a title with a list of rows.

Reading is plain: the file is parsed and the piece picked out. Writing never rewrites
the file. It is a function from the file's text to its new text (`ingest.yamledit`
replaces just the lines of the piece), run by `desk.handins.change` under the course's
lock, on the file as it is at that moment. That is also where a save learns whether the
piece is still what the form was filled from.
"""

import hashlib
import json
from dataclasses import dataclass

import yaml

from desk import handins
from ingest import schemas, yamledit

from . import source


@dataclass(frozen=True)
class Shelf:
    key: str  # its name in the editor's addresses: /edit/<course>/<key>/…
    title: str  # what the section is called: "Flashcards"
    noun: str  # one file (or one section) of it: "deck"
    one: str  # one item: "card"
    model: type  # the schema of the file, or of one section
    list_key: str  # the key that holds the items
    item_model: type
    folder: str = ""  # one file per <noun> in this folder …
    file: str = ""  # … or everything in this one file
    within: tuple = ()  # in that file, the list whose entries are the <noun>s
    label: str = "title"  # the field of an item that names it in a list
    public: str = ""  # the name of the course page that shows it

    @property
    def single(self):
        """One container only: the glossary, the hooks."""
        return bool(self.file) and not self.within

    @property
    def has_settings(self):
        return any(name != self.list_key for name in self.model.model_fields)


SHELVES = {
    shelf.key: shelf
    for shelf in (
        Shelf(
            "cards", "Flashcards", "deck", "card", schemas.DeckFile, "cards", schemas.CardSpec,
            folder="content/decks", label="q", public="catalog:deck",
        ),
        Shelf(
            "problems", "Problems", "problem set", "question", schemas.ProblemSetFile,
            "questions", schemas.QuestionSpec, folder="content/problems",
            public="catalog:problem_set",
        ),
        Shelf(
            "papers", "Papers", "paper", "question", schemas.PaperFile, "questions",
            schemas.QuestionSpec, folder="content/exams", public="catalog:paper",
        ),
        Shelf(
            "formulas", "Formulas", "section", "formula", schemas.FormulaSectionSpec, "rows",
            schemas.FormulaRowSpec, file="content/reference/formulas.yaml",
            within=("sections",), label="name", public="catalog:formulas",
        ),
        Shelf(
            "glossary", "Glossary", "glossary", "term", schemas.GlossaryFile, "terms",
            schemas.TermSpec, file="content/reference/glossary.yaml", label="term",
            public="catalog:glossary",
        ),
        Shelf(
            "hooks", "Hooks", "list of hooks", "hook", schemas.HooksFile, "hooks",
            schemas.HookSpec, file="content/memory/hooks.yaml", label="for",
            public="catalog:memory",
        ),
    )
}  # fmt: skip


@dataclass(frozen=True)
class Place:
    """Where one container is: a file, and (for a section of the formula sheet) the
    path to it inside the file."""

    name: str
    path: tuple = ()


def place(course, shelf, name="", exam=""):
    """The place of the container called `name` (a file's name without its ending, or a
    section's position), whether or not it exists."""
    if shelf.within:
        return Place(shelf.file, (*shelf.within, int(name)))
    if shelf.file:
        return Place(shelf.file)
    folder = f"{shelf.folder}/{exam}" if exam else shelf.folder
    # A course file may end in .yml; a new one is given .yaml.
    for ending in (".yaml", ".yml"):
        if source.read(course, f"{folder}/{name}{ending}") is not None:
            return Place(f"{folder}/{name}{ending}")
    return Place(f"{folder}/{name}.yaml")


def exam_place(course, exam):
    """Where an exam's own page is kept: the assessment.yaml of its folder."""
    for ending in (".yaml", ".yml"):
        name = f"content/exams/{exam}/assessment{ending}"
        if source.read(course, name) is not None:
            return Place(name)
    return Place(f"content/exams/{exam}/assessment.yaml")


def files_under(course, folder):
    """The names of the course's files in a folder, at any depth."""
    return [name for name, _size, _text in source.listing(course) if name.startswith(f"{folder}/")]


def load(text):
    """A file's content as data, or None if it is not a mapping."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def read(course, where):
    """The file of a place as data, or None if there is no such file."""
    raw = source.read(course, where.name)
    return load(raw.decode("utf-8-sig", errors="replace")) if raw is not None else None


def at(data, path):
    """The part of `data` at `path`, or None."""
    for step in path:
        try:
            data = data[step]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def containers(course, shelf, exam=""):
    """The containers of a shelf that exist: (name, its data), in order."""
    if shelf.within:
        whole = read(course, Place(shelf.file)) or {}
        sections = at(whole, shelf.within) or []
        return [
            (str(i), section) for i, section in enumerate(sections) if isinstance(section, dict)
        ]
    if shelf.file:
        whole = read(course, Place(shelf.file))
        return [("", whole)] if whole is not None else []
    folder = f"{shelf.folder}/{exam}" if exam else shelf.folder
    found = []
    for name, _size, text in source.listing(course):
        head, _slash, last = name.rpartition("/")
        stem, _dot, ending = last.rpartition(".")
        if head == folder and text and ending in ("yaml", "yml") and stem != "assessment":
            data = read(course, Place(name))
            if data is not None:
                found.append((stem, data))
    return found


def fingerprint(data):
    """What a form remembers of the piece it was filled from. Two pieces that mean the
    same have the same one, however they are laid out in the file."""
    if data is None:
        return ""
    text = json.dumps(yamledit.tidy(data), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def position_of(entries, item_id):
    """Where the item with this id stands in a list, or None."""
    for position, entry in enumerate(entries or []):
        if isinstance(entry, dict) and str(entry.get("id")) == item_id:
            return position
    return None


def settings_of(container, shelf):
    return {key: value for key, value in (container or {}).items() if key != shelf.list_key}


def same(model, one, other):
    """Whether two pieces of data are the same item as far as the schema is concerned:
    `kind: phrase` written out and left to its default are."""
    try:
        one, other = yamledit.tidy(one), yamledit.tidy(other)
        return model.model_validate(one) == model.model_validate(other)
    except ValueError:
        return False


def _conflict(what, there=True):
    return handins.Refused(
        "conflict",
        [f"This {what} was changed on the site after you opened it."]
        if there
        else [f"This {what} was removed from the site after you opened it."],
    )


def _written(text, expected, attempt):
    result = yamledit.changed(text, expected, attempt)
    if result is None:
        raise handins.Refused("bad", ["This could not be written as a course file."])
    return result


def change(user, course, where, make):
    """Change the file of `where`. `make(text, data)` is given the file as it is now (None
    twice if it does not exist) and returns its new text, or None to remove the file.
    Raises handins.Refused, and then nothing changed."""

    def edit(files):
        raw = files.get(where.name)
        text = raw.decode("utf-8-sig", errors="replace") if raw is not None else None
        result = make(text, load(text) if text is not None else None)
        if result is None:
            files.pop(where.name, None)
        else:
            files[where.name] = result.encode("utf-8")
        return files

    return handins.change(user, course, edit)


def _container(data, where, shelf):
    found = at(data, where.path) if data is not None else None
    if not isinstance(found, dict):
        raise _conflict(shelf.noun, there=False)
    return found


def save_item(user, course, shelf, where, item_id, item, seen):
    """Put `item` in place of the item `item_id` of the container at `where`; with
    `item_id` None it is added at the end. `seen` is the fingerprint the form was filled
    from ("" for a new item)."""

    def make(text, data):
        if text is None and item_id is None and shelf.single:
            # The first term makes the glossary: the file is the list and nothing else.
            return yamledit.dump({shelf.list_key: [item]})
        container = _container(data, where, shelf)
        entries = container.get(shelf.list_key) or []
        path = (*where.path, shelf.list_key)
        if item_id is None:
            if position_of(entries, str(item.get("id"))) is not None:
                raise handins.Refused(
                    "conflict", [f"There is a {shelf.one} with the id `{item['id']}` already."]
                )
            container[shelf.list_key] = [*entries, item]
            return _written(text, data, lambda t: yamledit.add_item(t, path, item))
        position = position_of(entries, item_id)
        if position is None:
            raise _conflict(shelf.one, there=False)
        if fingerprint(entries[position]) != seen:
            raise _conflict(shelf.one)
        entries[position] = item
        return _written(text, data, lambda t: yamledit.replace_item(t, (*path, position), item))

    return change(user, course, where, make)


def remove_item(user, course, shelf, where, item_id, seen):
    def make(text, data):
        container = _container(data, where, shelf)
        entries = container.get(shelf.list_key) or []
        position = position_of(entries, item_id)
        if position is None:
            raise _conflict(shelf.one, there=False)
        if fingerprint(entries[position]) != seen:
            raise _conflict(shelf.one)
        if len(entries) == 1:
            raise handins.Refused(
                "bad",
                [f"It is the only {shelf.one} here, and a {shelf.noun} cannot be empty. "
                 f"Remove the {shelf.noun} instead."],
            )  # fmt: skip
        del entries[position]
        path = (*where.path, shelf.list_key, position)
        return _written(text, data, lambda t: yamledit.remove_item(t, path))

    return change(user, course, where, make)


def keys_of(model, *, without=()):
    """The keys a schema gives a file, in its order."""
    names = [info.alias or name for name, info in model.model_fields.items()]
    return [name for name in names if name not in without]


def part(data, keys):
    """Those keys of a mapping, where it has them."""
    return {key: data[key] for key in keys if key in (data or {})}


def _meaning(model, container):
    """A mapping as its schema reads it, key by key: with every default filled in, so
    that `likelihood: medium` written out and left out are seen to be the same."""
    try:
        return model.model_validate(yamledit.tidy(container)).model_dump(by_alias=True)
    except ValueError:
        return None


def save_keys(user, course, where, keys, values, seen, *, model, before=None, what="page"):
    """Set `keys` of the mapping at `where` to `values`; a key without a value is taken
    out, and no other key is touched. A file that is not there yet is made from them.
    `seen` is the fingerprint of those keys as the form was filled from them, and `model`
    the schema of the mapping, which decides whether a key's value has changed at all."""

    def make(text, data):
        if text is None and not where.path:
            if seen:
                raise _conflict(what, there=False)
            return yamledit.dump(part(values, keys))
        container = at(data, where.path) if data is not None else None
        if not isinstance(container, dict):
            raise _conflict(what, there=False)
        if fingerprint(part(container, keys)) != seen:
            raise _conflict(what)
        # Only the keys that really changed are written: the others keep their lines as
        # their author laid them out, comments and all.
        wanted = {**{k: v for k, v in container.items() if k not in keys}, **part(values, keys)}
        was, now = _meaning(model, container), _meaning(model, wanted)
        if was is None or now is None:  # not for this to judge: the course's check will
            was, now = yamledit.tidy(container), yamledit.tidy(wanted)
        update = {key: values.get(key) for key in keys if was.get(key) != now.get(key)}
        if not update:
            return text
        for key, value in update.items():
            if value in (None, "", [], {}):
                container.pop(key, None)
            else:
                container[key] = value
        return _written(text, data, lambda t: yamledit.set_keys(t, where.path, update, before))

    return change(user, course, where, make)


def save_settings(user, course, shelf, where, values, seen):
    """Set the settings of the container at `where` (everything but its list of items)."""
    keys = keys_of(shelf.model, without=(shelf.list_key,))
    return save_keys(
        user,
        course,
        where,
        keys,
        values,
        seen,
        model=shelf.model,
        before=shelf.list_key,
        what=shelf.noun,
    )


def create(user, course, shelf, where, settings, item):
    """A new container with its first item: a new file, or a new section in the sheet."""
    content = {**settings, shelf.list_key: [item]}

    def make(text, data):
        if not shelf.within:
            if text is not None:
                raise handins.Refused(
                    "conflict", [f"There is a {shelf.noun} of this name already."]
                )
            return yamledit.dump(content)
        # A section of the one file; the file itself may not be there yet.
        outer = shelf.within[0]
        if text is None:
            return yamledit.dump({outer: [content]})
        data = data or {}
        data[outer] = [*(data.get(outer) or []), content]
        return _written(text, data, lambda t: yamledit.add_item(t, shelf.within, content))

    return change(user, course, where, make)


def remove_container(user, course, shelf, where, seen):
    """Remove a whole deck, problem set or paper (its file), or a section of the sheet."""

    def make(text, data):
        container = _container(data, where, shelf)
        if fingerprint(container) != seen:
            raise _conflict(shelf.noun)
        if not shelf.within:
            return None
        sections = at(data, shelf.within)
        if len(sections) == 1:
            return None  # the last section: the sheet goes with it
        del sections[where.path[-1]]
        return _written(text, data, lambda t: yamledit.remove_item(t, where.path))

    return change(user, course, where, make)


def remove_files(user, course, names, seen):
    """Remove whole files (a palace; an exam with its papers). `seen` is the fingerprint
    of the first of them as data."""

    def edit(files):
        first = files.get(names[0])
        data = load(first.decode("utf-8-sig", errors="replace")) if first is not None else None
        if fingerprint(data) != seen:
            raise _conflict("page", there=first is not None)
        for name in names:
            files.pop(name, None)
        return files

    return handins.change(user, course, edit)


def ids_in(container, shelf):
    """The ids people's progress is stored under, in a container."""
    entries = (container or {}).get(shelf.list_key) or []
    return [str(entry["id"]) for entry in entries if isinstance(entry, dict) and "id" in entry]
