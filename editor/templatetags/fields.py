"""Small helpers for the templates that draw a form from a schema (editor/nodes.html).

A template tag library is a module in an app's `templatetags` folder; `{% load fields %}`
makes its filters available in a template. A filter is a plain function of the value to
its left: `{{ fields|steer }}`.
"""

import json

from django import template

register = template.Library()


@register.filter
def steer(fields):
    """The steering choices among these fields, as the state of an Alpine component:
    `{"type": "mcq"}`. A field that depends on one is shown with x-show against it."""
    return json.dumps({field.node.key: field.shown for field in fields if field.steers})


@register.filter
def among_choices(value, choices):
    """Whether a value is one of a field's choices."""
    return str(value) in [str(choice) for choice, _label in choices]


@register.filter
def among(values):
    """A tuple of values as a JavaScript list, for x-show: ["mcq", "multi"]."""
    return json.dumps(list(values))


@register.filter
def height(text):
    """How many rows a text box starts with: its text, within reason."""
    lines = str(text or "").count("\n") + 1
    return max(2, min(lines + 1, 14))


@register.filter
def has(values, value):
    """Whether a ticked value is among those the item has."""
    return str(value) in [str(item) for item in values or []]


@register.filter
def first_line(text, limit=110):
    """The first line of a text, shortened: how an item is named in a list."""
    line = next((part.strip() for part in str(text or "").split("\n") if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
