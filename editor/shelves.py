"""The editor's pages for content kept in YAML files: decks and their cards, problem sets
and papers with their questions, the formula sheet, the glossary, the hooks.

They are one set of views for all of them. Which kind a request is about comes with the
URL (`shelf`, a key of editor.items.SHELVES; editor/urls.py passes it), and everything a
view needs to know about that kind is in its `Shelf`: the file, the key of the list, the
schema of an item. The form itself is made from that schema (editor/schema.py).

    index       the decks there are               container   one deck: its cards
    create      a new deck, with its first card   settings    a deck's title, topic…
    item        one card: change it, or a new one
    remove_item, remove_container                 asked first (GET), done on POST
"""

import re

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from catalog.models import Document
from desk import handins
from ingest import schemas

from . import items, schema
from .items import SHELVES
from .views import GONE, _open, _refusal, _tell

SLUG = re.compile(schemas.SLUG_PATTERN)
RESERVED = {"new", "settings", "remove", "papers", "about"}  # words the addresses use
# Which entry of the course's navigation an editor page belongs under.
ACTIVE = {"papers": "exams", "hooks": "memory"}


def _course_refs(course):
    """What a form may point to in this course, read from the database: the course on
    the site passed validation, so its rows are its files."""
    lectures = list(
        course.documents.filter(kind=Document.Kind.LECTURE).values_list("slug", "number", "title")
    )
    return schema.Course(
        topics=list(course.topics.values_list("key", "title")),
        lectures=[(slug, title) for slug, _number, title in lectures],
        numbers=[(n, f"{n} · {title}") for _slug, n, title in lectures if n is not None],
        exams=list(course.assessments.values_list("slug", "title")),
    )


def _url(course, shelf, what="", *args, exam=""):
    lead = [course.slug, exam] if shelf.key == "papers" else [course.slug]
    return reverse(f"editor:{shelf.key}{'_' + what if what else ''}", args=[*lead, *args])


def _home(course, shelf, exam=""):
    """Where the list of a shelf's containers is."""
    if shelf.key == "papers":
        return reverse("editor:exam", args=[course.slug, exam])
    return _url(course, shelf)


def _public(course, shelf, name="", exam=""):
    if shelf.key == "papers":
        return reverse(shelf.public, args=[course.slug, exam, name])
    if shelf.folder:
        return reverse(shelf.public, args=[course.slug, name])
    return reverse(shelf.public, args=[course.slug])


def _begin(request, course, shelf, exam=""):
    shelf = SHELVES[shelf]
    course, context = _open(request, course, ACTIVE.get(shelf.key, shelf.key))
    # A paper belongs to an exam, and an exam is a folder with an assessment.yaml in it.
    if shelf.key == "papers" and items.read(course, items.exam_place(course, exam)) is None:
        raise Http404
    context.update(shelf=shelf, exam=exam, home=_home(course, shelf, exam))
    return shelf, course, context


def _found(course, shelf, name, exam):
    """The place of a container and what is in it; (place, None, None) if it is not there."""
    try:
        where = items.place(course, shelf, name, exam)
    except ValueError:
        raise Http404 from None
    data = items.read(course, where)
    container = items.at(data, where.path) if data is not None else None
    return where, data, container if isinstance(container, dict) else None


def _seen(request):
    """The fingerprint a save is made from: the one the form was filled from, or, after
    a conflict and "Save mine over it", the one that is there now."""
    over = request.POST.get("over")
    if over is None:
        return request.POST.get("seen", "")
    return "" if over == GONE else over


def _name_of(entry, shelf):
    """What an item is called in a list."""
    for key in (shelf.label, "title", "q", "cloze", "term", "name", "id"):
        if entry.get(key):
            return str(entry[key])
    return ""


def _title(container, shelf, name):
    if shelf.single:
        return shelf.title
    return (container or {}).get("title") or name


# ---- lists -----------------------------------------------------------------------------------


def index(request, course, shelf, exam=""):
    """The containers of a shelf: the decks, the problem sets, the sections of the sheet."""
    shelf, course, context = _begin(request, course, shelf, exam)
    if shelf.single:
        return _container_page(request, course, shelf, context)
    rows = [
        {
            "name": name,
            "title": _title(data, shelf, name),
            "count": len(data.get(shelf.list_key) or []),
            "url": _url(course, shelf, "one", name, exam=exam),
        }
        for name, data in items.containers(course, shelf, exam)
    ]
    context.update(rows=rows, new=_url(course, shelf, "new", exam=exam))
    context.update(removed=request.GET.get("removed"))
    return render(request, "editor/shelf.html", context)


