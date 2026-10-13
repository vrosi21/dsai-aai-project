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
