"""The editor's pages for files that are one form each: an exam's page (assessment.yaml),
a memory palace, and the course's own file.

Decks and problem sets are a few settings and a long list, edited one item at a time
(editor/shelves.py). These are the other kind: everything in the file belongs together
and fits one form, lists included (a palace's stops, an exam's plan with its phases and
tasks). The form is made from the file's schema all the same (editor/schema.py), and a
save still writes only the keys that changed (editor.items.save_keys).
"""

import re
from dataclasses import dataclass

from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from ingest import schemas

from . import items, schema
from .items import SHELVES, Place
from .shelves import RESERVED, _course_refs, _save, _seen
from .views import GONE, _open

SLUG = re.compile(schemas.SLUG_PATTERN)


@dataclass(frozen=True)
class Sheet:
    key: str  # its name in the editor's addresses
    title: str
    noun: str
    one: str  # what it holds, for a count: "stop", "paper"
    model: type
    active: str  # the entry of the course's navigation it belongs under
    folder: str = ""  # shown under a row's title, like a shelf's


SHEETS = {
    "exams": Sheet(
        "exams", "Exams", "exam", "paper", schemas.AssessmentFile, "exams", "content/exams"
    ),
    "palaces": Sheet(
        "palaces", "Memory palaces", "palace", "stop", schemas.PalaceFile, "memory",
        "content/memory/palaces",
    ),
}  # fmt: skip
COURSE = Place("course.yaml")
# In course.yaml: what the people who write the course decide, and what the owner does.
THEIRS = ("description", "topics")
OWNERS = ("title", "code", "year", "series", "semester", "status", "enabled", "has_educator",
          "hue", "order")  # fmt: skip


def _place(course, sheet, name):
    if sheet.key == "exams":
        return items.exam_place(course, name)
    return items.place(course, _PALACES, name)


# A palace is found the way a deck is: a file in a folder.
_PALACES = items.Shelf(
    "palaces", "Memory palaces", "palace", "stop", schemas.PalaceFile, "loci", schemas.LocusSpec,
    folder="content/memory/palaces",
)  # fmt: skip


def _listed(course, sheet):
    """The files of a sheet that exist: (name, its data)."""
    if sheet.key == "palaces":
        return items.containers(course, _PALACES)
    names = {name.split("/")[2] for name in items.files_under(course, "content/exams")}
    found = [(name, items.read(course, items.exam_place(course, name))) for name in sorted(names)]
    return [(name, data) for name, data in found if data is not None]


def _public(course, sheet, name):
    if sheet.key == "exams":
        return reverse("catalog:assessment", args=[course.slug, name])
    return reverse("catalog:palace", args=[course.slug, name])


def _one(course, sheet, name):
    """Where a file of a sheet is looked at in the editor."""
    if sheet.key == "exams":
        return reverse("editor:exam", args=[course.slug, name])
    return reverse("editor:palaces_one", args=[course.slug, name])


def index(request, course, sheet):
    """The exams of a course, or its memory palaces."""
    sheet = SHEETS[sheet]
    course, context = _open(request, course, sheet.active)
    rows = []
    for name, data in _listed(course, sheet):
        if sheet.key == "exams":
            count = len(items.containers(course, SHELVES["papers"], name))
        else:
            count = len(data.get("loci") or [])
        rows.append(
            {
                "name": name,
                "title": data.get("title") or name,
                "count": count,
                "url": _one(course, sheet, name),
            }
        )
    context.update(
        shelf=sheet,
        rows=rows,
        new=reverse(f"editor:{sheet.key}_new", args=[course.slug]),
        removed=request.GET.get("removed"),
    )
    return render(request, "editor/shelf.html", context)


def exam(request, course, exam):
    """One exam: its own page, and its papers."""
    course, context = _open(request, course, "exams")
    data = items.read(course, items.exam_place(course, exam))
    if data is None:
        raise Http404
    papers = SHELVES["papers"]
    rows = [
        {
            "name": name,
            "title": paper.get("title") or name,
            "kind": paper.get("kind", ""),
            "count": len(paper.get("questions") or []),
            "url": reverse("editor:papers_one", args=[course.slug, exam, name]),
        }
        for name, paper in items.containers(course, papers, exam)
    ]
    context.update(
        exam=exam,
        about=data,
        rows=rows,
        about_url=reverse("editor:exams_one", args=[course.slug, exam]),
        remove_url=reverse("editor:exams_remove", args=[course.slug, exam]),
        new_paper=reverse("editor:papers_new", args=[course.slug, exam]),
        public=reverse("catalog:assessment", args=[course.slug, exam]),
        saved=request.GET.get("saved"),
        removed=request.GET.get("removed"),
    )
    return render(request, "editor/exam.html", context)