def container(request, course, shelf, name="", exam=""):
    shelf, course, context = _begin(request, course, shelf, exam)
    return _container_page(request, course, shelf, context, str(name), exam)


def _container_page(request, course, shelf, context, name="", exam=""):
    """One container: its settings in a line, and its items."""
    _where, _data, found = _found(course, shelf, name, exam)
    if found is None and not shelf.single:
        raise Http404
    found = found or {}
    lead = [name] if not shelf.single else []
    entries = [entry for entry in found.get(shelf.list_key) or [] if isinstance(entry, dict)]
    rows = [
        {
            "id": entry.get("id"),
            "name": _name_of(entry, shelf),
            "edit": _url(course, shelf, "item", *lead, entry.get("id"), exam=exam),
            "remove": _url(course, shelf, "item_remove", *lead, entry.get("id"), exam=exam),
        }
        for entry in entries
        if entry.get("id")
    ]
    context.update(
        name=name,
        title=_title(found, shelf, name),
        settings=items.settings_of(found, shelf) if found else {},
        rows=rows,
        add=_url(course, shelf, "item_new", *lead, exam=exam),
        saved=request.GET.get("saved"),
        removed=request.GET.get("removed"),
        public=_public(course, shelf, name, exam) if found else "",
    )
    if not shelf.single:
        context.update(
            settings_url=_url(course, shelf, "settings", name, exam=exam)
            if shelf.has_settings
            else "",
            remove_url=_url(course, shelf, "remove", name, exam=exam),
        )
    return render(request, "editor/container.html", context)


# ---- forms -----------------------------------------------------------------------------------


def _form(request, context, **more):
    """Draw the one form page there is. `sections` is a list of {"legend", "fields"}."""
    context.update(more)
    return render(request, "editor/item.html", context)


def _save(request, course, context, save, done, *, same, noun):
    """Run a save and answer: a redirect when it worked (or changed nothing), else None
    with why it was refused put into the page's context."""
    try:
        step, fresh = save()
    except handins.Refused as error:
        if error.kind == "nothing":
            return redirect(same)
        context["refused"] = _refusal(error, noun)
        return None
    _tell(request, course, step, fresh)
    return redirect(done)


def item(request, course, shelf, name="", item=None, exam=""):
    """The form for one item of a container: a card of a deck, a term of the glossary.
    Without `item` it is the form for a new one."""
    shelf, course, context = _begin(request, course, shelf, exam)
    name = str(name)
    where, _data, found = _found(course, shelf, name, exam)
    new = item is None
    if found is None and not (new and shelf.single):
        raise Http404
    entries = (found or {}).get(shelf.list_key) or []
    position = None if new else items.position_of(entries, item)
    if not new and position is None:
        raise Http404
    current = {} if new else entries[position]

    nodes = schema.describe(shelf.item_model, _course_refs(course))
    for node in nodes:
        # An id is permanent: people's progress on the item is stored under it.
        node.fixed = node.key == "id" and not new
    lead = [name] if not shelf.single else []
    back = _url(course, shelf, "one", name, exam=exam) if lead else _url(course, shelf)
    here = _url(
        course, shelf, "item_new" if new else "item", *lead, *([] if new else [item]), exam=exam
    )
    data, errors, chosen = current, {}, {}
    seen = items.fingerprint(current) if not new else ""
    if request.method == "POST":
        data = schema.parse(nodes, request.POST)
        if not new:
            data = {"id": current["id"], **data}
        errors = schema.validate(shelf.item_model, data)
        if new and str(data.get("id", "")) in RESERVED:
            errors.setdefault(("id",), []).append("This word is used by the editor's addresses.")
        chosen = schema.picked(nodes, request.POST)
        seen = _seen(request)
        if not errors:
            if not new and items.same(shelf.item_model, current, data):
                return redirect(f"{back}?saved=same")
            again = _url(course, shelf, "item_new", *lead, exam=exam)
            done = (
                f"{again}?saved={data['id']}"
                if "again" in request.POST
                else _public(course, shelf, name, exam)
                if "view" in request.POST
                else f"{back}?saved={data['id']}"
            )
            answer = _save(
                request,
                course,
                context,
                lambda: items.save_item(
                    request.user, course, shelf, where, None if new else item, data, seen
                ),
                done,
                same=f"{back}?saved=same",
                noun=shelf.one,
            )
            if answer is not None:
                return answer
            if context["refused"]["kind"] == "conflict":
                now = items.read(course, where)
                there = items.at(now, where.path) if now else None
                at = items.position_of((there or {}).get(shelf.list_key), item) if not new else None
                fresh = there[shelf.list_key][at] if at is not None else None
                context["refused"]["now"] = items.fingerprint(fresh) or GONE
    fields = schema.bind(nodes, data, errors, chosen=chosen)
    return _form(
        request,
        context,
        heading=f"New {shelf.one}" if new else f"{shelf.one.capitalize()} {current.get('id')}",
        overline=_title(found, shelf, name),
        crumbs=[
            (shelf.title, _home(course, shelf, exam)),
            *([(_title(found, shelf, name), back)] if lead else []),
        ],
        sections=[{"legend": "", "fields": fields}],
        problems=[*schema.general(errors), *schema.unplaced(nodes, errors)],
        seen=seen,
        here=here,
        cancel=back,
        file=where.name,
        new=new,
        again=new,
        public=_public(course, shelf, name, exam),
        remove=None if new else _url(course, shelf, "item_remove", *lead, item, exam=exam),
        raw=f"{reverse('editor:file', args=[course.slug])}?name={where.name}",
        saved=request.GET.get("saved"),
        noun=shelf.one,
    )


