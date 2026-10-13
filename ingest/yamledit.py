"""Write course files: the opposite direction of yamlio.py.

The importer reads YAML strictly: no tabs, no anchors, and never a double-quoted string
with a backslash in it, because that is where LaTeX is silently destroyed. An editor on the
site has to write files the importer accepts and a person can still read, so this module
writes YAML one way only:

    title: Least squares                 one line: plain, or quoted where YAML needs it
    hy: true
    a: |-                                anything with a line break or a backslash: a
      $$ \\hat\\theta = (X^\\top X)^{-1} X^\\top y $$      block, where every character
                                                        means itself
    steps:
      - do: Write the residual.
        why: |-
          It is what is minimised.

`dump` writes a whole mapping, `page` a Markdown file with its front matter. Both go
through `tidy` first, which is also what a caller compares against when it reads the
result back: `yaml.safe_load(dump(data)) == tidy(data)` holds for everything this module
writes, and the tests keep it that way.

Nothing here imports Django.
"""

import datetime as dt

import yaml

INDENT = "  "
# Characters that have no place in a line of text. A tab becomes spaces, because the
# importer refuses tabs and YAML would otherwise write the string in double quotes with a
# backslash in it.
_CONTROL = {code: None for code in range(32) if code not in (9, 10, 13)}
_CONTROL[9] = "    "
_CONTROL[0x7F] = None


def clean(text):
    """A string as it is stored: Unix line endings, no control characters, no blank lines
    at either end, and no spaces left on lines that are otherwise empty."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL)
    lines = [line if line.strip() else "" for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def tidy(value):
    """`value` as it will be written: strings cleaned, and what is empty left out.

    None, an empty string, an empty list and an empty mapping all mean "not given", which
    in a course file is said by not writing the key at all.
    """
    if isinstance(value, dict):
        kept = {key: tidy(item) for key, item in value.items()}
        return {key: item for key, item in kept.items() if not _empty(item)}
    if isinstance(value, (list, tuple)):
        return [item for item in (tidy(item) for item in value) if not _empty(item)]
    if isinstance(value, str):
        return clean(value)
    return value


def _empty(value):
    return value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)


def _needs_block(text):
    return "\n" in text or "\\" in text


def _scalar(value):
    """A value that fits on one line, written as YAML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return str(value)
    # PyYAML knows when a string needs quotes ("yes", "12", "a: b", a leading "-"). Asking
    # it for a one-entry mapping gives the string exactly as it may follow a key.
    written = yaml.safe_dump({"k": str(value)}, allow_unicode=True, width=2**30)
    return written[len("k: ") :].rstrip("\n")


def _block(text, depth):
    """`text` as a literal block: the header to put after the key, and the lines under it."""
    # A block's indentation is read off its first line. One that starts with a space has
    # to say how far the block is indented, or the space would be taken for indentation.
    header = f"|{len(INDENT)}-" if text.startswith(" ") else "|-"
    pad = INDENT * (depth + 1)
    return header, [pad + line if line else "" for line in text.split("\n")]


def _entry(head, value, depth, lines):
    """Write `value` after `head`, which ends in "key:" or is a list item's dash."""
    if isinstance(value, dict):
        lines.append(head)
        _mapping(value, depth + 1, lines)
    elif isinstance(value, list):
        lines.append(head)
        _sequence(value, depth + 1, lines)
    elif isinstance(value, str) and _needs_block(value):
        header, body = _block(value, depth)
        lines.append(f"{head} {header}")
        lines.extend(body)
    else:
        lines.append(f"{head} {_scalar(value)}")


def _mapping(data, depth, lines, first=None):
    """The keys of `data`, one per line. `first`, when given, starts the first line in
    place of its indentation: that is how a mapping sits in a list item ("- key: …")."""
    pad = INDENT * depth
    for number, (key, value) in enumerate(data.items()):
        head = first if number == 0 and first is not None else pad
        _entry(f"{head}{key}:", value, depth, lines)