def _whole_form(request, course, context, *, where, nodes, keys, model, done, same, noun, **page):
    """The form for some keys of one file: show it, or check and save it. `model` is the
    schema of the whole file, which is what the form's keys are checked as part of."""
    data = items.read(course, where)
    current = items.part(data, keys) if data is not None else {}
    shown, errors, seen = current, {}, items.fingerprint(current) if data is not None else ""
    address, address_errors = page.pop("address", None), []
    if request.method == "POST":
        shown = schema.parse(nodes, request.POST)
        rest = {key: value for key, value in (data or {}).items() if key not in keys}
        errors = schema.validate(model, {**rest, **shown})
        errors = {place: found for place, found in errors.items() if not place or place[0] in keys}
        seen = _seen(request)
        if address is not None:
            address["value"] = request.POST.get("address", "").strip().lower()
            if not SLUG.match(address["value"]) or len(address["value"]) > 64:
                address_errors = ["Use lowercase letters, digits and hyphens only."]
            elif address["value"] in address["taken"]:
                address_errors = ["This address is taken already."]
            else:
                where, done = address["where"](address["value"]), address["done"](address["value"])
        if not errors and not address_errors:
            answer = _save(
                request,
                course,
                context,
                lambda: items.save_keys(
                    request.user, course, where, keys, shown, seen, model=model, what=noun
                ),
                done,
                same=same,
                noun=noun,
            )
            if answer is not None:
                return answer
            if context["refused"]["kind"] == "conflict":
                now = items.read(course, where)
                context["refused"]["now"] = (
                    items.fingerprint(items.part(now, keys)) if now is not None else GONE
                )
    if address is not None:
        page["address"] = {"value": address.get("value", ""), "errors": address_errors}
    context.update(
        sections=[{"legend": "", "fields": schema.bind(nodes, shown, errors)}],
        problems=[*schema.general(errors), *schema.unplaced(nodes, errors)],
        seen=seen,
        file=where.name,
        noun=noun,
        **page,
    )
    return render(request, "editor/item.html", context)


def form(request, course, sheet, name=None):
    """An exam's page or a palace, as one form. Without a name: a new one."""
    sheet = SHEETS[sheet]
    course, context = _open(request, course, sheet.active)
    new = name is None
    where = _place(course, sheet, name or "new")
    data = items.read(course, where)
    if not new and data is None:
        raise Http404
    home = reverse(f"editor:{sheet.key}", args=[course.slug])
    back = home if new else _one(course, sheet, name)
    nodes = schema.describe(sheet.model, _course_refs(course))
    keys = items.keys_of(sheet.model)
    address = None
    if new:
        address = {
            "taken": {found for found, _data in _listed(course, sheet)} | RESERVED | {"assets"},
            "where": lambda value: _place(course, sheet, value),
            "done": lambda value: f"{_one(course, sheet, value)}?saved=new",
        }
    return _whole_form(
        request,
        course,
        context,
        where=where,
        nodes=nodes,
        keys=keys,
        model=sheet.model,
        done=f"{back}?saved=1",
        same=f"{back}?saved=same",
        noun=sheet.noun,
        address=address,
        heading=f"New {sheet.noun}" if new else (data.get("title") or name),
        overline=sheet.title if new else f"{sheet.noun.capitalize()} · {name}",
        crumbs=[(sheet.title, home), *([] if new or sheet.key != "exams" else [(name, back)])],
        here=request.path,
        cancel=back,
        new=new,
        public="" if new else _public(course, sheet, name),
        remove=None if new else reverse(f"editor:{sheet.key}_remove", args=[course.slug, name]),
        raw="" if new else f"{reverse('editor:file', args=[course.slug])}?name={where.name}",
        saved=None,
        note=request.GET.get("saved"),
    )


def remove(request, course, sheet, name):
    """Remove a palace, or an exam with all its papers."""
    sheet = SHEETS[sheet]
    course, context = _open(request, course, sheet.active)
    where = _place(course, sheet, name)
    data = items.read(course, where)
    if data is None:
        raise Http404
    home = reverse(f"editor:{sheet.key}", args=[course.slug])
    names, ids = [where.name], []
    if sheet.key == "exams":
        folder = f"content/exams/{name}"
        names += [found for found in items.files_under(course, folder) if found != where.name]
        for _paper, paper in items.containers(course, SHELVES["papers"], name):
            ids += items.ids_in(paper, SHELVES["papers"])
    if request.method == "POST":
        answer = _save(
            request,
            course,
            context,
            lambda: items.remove_files(request.user, course, names, _seen(request)),
            f"{home}?removed=1",
            same=home,
            noun=sheet.noun,
        )
        if answer is not None:
            return answer
    context.update(
        heading=data.get("title") or name,
        what=sheet.noun,
        ids=ids,
        seen=items.fingerprint(data),
        cancel=_one(course, sheet, name),
        crumbs=[(sheet.title, home)],
    )
    return render(request, "editor/remove.html", context)


def _course_form(request, course, keys, *, heading, overline, done):
    course, context = _open(request, course)
    nodes = schema.describe(schemas.CourseFile, _course_refs(course), only=keys)
    back = reverse("editor:overview", args=[course.slug])
    return _whole_form(
        request,
        course,
        context,
        where=COURSE,
        nodes=nodes,
        keys=list(keys),
        model=schemas.CourseFile,
        done=f"{back}?saved={done}",
        same=f"{back}?saved=same",
        noun="course",
        heading=heading,
        overline=overline,
        crumbs=[],
        here=request.path,
        cancel=back,
        raw=f"{reverse('editor:file', args=[course.slug])}?name=course.yaml",
    )


def about(request, course):
    """What the people who write a course decide about it: its description and topics."""
    return _course_form(
        request,
        course,
        THEIRS,
        heading="About this course",
        overline="The description and the topics",
        done="about",
    )


def course_settings(request, course):
    """What the owner of the site decides about a course. Staff only: to anyone else,
    educators of the course included, there is no such page."""
    if not request.user.is_staff:
        raise Http404
    return _course_form(
        request,
        course,
        OWNERS,
        heading="Settings of this course",
        overline="For the owner of the site",
        done="settings",
    )