def settings(request, course, shelf, name, exam=""):
    """A container's own settings: a deck's title, topic and lecture."""
    shelf, course, context = _begin(request, course, shelf, exam)
    name = str(name)
    where, _data, found = _found(course, shelf, name, exam)
    if found is None or not shelf.has_settings:
        raise Http404
    nodes = schema.describe(shelf.model, _course_refs(course), without=(shelf.list_key,))
    current = items.settings_of(found, shelf)
    back = _url(course, shelf, "one", name, exam=exam)
    data, errors, seen = current, {}, items.fingerprint(current)
    if request.method == "POST":
        data = schema.parse(nodes, request.POST)
        # Checked as part of the whole container, which is what the schema describes.
        errors = schema.validate(shelf.model, {**data, shelf.list_key: found[shelf.list_key]})
        errors = {
            place: found_ for place, found_ in errors.items() if place[:1] != (shelf.list_key,)
        }
        seen = _seen(request)
        if not errors:
            answer = _save(
                request,
                course,
                context,
                lambda: items.save_settings(request.user, course, shelf, where, data, seen),
                f"{back}?saved=settings",
                same=f"{back}?saved=same",
                noun=shelf.noun,
            )
            if answer is not None:
                return answer
            if context["refused"]["kind"] == "conflict":
                _w, _d, now = _found(course, shelf, name, exam)
                context["refused"]["now"] = (
                    items.fingerprint(items.settings_of(now, shelf)) if now else GONE
                )
    return _form(
        request,
        context,
        heading=f"Settings of this {shelf.noun}",
        overline=_title(found, shelf, name),
        crumbs=[(shelf.title, _home(course, shelf, exam)), (_title(found, shelf, name), back)],
        sections=[{"legend": "", "fields": schema.bind(nodes, data, errors)}],
        problems=[*schema.general(errors), *schema.unplaced(nodes, errors)],
        seen=seen,
        here=_url(course, shelf, "settings", name, exam=exam),
        cancel=back,
        file=where.name,
        raw=f"{reverse('editor:file', args=[course.slug])}?name={where.name}",
        noun=shelf.noun,
    )