def _sequence(items, depth, lines):
    pad = INDENT * depth
    for item in items:
        if isinstance(item, dict):
            # The dash and its space are as wide as one step of indentation, so the keys
            # after the first line up under the first.
            _mapping(item, depth + 1, lines, first=f"{pad}- ")
        elif isinstance(item, list):
            # A list inside a list does not occur in course files; written on one line.
            flow = yaml.safe_dump(item, default_flow_style=True, allow_unicode=True, width=2**30)
            lines.append(f"{pad}- {flow.strip()}")
        elif isinstance(item, str) and _needs_block(item):
            header, body = _block(item, depth)
            lines.append(f"{pad}- {header}")
            lines.extend(body)
        else:
            lines.append(f"{pad}- {_scalar(item)}")


def dump(data, depth=0):
    """A mapping as the text of a YAML file (or, with `depth`, as part of one)."""
    lines = []
    _mapping(tidy(data), depth, lines)
    return "\n".join(lines) + "\n" if lines else ""


def item(data, depth=0):
    """One mapping as an entry of a list: "- key: …" at this depth, its other keys under it."""
    lines = []
    _sequence([tidy(data)], depth, lines)
    return "\n".join(lines) + "\n"


def page(meta, body):
    """The text of a Markdown page: front matter between two `---` lines, then the body."""
    return f"---\n{dump(meta)}---\n\n{clean(body)}\n"


def agrees(text, data):
    """Whether `text`, read back as YAML, is `data`. The check every writer here must pass
    before what it wrote is trusted."""
    try:
        return yaml.safe_load(text) == tidy(data)
    except yaml.YAMLError:
        return False


# ---- changing part of a file ---------------------------------------------------------------
#
# A deck of forty cards is one file. Changing one card should change that card's lines and
# nothing else: the file keeps its comments, its layout and every other card byte for
# byte, and the course's history shows what was really changed. PyYAML can say where each
# part of a file sits (`yaml.compose` gives every node its start and end), so a part is
# replaced as lines of text, and never by writing the whole file anew.
#
# Every function here takes the file's text and returns its new text. None of them is
# trusted: `changed` reads the result back and compares it with what was meant.


class Misplaced(LookupError):
    """The place that was to be changed is not in the file (any more)."""


def _node(root, path):
    """The node at `path` (keys and list positions) under `root`, with its parent."""
    parent, node = None, root
    for step in path:
        parent = node
        if isinstance(node, yaml.MappingNode) and isinstance(step, str):
            node = next((value for key, value in node.value if key.value == step), None)
        elif isinstance(node, yaml.SequenceNode) and isinstance(step, int):
            node = node.value[step] if 0 <= step < len(node.value) else None
        else:
            node = None
        if node is None:
            raise Misplaced(".".join(map(str, path)))
    return parent, node


def _trimmed(lines, start, end, column):
    """Where a part that begins on line `start` really ends, given that the next part
    begins on line `end`: blank lines between the two, and comments that sit no deeper
    than `column`, belong to what follows, not to this part."""
    while end > start + 1:
        line = lines[end - 1]
        inner = line.strip()
        outside = inner.startswith("#") and len(line) - len(line.lstrip()) <= column
        if inner and not outside:
            break
        end -= 1
    return end


def _indented(text, column):
    pad = " " * column
    return [pad + line if line else "" for line in text.rstrip("\n").split("\n")]


def _compose(text):
    root = yaml.compose(text)
    if root is None:
        raise Misplaced("the file is empty")
    return root, text.split("\n")


def _join(lines):
    text = "\n".join(lines)
    return text if text.endswith("\n") else text + "\n"


def _item_span(lines, item):
    """The lines of one entry of a list, and the column its dash is in."""
    column = max(0, item.start_mark.column - len(INDENT))
    start = item.start_mark.line
    return start, _trimmed(lines, start, item.end_mark.line, column), column


def replace_item(text, path, data):
    """The file with the list entry at `path` (e.g. ("cards", 3)) written anew."""
    root, lines = _compose(text)
    _parent, node = _node(root, path)
    start, end, column = _item_span(lines, node)
    return _join([*lines[:start], *_indented(item(data), column), *lines[end:]])


