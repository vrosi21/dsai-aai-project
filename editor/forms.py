"""The editor's forms.

A Django form does three jobs here: it draws the fields, it turns what the browser sent
(always text) into values of the right kind (a number, a date, a list), and it collects
what is wrong, per field. What a page's front matter may contain is not decided here,
though: that is `ingest.schemas.DocMeta`, the same model the importer checks a file
against. `clean()` asks it, so a field's error in the form is the importer's own message.
"""

import re

from django import forms
from django.utils.text import slugify
from pydantic import ValidationError

from ingest import schemas, yamledit

SLUG = re.compile(schemas.SLUG_PATTERN)
# The order of the keys in a page's front matter, when the form writes it.
ORDER = list(schemas.DocMeta.model_fields)

# Which fields each kind of page is asked for. Whatever else a file's front matter holds
# is kept as it is.
SHOWN = {
    "lecture": ["title", "number", "date", "topics", "summary", "takeaways", "sources", "status"],
    "lab": [
        "title",
        "number",
        "date",
        "due",
        "topics",
        "summary",
        "takeaways",
        "sources",
        "status",
    ],
    "homework": [
        "title",
        "number",
        "date",
        "due",
        "topics",
        "summary",
        "takeaways",
        "sources",
        "status",
    ],
    "guide": ["title", "number", "topics", "summary", "takeaways", "sources", "status"],
    "sheet": ["title", "summary"],
}


class Rows(forms.Widget):
    """A list of short texts, one box each, with a button to add a box and one to remove
    it. The boxes all carry the field's name, so the browser sends them as one list."""

    template_name = "editor/widgets/rows.html"

    def __init__(self, attrs=None, *, add="Add a line"):
        super().__init__(attrs)
        self.add = add

    def format_value(self, value):
        return [str(item) for item in value or []]

    def value_from_datadict(self, data, files, name):
        # A QueryDict (request.POST) keeps every value sent under one name; a plain
        # dict, as a test may pass, has just the one.
        if hasattr(data, "getlist"):
            return data.getlist(name)
        return data.get(name) or []

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["add"] = self.add
        return context


class RowsField(forms.Field):
    """A list of non-empty lines."""

    widget = Rows

    def __init__(self, *, add="Add a line", **kwargs):
        kwargs.setdefault("widget", Rows(add=add))
        super().__init__(**kwargs)

    def to_python(self, value):
        return [text for text in (yamledit.clean(item) for item in value or []) if text]


def _date():
    # type="date" gives the browser's own date picker, which sends and expects 2026-10-13
    # whatever the person's language shows it as.
    return forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )


class PageForm(forms.Form):
    """A Markdown page: its front matter, field by field, and its text."""

    title = forms.CharField(max_length=200)
    number = forms.IntegerField(required=False, min_value=0, max_value=999)
    # After `number`: fields are cleaned in the order they are declared, and an address
    # left empty is made from the title and the number.
    name = forms.CharField(
        required=False,
        max_length=64,
        label="Address",
        help_text="The page's file name, and the end of its address on the site: lowercase "
        "letters, digits and hyphens. It cannot be changed afterwards.",
    )
    date = _date()
    due = _date()
    topics = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    summary = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    takeaways = RowsField(required=False, add="Add a takeaway")
    sources = RowsField(required=False, add="Add a source")
    status = forms.ChoiceField(
        choices=[("complete", "Complete"), ("draft", "Draft: shown with a label")]
    )
    # strip=False: the text is the author's, to the last space; ingest.yamledit tidies it.
    body = forms.CharField(required=False, strip=False, widget=forms.Textarea(attrs={"rows": 24}))
    seen = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, kind, page, topics, taken=(), **kwargs):
        """`page` is the file the form is about (editor.source.Page), `topics` the
        course's (id, title) pairs, `taken` the names pages of this kind already have."""
        # `initial` is what an unbound form shows (a GET); a form given data ignores it.
        kwargs.setdefault("initial", self.initial_from(page))
        super().__init__(*args, **kwargs)
        self.kind, self.page, self.taken = kind, page, set(taken)
        self.fields["topics"].choices = topics
        self.fields["date"].label = "Held on"
        self.fields["number"].help_text = "Its place in the list."
        # A form's fields are a plain dict copied for each instance, so one that does not
        # apply here is simply taken out: it is then neither drawn nor read.
        shown = {*SHOWN[kind], "body", "seen", *([] if page.exists else ["name"])}
        for name in set(self.fields) - shown:
            del self.fields[name]

    @staticmethod
    def initial_from(page):
        initial = {key: value for key, value in page.meta.items() if key in ORDER}
        initial.setdefault("status", "complete")
        if isinstance(initial.get("summary"), str):
            initial["summary"] = yamledit.clean(initial["summary"])
        return {**initial, "body": page.body, "seen": page.seen}

    def clean_name(self):
        name = self.cleaned_data["name"].strip().lower()
        if not name:
            name = suggest_name(self.cleaned_data.get("title", ""), self.cleaned_data.get("number"))
        if not SLUG.match(name):
            raise forms.ValidationError(
                "Use lowercase letters, digits and hyphens only, e.g. 05-fisher."
            )
        if name in self.taken:
            raise forms.ValidationError("A page with this address exists already.")
        return name

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        self.meta = self._meta(data)
        try:
            schemas.DocMeta.model_validate(self.meta)
        except ValidationError as failure:
            for error in failure.errors():
                field = error["loc"][0] if error["loc"] else None
                message, hint = schemas.explain(error)
                text = f"{message}. {hint}" if hint else message
                # add_error(None, …) files it under the form as a whole.
                self.add_error(field if field in self.fields else None, text)
        return data

    def _meta(self, data):
        """The page's front matter as it will be written: the file's own, with what the
        form shows put over it."""
        meta = dict(self.page.meta)
        for key in SHOWN[self.kind]:
            value = data.get(key)
            if value in (None, "", []):
                meta.pop(key, None)
            else:
                meta[key] = value
        # The order of ticked boxes is the order of the boxes. A file that lists the same
        # topics in another order has not been changed by that.
        before = self.page.meta.get("topics")
        if isinstance(before, list) and set(before) == set(meta.get("topics", [])):
            meta["topics"] = before
        # What is the default is not written, unless the file already says it.
        if meta.get("status") == "complete" and "status" not in self.page.meta:
            del meta["status"]
        ordered = {key: meta[key] for key in ORDER if key in meta}
        return yamledit.tidy({**ordered, **{k: v for k, v in meta.items() if k not in ordered}})


def suggest_name(title, number=None):
    """An address made from a title: "05-fisher-s-criterion"."""
    stem = slugify(title).replace("_", "-").strip("-")[:56]
    stem = re.sub(r"-{2,}", "-", stem)
    if number is not None and stem:
        return f"{int(number):02d}-{stem}"
    return stem


class FileForm(forms.Form):
    """Any text file of the course, as it is: the fallback for what has no form."""

    text = forms.CharField(required=False, strip=False, widget=forms.Textarea(attrs={"rows": 30}))
    seen = forms.CharField(required=False, widget=forms.HiddenInput)