def create(request, course, shelf, exam=""):
    """A new container with its first item: a deck cannot be empty, so the form asks for
    both at once."""
    shelf, course, context = _begin(request, course, shelf, exam)
    if shelf.single:
        raise Http404
    refs = _course_refs(course)
    head = schema.describe(shelf.model, refs, without=(shelf.list_key,))
    first = schema.describe(shelf.item_model, refs)
    settings_, entry, errors, chosen, address, address_errors = {}, {}, {}, {}, "", []
    home = _home(course, shelf, exam)
    if request.method == "POST":
        settings_ = schema.parse(head, request.POST)
        entry = schema.parse(first, request.POST, "first-")
        chosen = schema.picked(first, request.POST, "first-")
        errors = schema.validate(shelf.model, {**settings_, shelf.list_key: [entry]})
        if shelf.folder:
            address = request.POST.get("address", "").strip().lower()
            taken = {name for name, _data in items.containers(course, shelf, exam)}
            if not SLUG.match(address) or len(address) > 64:
                address_errors = [
                    "Use lowercase letters, digits and hyphens only, e.g. ch5-fisher."
                ]
            elif address in taken | RESERVED | {"assessment"}:
                address_errors = ["This address is taken already."]
            where = items.place(course, shelf, address or "new", exam)
        else:
            where = items.Place(shelf.file)
        if not errors and not address_errors:
            if shelf.folder:
                done = f"{_url(course, shelf, 'one', address, exam=exam)}?saved=new"
            else:
                done = f"{home}?saved=new"
            answer = _save(
                request,
                course,
                context,
                lambda: items.create(request.user, course, shelf, where, settings_, entry),
                done,
                same=home,
                noun=shelf.noun,
            )
            if answer is not None:
                return answer
    in_first = {
        place[2:]: found for place, found in errors.items() if place[:2] == (shelf.list_key, 0)
    }
    in_head = {place: found for place, found in errors.items() if place[:1] != (shelf.list_key,)}
    return _form(
        request,
        context,
        heading=f"New {shelf.noun}",
        overline=shelf.title,
        crumbs=[(shelf.title, home)],
        address={"value": address, "errors": address_errors} if shelf.folder else None,
        sections=[
            {"legend": f"The {shelf.noun}", "fields": schema.bind(head, settings_, in_head)},
            {
                "legend": f"Its first {shelf.one}",
                "fields": schema.bind(first, entry, in_first, "first-", chosen=chosen),
            },
        ],
        problems=[*schema.general(errors), *schema.general(in_first)],
        seen="",
        here=_url(course, shelf, "new", exam=exam),
        cancel=home,
        file=f"{shelf.folder or 'content/reference'}/new.yaml",
        new=True,
        noun=shelf.noun,
    )


# ---- removing --------------------------------------------------------------------------------


def remove_item(request, course, shelf, item, name="", exam=""):
    shelf, course, context = _begin(request, course, shelf, exam)
    name = str(name)
    where, _data, found = _found(course, shelf, name, exam)
    entries = (found or {}).get(shelf.list_key) or []
    position = items.position_of(entries, item)
    if position is None:
        raise Http404
    current = entries[position]
    lead = [name] if not shelf.single else []
    back = _url(course, shelf, "one", name, exam=exam) if lead else _url(course, shelf)
    if request.method == "POST":
        answer = _save(
            request,
            course,
            context,
            lambda: items.remove_item(request.user, course, shelf, where, item, _seen(request)),
            f"{back}?removed={item}",
            same=back,
            noun=shelf.one,
        )
        if answer is not None:
            return answer
    context.update(
        heading=_name_of(current, shelf),
        what=shelf.one,
        ids=[item],
        only=len(entries) == 1,
        container_noun=shelf.noun,
        seen=items.fingerprint(current),
        cancel=_url(course, shelf, "item", *lead, item, exam=exam),
        crumbs=[
            (shelf.title, _home(course, shelf, exam)),
            *([(_title(found, shelf, name), back)] if lead else []),
        ],
    )
    return render(request, "editor/remove.html", context)


def remove_container(request, course, shelf, name, exam=""):
    shelf, course, context = _begin(request, course, shelf, exam)
    name = str(name)
    where, _data, found = _found(course, shelf, name, exam)
    if found is None or shelf.single:
        raise Http404
    home = _home(course, shelf, exam)
    if request.method == "POST":
        answer = _save(
            request,
            course,
            context,
            lambda: items.remove_container(request.user, course, shelf, where, _seen(request)),
            f"{home}?removed=1",
            same=home,
            noun=shelf.noun,
        )
        if answer is not None:
            return answer
    context.update(
        heading=_title(found, shelf, name),
        what=shelf.noun,
        ids=items.ids_in(found, shelf),
        seen=items.fingerprint(found),
        cancel=_url(course, shelf, "one", name, exam=exam),
        crumbs=[(shelf.title, home)],
    )
    return render(request, "editor/remove.html", context)