def remove_item(text, path):
    """The file without the list entry at `path`."""
    root, lines = _compose(text)
    parent, node = _node(root, path)
    start, end, _column = _item_span(lines, node)
    # The empty line that set it apart goes with it: the one after it, or, for the last
    # entry of a list, the one before (what follows the list keeps its distance).
    if path[-1] == len(parent.value) - 1:
        while start > 0 and not lines[start - 1].strip():
            start -= 1
    else:
        while end < len(lines) - 1 and not lines[end].strip():
            end += 1
    return _join([*lines[:start], *lines[end:]])


def add_item(text, path, data, position=None):
    """The file with a new entry in the list at `path`, at `position` (default: last)."""
    root, lines = _compose(text)
    _parent, node = _node(root, path)
    if not isinstance(node, yaml.SequenceNode) or not node.value:
        raise Misplaced(".".join(map(str, path)))
    entries = node.value
    spans = [_item_span(lines, entry) for entry in entries]
    column = spans[0][2]
    # Entries set apart by an empty line stay that way.
    spaced = any(not lines[span[0] - 1].strip() for span in spans[1:])
    new = _indented(item(data), column)
    if position is None or position >= len(entries):
        at = spans[-1][1]
        return _join([*lines[:at], *([""] if spaced else []), *new, *lines[at:]])
    at = spans[max(0, position)][0]
    return _join([*lines[:at], *new, *([""] if spaced else []), *lines[at:]])


def set_keys(text, path, values, before=None):
    """The file with keys of the mapping at `path` (() is the whole file) set to `values`.
    A key whose value is empty is taken out; one that is not there yet is put in ahead of
    the key `before` (a deck's settings go above its `cards`), or else at the end."""
    values = {key: tidy(value) for key, value in values.items()}
    for key in values:
        root, lines = _compose(text)
        _parent, node = _node(root, path)
        if not isinstance(node, yaml.MappingNode):
            raise Misplaced(".".join(map(str, path)))
        pairs = node.value
        column = pairs[0][0].start_mark.column if pairs else 0
        ends = [pair[0].start_mark.line for pair in pairs[1:]] + [node.end_mark.line]
        if node.end_mark.line == len(lines) - 1 and lines[-1].strip():
            ends[-1] = len(lines)  # the file does not end in a line break
        where = next((i for i, pair in enumerate(pairs) if pair[0].value == key), None)
        value = values[key]
        new = [] if _empty(value) else _indented(dump({key: value}), column)
        if where is None:
            if not new:
                continue
            anchor = next((i for i, pair in enumerate(pairs) if pair[0].value == before), None)
            at = ends[-1] if anchor is None else pairs[anchor][0].start_mark.line
            at = _trimmed(lines, 0, at, column) if anchor is None else at
            while anchor is not None and at > 0 and not lines[at - 1].strip():
                at -= 1  # above the empty line that sets `before` apart
            lines[at:at] = new
        else:
            start = pairs[where][0].start_mark.line
            end = _trimmed(lines, start, ends[where], column)
            if new and where == 0:
                # The first key of a list entry shares its line with the dash.
                new[0] = lines[start][:column] + new[0][column:]
            lines[start:end] = new
        text = _join(lines)
    return text


def changed(text, expected, attempt):
    """Change part of a file and make sure of the result.

    `attempt(text)` is one of the functions above. What it returns is read back; if that
    is not `expected` (the file's whole content as data, as it should be now), or the
    place could not be found, the file is written whole from `expected` instead: correct,
    at the price of its comments. Returns None if even that does not read back, which a
    caller must treat as "cannot be saved".
    """
    expected = tidy(expected)
    try:
        result = attempt(text)
        if tidy(yaml.safe_load(result)) == expected:
            return result
    except (Misplaced, yaml.YAMLError, IndexError, AttributeError):
        pass
    whole = dump(expected)
    return whole if agrees(whole, expected) else None
